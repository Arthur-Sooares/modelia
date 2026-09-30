"""Base de conhecimento do modo Explicar (RF4, RF8): RAG local com ChromaDB.

Os embeddings são calculados pelo Ollama (nomic-embed-text), nunca por um serviço externo.
O nomic-embed-text pede um prefixo que diz se o texto é um documento ou uma pergunta.
"""

import hashlib
import re
from pathlib import Path

import chromadb

from modelia import config, llm

TAMANHO_TRECHO = 900  # caracteres, mais ou menos um parágrafo grande
SOBREPOSICAO = 150
# Distância de cosseno máxima para um trecho contar como relevante (0 = idêntico, 2 = oposto).
# Calibrado em 30/09/2026 com um texto de teste: perguntas do assunto ficaram entre 0,23 e 0,40 e uma
# pergunta fora do assunto ficou em 0,51. Recalibrar quando a apostila real for indexada.
LIMIAR_DISTANCIA = 0.45


def ler_arquivo(caminho):
    caminho = Path(caminho)
    if caminho.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join(pagina.extract_text() or "" for pagina in PdfReader(caminho).pages)
    return caminho.read_text(encoding="utf-8", errors="replace")


def dividir(texto, tamanho=TAMANHO_TRECHO, sobreposicao=SOBREPOSICAO):
    """Junta parágrafos até perto de `tamanho` caracteres. Parágrafos enormes são cortados com sobreposição."""
    paragrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    trechos, atual = [], ""
    for paragrafo in paragrafos:
        while len(paragrafo) > tamanho:
            if atual:
                trechos.append(atual)
                atual = ""
            trechos.append(paragrafo[:tamanho])
            paragrafo = paragrafo[tamanho - sobreposicao:]
        if len(atual) + len(paragrafo) + 2 > tamanho and atual:
            trechos.append(atual)
            atual = ""
        atual = f"{atual}\n\n{paragrafo}" if atual else paragrafo
    if atual:
        trechos.append(atual)
    return trechos


class Conhecimento:
    def __init__(self, pasta=None):
        cliente = chromadb.PersistentClient(path=str(pasta or config.PASTA_INDICE))
        # embedding_function=None: nós mesmos mandamos os vetores (evita o modelo padrão do Chroma,
        # que seria baixado da internet).
        self.colecao = cliente.get_or_create_collection(
            "conhecimento", embedding_function=None, metadata={"hnsw:space": "cosine"})

    def adicionar(self, caminho, titulo, fonte=""):
        """Indexa um arquivo PDF, Markdown ou texto. Devolve quantos trechos foram criados."""
        titulo = (titulo or "").strip() or Path(caminho).stem
        texto = ler_arquivo(caminho)
        trechos = dividir(texto)
        if not trechos:
            raise ValueError("Não encontrei texto no arquivo. PDFs escaneados (só imagem) não funcionam.")
        doc_id = hashlib.sha1(f"{titulo}|{fonte}".encode()).hexdigest()[:12]
        self.remover(doc_id)  # reenviar o mesmo documento substitui a versão antiga
        vetores = llm.embeddings([f"search_document: {t}" for t in trechos])
        self.colecao.add(
            ids=[f"{doc_id}-{i}" for i in range(len(trechos))],
            documents=trechos,
            embeddings=vetores,
            metadatas=[{"doc_id": doc_id, "titulo": titulo, "fonte": fonte, "parte": i}
                       for i in range(len(trechos))],
        )
        return len(trechos)

    def listar(self):
        """Documentos indexados, com a quantidade de trechos de cada um."""
        documentos = {}
        for meta in self.colecao.get(include=["metadatas"])["metadatas"]:
            doc = documentos.setdefault(meta["doc_id"], {
                "doc_id": meta["doc_id"], "titulo": meta["titulo"], "fonte": meta["fonte"], "trechos": 0})
            doc["trechos"] += 1
        return sorted(documentos.values(), key=lambda d: d["titulo"].lower())

    def remover(self, doc_id):
        self.colecao.delete(where={"doc_id": doc_id})

    def vazia(self):
        return self.colecao.count() == 0

    def buscar(self, pergunta, k=4):
        """Trechos mais próximos da pergunta, já filtrados pelo limiar de relevância."""
        if self.vazia():
            return []
        vetor = llm.embeddings([f"search_query: {pergunta}"])[0]
        achados = self.colecao.query(query_embeddings=[vetor], n_results=k,
                                     include=["documents", "metadatas", "distances"])
        return [
            {"texto": texto, "titulo": meta["titulo"], "fonte": meta["fonte"], "distancia": round(dist, 3)}
            for texto, meta, dist in zip(achados["documents"][0], achados["metadatas"][0],
                                         achados["distances"][0], strict=True)
            if dist <= LIMIAR_DISTANCIA
        ]
