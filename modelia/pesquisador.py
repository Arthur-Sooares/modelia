"""API das telas do Pesquisador no site (pesquisador.html): Modelos, Conhecimento, Histórico,
Experimento e Instalação (RF3, RF8, RF9, RF10). Telas 8 a 12 do Figma.

Sem senha por padrão. Com MODELIA_SENHA definida, toda rota pede a senha no cabeçalho X-Senha
(a restrição é conferida no servidor).
O que demora (baixar, instalar, indexar, o experimento) roda numa thread como um "trabalho":
a página pede POST para começar e consulta GET /trabalhos a cada segundo para mostrar a barra.
Os trabalhos ficam na memória: sobrevivem a trocar de tela, não a reiniciar o Modelia.
"""

import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from modelia import config, experimento, llm, manual, preparar
from modelia.agente import MODOS
from modelia.banco import para_csv
from modelia.tarefas import TAREFAS

# Catálogo da tela Modelos: sugestões que cabem (ou quase) numa placa de 6 GB.
SUGESTOES = [
    {"nome": "qwen3.5:4b", "tamanho_gb": 3.4, "descricao": "O padrão do Modelia. Rápido e bom em português."},
    {"nome": "llama3.2:3b", "tamanho_gb": 2.0, "descricao": "Leve e rápido. Bom para o modo Explicar."},
    {"nome": "qwen2.5-coder:7b", "tamanho_gb": 4.7, "descricao": "Forte em código Python. Bom para o Demonstrar."},
    {"nome": "mistral:7b", "tamanho_gb": 4.1, "descricao": "Equilibrado, responde bem em português."},
    {"nome": "gemma4:12b-it-qat", "tamanho_gb": 8.9, "descricao": "O maior do experimento. Usa parte da RAM."},
]
LINK_DO_MANUAL = re.compile(r"(https://docs\.blender\.org/manual/[^/]+/(\d+\.\d+)/)")
ETAPAS = {"ollama": preparar.instalar_ollama, "modelos": preparar.baixar_modelos}


class Nome(BaseModel):
    nome: str


class Documento(BaseModel):
    doc_id: str


class PastaDoManual(BaseModel):
    caminho: str
    atualizar: bool = False


class Planejamento(BaseModel):
    modelos: list[str]
    tarefas: list[str]
    condicoes: list[str] = experimento.CONDICOES
    repeticoes: int = 3
    temperatura: float = 0.6
    seeds: list[int] = [1, 2, 3]


def exigir_senha(x_senha: str = Header("")):  # noqa: B008
    if config.SENHA_PESQUISADOR and x_senha != config.SENHA_PESQUISADOR:
        raise HTTPException(401, "Senha do Pesquisador incorreta.")


class Trabalhos:
    """Um trabalho de cada tipo por vez ("modelo", "documento", "instalacao", "experimento")."""

    def __init__(self):
        self.estado = {}
        self.trava = threading.Lock()

    def iniciar(self, tipo, funcao, **extra):
        """`funcao(trabalho)` é um gerador de (fração ou None, texto). `trabalho` é o dicionário do estado,
        para a função guardar dados a mais (ex.: as linhas do experimento)."""
        with self.trava:
            if self.estado.get(tipo, {}).get("ativo"):
                raise HTTPException(409, "Já tem uma tarefa dessas em andamento. Espere terminar.")
            trabalho = self.estado[tipo] = {"ativo": True, "fracao": None, "texto": "Começando…", "erro": "", **extra}

        def rodar():
            try:
                for fracao, texto in funcao(trabalho):
                    trabalho["fracao"], trabalho["texto"] = fracao, texto
            except Exception as erro:
                trabalho["erro"] = str(erro) or erro.__class__.__name__
            finally:
                trabalho["ativo"] = False

        threading.Thread(target=rodar, daemon=True).start()
        return trabalho

    def ver(self):
        with self.trava:
            return {nome: dict(t) for nome, t in self.estado.items()}


def salvar_envio(arquivo):
    """Copia o arquivo enviado pelo navegador para uma pasta temporária, com a extensão original."""
    pasta = Path(tempfile.mkdtemp(prefix="modelia_"))
    caminho = pasta / Path(arquivo.filename or "arquivo").name
    with caminho.open("wb") as destino:
        shutil.copyfileobj(arquivo.file, destino)
    return caminho


def separar_manual(documentos):
    """O manual vira um documento por página (~400): na tela ele aparece como um cartão só."""
    paginas = [d for d in documentos if d["titulo"].startswith(manual.TITULO)]
    outros = [d for d in documentos if not d["titulo"].startswith(manual.TITULO)]
    # A versão indexada vem do link das páginas (.../manual/pt/5.0/...): pode ter vindo de uma pasta 5.0.
    achado = next((LINK_DO_MANUAL.search(d["fonte"]) for d in paginas), None)
    resumo = {"na_base": bool(paginas), "paginas": len(paginas), "trechos": sum(d["trechos"] for d in paginas),
              "adicionado": max((d["adicionado"] for d in paginas), default=""),
              "versao": achado[2] if achado else manual.VERSAO, "endereco": achado[1] if achado else manual.ENDERECO,
              "embutido": manual.EMBUTIDO.exists(), "faltam": len(manual.faltando(paginas)) if paginas else 0}
    return resumo, outros


def criar_api_pesquisador(s):
    """Rotas /api/pesquisador/... sobre os serviços compartilhados `s`."""
    api = APIRouter(prefix="/api/pesquisador", dependencies=[Depends(exigir_senha)])
    trabalhos = Trabalhos()

    @api.get("/trabalhos")
    def ver_trabalhos():
        return trabalhos.ver()

    # --- Modelos (RF3, tela 8) ---

    @api.get("/modelos")
    def modelos():
        if not llm.disponivel():
            return {"ollama": False, "vram_gb": config.VRAM_GB, "vram_em_uso": 0, "instalados": [],
                    "sugestoes": SUGESTOES, "padrao": config.MODELO_PADRAO}
        instalados = [{**m, "cabe": llm.cabe_na_vram(m["tamanho_gb"]), "embeddings": "embed" in m["nome"],
                       "ferramentas": "embed" in m["nome"] or llm.usa_ferramentas(m["nome"]),
                       "padrao": m["nome"] in (config.MODELO_PADRAO, f"{config.MODELO_PADRAO}:latest")}
                      for m in llm.modelos()]
        nomes = {m["nome"] for m in instalados}
        try:
            em_uso = llm.vram_em_uso()
        except Exception:
            em_uso = 0
        return {"ollama": True, "vram_gb": config.VRAM_GB, "vram_em_uso": em_uso, "instalados": instalados,
                "sugestoes": [{**m, "cabe": llm.cabe_na_vram(m["tamanho_gb"])} for m in SUGESTOES
                              if m["nome"] not in nomes],
                "padrao": config.MODELO_PADRAO}

    @api.post("/modelos/baixar")
    def baixar_modelo(n: Nome):
        nome = n.nome.strip()
        if not nome:
            raise HTTPException(400, "Digite o nome do modelo, por exemplo qwen3.5:4b.")
        trabalhos.iniciar("modelo", lambda _: llm.baixar(nome), nome=nome)
        return {"ok": True}

    @api.post("/modelos/apagar")
    def apagar_modelo(n: Nome):
        if n.nome == config.MODELO_PADRAO:
            raise HTTPException(400, "Esse é o modelo padrão do chat. Escolha outro como padrão antes de apagar.")
        llm.apagar(n.nome)
        return {"ok": True}

    @api.post("/modelos/padrao")
    def usar_como_padrao(n: Nome):
        if "embed" in n.nome:
            raise HTTPException(400, "Modelos de embeddings não conversam: escolha um modelo de chat.")
        if not llm.usa_ferramentas(n.nome):
            raise HTTPException(400, f"{n.nome} não sabe usar ferramentas (tools), e o Modelia precisa delas "
                                     "para mexer no Blender. Use o qwen3.5:4b.")
        config.salvar_modelo_padrao(n.nome)
        return {"ok": True}

    @api.post("/ollama/ligar")
    def ligar_ollama():
        if not preparar.ligar_ollama():
            raise HTTPException(503, "Não consegui ligar o Ollama. Ele está instalado? Veja a tela Instalação.")
        return {"ok": True}

    # --- Conhecimento (RF8, tela 9) ---

    @api.get("/conhecimento")
    def conhecimento():
        resumo, documentos = separar_manual(s.conhecimento.listar())
        return {"manual": resumo, "documentos": documentos}

    @api.post("/conhecimento")
    def adicionar_documento(arquivo: UploadFile = File(...), titulo: str = Form(""), fonte: str = Form("")):  # noqa: B008
        if Path(arquivo.filename or "").suffix.lower() not in (".pdf", ".md", ".txt"):
            raise HTTPException(400, "Use um arquivo PDF, Markdown (.md) ou texto (.txt).")
        caminho = salvar_envio(arquivo)
        titulo = titulo.strip() or caminho.stem

        def indexar(_):
            yield None, f"Indexando “{titulo}”… dividindo em trechos e gerando embeddings."
            try:
                trechos = s.conhecimento.adicionar(caminho, titulo, fonte.strip())
            except Exception as erro:
                if "not found" in str(erro).lower():
                    raise RuntimeError(f"Falta o modelo de embeddings ({config.MODELO_EMBEDDINGS}). "
                                       "Baixe na tela Instalação.") from erro
                raise
            finally:
                shutil.rmtree(caminho.parent, ignore_errors=True)
            yield 1, f"“{titulo}” incluído com {trechos} trechos."

        trabalhos.iniciar("documento", indexar, titulo=titulo)
        return {"ok": True}

    @api.post("/conhecimento/remover")
    def remover_documento(d: Documento):
        s.conhecimento.remover(d.doc_id)
        return {"ok": True}

    @api.post("/manual/remover")
    def remover_manual():
        for d in s.conhecimento.listar():
            if d["titulo"].startswith(manual.TITULO):
                s.conhecimento.remover(d["doc_id"])
        return {"ok": True}

    @api.post("/manual")
    def preparar_manual(arquivo: UploadFile | None = File(None), atualizar: bool = Form(False)):  # noqa: B008
        """Baixa (ou usa o .zip enviado) e indexa o manual. `atualizar` tira as páginas antigas antes."""
        caminho = salvar_envio(arquivo) if arquivo else None

        def etapa(_):
            try:
                if atualizar:
                    yield None, "Tirando a versão antiga do manual…"
                    remover_manual()
                yield from preparar.preparar_manual(s.conhecimento, caminho)
            finally:
                if caminho:
                    shutil.rmtree(caminho.parent, ignore_errors=True)

        trabalhos.iniciar("instalacao", etapa, etapa="manual", sequencia=["manual"])
        return {"ok": True}

    @api.post("/manual/pasta")
    def manual_de_uma_pasta(p: PastaDoManual):
        """Indexa um manual já extraído numa pasta deste computador (o navegador não envia pastas)."""
        try:
            manual.achar_pasta(p.caminho)  # confere já, para o erro aparecer na hora
        except ValueError as erro:
            raise HTTPException(400, str(erro)) from erro

        def etapa(_):
            if p.atualizar:
                yield None, "Tirando a versão antiga do manual…"
                remover_manual()
            yield from preparar.preparar_manual(s.conhecimento, pasta_propria=p.caminho)

        trabalhos.iniciar("instalacao", etapa, etapa="manual", sequencia=["manual"])
        return {"ok": True}

    # --- Histórico (RF9, tela 10) ---

    def filtrar(estudante, modo, desde, ate):
        for data in (desde, ate):
            if data:
                try:
                    datetime.strptime(data, "%Y-%m-%d")
                except ValueError as erro:
                    raise HTTPException(400, "Use datas no formato AAAA-MM-DD.") from erro
        return s.banco.historico(estudante, modo, desde, ate)

    @api.get("/historico")
    def historico(estudante: str = "", modo: str = "", desde: str = "", ate: str = ""):
        linhas = filtrar(estudante, modo, desde, ate)
        return {
            "linhas": linhas[:500],  # a tabela mostra as mais recentes; o CSV leva tudo
            "total": len(linhas),
            "por_modo": {m: sum(1 for linha in linhas if linha["modo"] == m) for m in MODOS},
            "erros": sum(linha["erros"] for linha in linhas),
            "estudantes": s.banco.estudantes(),
        }

    @api.get("/historico.csv")
    def historico_csv(estudante: str = "", modo: str = "", desde: str = "", ate: str = ""):
        linhas = filtrar(estudante, modo, desde, ate)
        if not linhas:
            raise HTTPException(404, "Não há registros para exportar.")
        nome = f"modelia_historico_{datetime.now():%Y%m%d_%H%M%S}.csv"
        # -sig: o Excel abre os acentos certos
        return Response(para_csv(linhas).encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{nome}"'})

    # --- Experimento (RF10, tela 11) ---

    @api.get("/experimento")
    def ver_experimento():
        try:
            modelos = llm.modelos_de_chat()
        except Exception:
            modelos = []
        return {"modelos": modelos, "tarefas": [{"id": t.id, "nome": t.nome} for t in TAREFAS.values()],
                "condicoes": experimento.CONDICOES, "cena_base": config.CENA_BASE.exists(),
                "blender": s.blender.disponivel()}

    @api.post("/experimento/cena-base")
    def cena_base():
        if not s.blender.disponivel():
            raise HTTPException(503, "Abra o Blender 5.1 com o add-on MCP ligado para criar a cena base.")
        experimento.criar_cena_base(s.blender)
        return {"ok": True}

    @api.post("/experimento/iniciar")
    def iniciar_experimento(p: Planejamento):
        if not p.modelos or not p.tarefas or not p.condicoes:
            raise HTTPException(400, "Escolha pelo menos um modelo, uma tarefa e uma condição.")
        if not config.CENA_BASE.exists():
            raise HTTPException(400, "Crie a cena base antes (botão “Criar cena base”).")
        s.parar_experimento.clear()

        def rodar(trabalho):
            for feitas, total, linha, arquivo in experimento.rodar(
                    s.blender, p.modelos, p.tarefas, p.repeticoes, p.temperatura, p.seeds or [1], p.condicoes,
                    deve_parar=s.parar_experimento.is_set):
                trabalho["arquivo"] = str(arquivo)
                trabalho["linhas"] = [{c: linha.get(c, "") for c in ("condicao", "modelo", "tarefa", "repeticao",
                                                                       "nota_0a3", "avaliacao")}] + trabalho["linhas"]
                yield feitas / total, f"Execução {feitas} de {total}: {linha.get('modelo')} · {linha.get('tarefa')}"
            yield 1, "Experimento interrompido." if s.parar_experimento.is_set() else "Experimento concluído."

        trabalhos.iniciar("experimento", rodar, linhas=[], arquivo="")
        return {"ok": True}

    @api.post("/experimento/parar")
    def parar_experimento():
        s.parar_experimento.set()
        return {"ok": True}

    # --- Instalação (tela 12) ---

    @api.get("/instalacao")
    def instalacao():
        return {**preparar.situacao(s.conhecimento), "necessarios": config.MODELOS_NECESSARIOS,
                "manual_versao": manual.VERSAO, "manual_endereco": manual.ENDERECO,
                "manual_embutido": manual.EMBUTIDO.exists(), "atalho": situacao_do_atalho()}

    def situacao_do_atalho():
        try:
            caminho = preparar.caminho_do_atalho()
        except RuntimeError:
            caminho = None
        return {"possivel": caminho is not None, "existe": bool(caminho and caminho.exists())}

    @api.post("/atalho")
    def criar_atalho():
        """Atalho "Modelia" na área de trabalho: abre com dois cliques, sem terminal (tela 0 do Figma)."""
        try:
            return {"caminho": str(preparar.criar_atalho())}
        except RuntimeError as erro:
            raise HTTPException(400, str(erro)) from erro

    @api.post("/instalacao/{etapa}")
    def preparar_etapa(etapa: str):
        """Uma etapa ("ollama", "modelos", "manual") ou "tudo", em sequência."""
        if etapa not in (*ETAPAS, "manual", "tudo"):
            raise HTTPException(404, "Etapa desconhecida.")
        sequencia = [*ETAPAS, "manual"] if etapa == "tudo" else [etapa]

        def rodar(trabalho):
            for nome in sequencia:
                trabalho["etapa"] = nome
                if nome == "manual":
                    yield from preparar.preparar_manual(s.conhecimento)
                else:
                    yield from ETAPAS[nome]()
            trabalho["etapa"] = "fim"

        trabalhos.iniciar("instalacao", rodar, etapa=sequencia[0], sequencia=sequencia)
        return {"ok": True}

    return api
