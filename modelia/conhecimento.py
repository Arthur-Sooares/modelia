"""Base de conhecimento do modo Explicar (RF4, RF8): RAG local com ChromaDB.

Os embeddings são calculados pelo Ollama (config.MODELO_EMBEDDINGS), nunca por um serviço externo.
O qwen3-embedding pede uma instrução antes da pergunta; os documentos vão sem prefixo.
"""

import hashlib
import re
from pathlib import Path

import chromadb

from modelia import config, llm

TAMANHO_TRECHO = 900  # caracteres, mais ou menos um parágrafo grande
SOBREPOSICAO = 150
PREFIXO_PERGUNTA = ("Instruct: Given a question from a Blender student, retrieve manual passages that answer it\n"
                    "Query: ")
# Distância de cosseno máxima para um trecho contar como relevante (0 = idêntico, 2 = oposto).
# ponytail: calibrado em 18/10/2026 com o manual do Blender e poucas perguntas: as do assunto ficaram
# entre 0,25 e 0,48 (ex.: "chanfro nas bordas de um cubo" 0,42) e as de fora a partir de 0,42. Folgado de
# propósito: pergunta de fora que passa daqui a LLM recusa (NAO_SEI); as 5 de teste foram recusadas.
# Recalibrar com perguntas reais dos alunos.
LIMIAR_DISTANCIA = 0.5


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
        # Uma coleção por modelo de embeddings: vetores de modelos diferentes não se comparam
        # (e têm tamanhos diferentes). Trocar o modelo pede indexar o material de novo.
        nome = "conhecimento-" + re.sub(r"[^a-zA-Z0-9]+", "-", config.MODELO_EMBEDDINGS).strip("-")
        self.colecao = cliente.get_or_create_collection(
            nome, embedding_function=None, metadata={"hnsw:space": "cosine"})

    def adicionar(self, caminho, titulo, fonte=""):
        """Indexa um arquivo PDF, Markdown ou texto. Devolve quantos trechos foram criados."""
        return self.adicionar_texto(ler_arquivo(caminho), (titulo or "").strip() or Path(caminho).stem, fonte)

    def adicionar_texto(self, texto, titulo, fonte=""):
        trechos = dividir(texto)
        if not trechos:
            raise ValueError("Não encontrei texto no arquivo. PDFs escaneados (só imagem) não funcionam.")
        doc_id = hashlib.sha1(f"{titulo}|{fonte}".encode()).hexdigest()[:12]
        self.remover(doc_id)  # reenviar o mesmo documento substitui a versão antiga
        vetores = llm.embeddings(trechos)
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
        vetor = llm.embeddings([PREFIXO_PERGUNTA + pergunta])[0]
        achados = self.colecao.query(query_embeddings=[vetor], n_results=k,
                                     include=["documents", "metadatas", "distances"])
        return [
            {"texto": texto, "titulo": meta["titulo"], "fonte": meta["fonte"], "distancia": round(dist, 3)}
            for texto, meta, dist in zip(achados["documents"][0], achados["metadatas"][0],
                                         achados["distances"][0], strict=True)
            if dist <= LIMIAR_DISTANCIA
        ]
