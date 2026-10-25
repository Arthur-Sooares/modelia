"""API JSON do chat novo (site/chat.html). Usa o mesmo Agente do app em Gradio.

Cada conversa do navegador vira uma Conversa guardada na memória do servidor, com uma
sessão própria no banco (RNF10). Ainda sem login: todas as conversas são do "visitante".

A resposta do agente roda numa thread que não depende da página: se o aluno vai para outra
tela no meio da geração, ela continua, e ao voltar o chat busca a conversa (GET /conversas/...)
e retoma a resposta em andamento (GET /conversas/.../acompanhar).
"""

import io
import json
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from modelia import config, llm
from modelia.agente import MODOS, Estado
from modelia.tarefas import TAREFAS

# ponytail: identificador fixo até a tela de entrar voltar (RF1); aí vem do login.
IDENTIFICADOR = "visitante"
TITULO_MAX = 40


class Mensagem(BaseModel):
    conversa: str
    texto: str
    modo: str
    modelo: str = ""
    tarefa: str = ""


class Acao(BaseModel):
    conversa: str


class Conversa:
    """O que a tela do chat mostra, guardado no servidor. `historico` tem itens
    {"tipo": "aluno", "texto"} e {"tipo": "modelia", "modo", "etapa", "parcial", "resposta", "erro", "decisao",
    "inicio", "segundos"}: `inicio` (época, em segundos) e `segundos` alimentam o cronômetro da tela.
    `decisao` diz o que aconteceu com o código proposto: "executado", "cancelado" ou "nao_executado"."""

    def __init__(self, conversa_id, estado):
        self.id = conversa_id
        self.estado = estado
        self.titulo = ""
        self.historico = []
        self.andamento = None  # o item da resposta que ainda está sendo gerada
        self.criada = time.time()
        self.mudou = threading.Condition()

    def decidir(self, decisao, todos=False):
        """Marca o código proposto que esperava o clique (o último, ou todos os pendentes)."""
        for item in reversed(self.historico):
            if item["tipo"] == "modelia" and (item["resposta"] or {}).get("codigo") and not item["decisao"]:
                item["decisao"] = decisao
                if not todos:
                    return


def nomes_de_modelos():
    try:
        return llm.modelos_de_chat()
    except Exception:
        return []


def para_json(resposta):
    return {"texto": resposta.texto, "codigo": resposta.codigo,
            "atualizar_cena": resposta.atualizar_cena, "detalhes": resposta.detalhes, "problema": resposta.problema}


def linha(item):
    return json.dumps(item, ensure_ascii=False) + "\n"


def acompanhar(conversa, item):
    """Manda a resposta aos pedaços (NDJSON): {"etapa": o que o agente está fazendo} e {"parcial": texto até
    agora}, ..., e por último {"fim": resposta} ou {"erro": mensagem}. Pode ser chamada de novo a qualquer
    momento (ao voltar para o chat): começa do estado atual da resposta."""
    def linhas():
        visto, etapa, parcial = -1, "", ""
        while True:
            with conversa.mudou:
                conversa.mudou.wait_for(lambda v=visto: item["versao"] != v, timeout=15)
                visto, agora = item["versao"], dict(item)
            if agora["etapa"] != etapa:
                etapa = agora["etapa"]
                yield linha({"etapa": etapa})
            if agora["parcial"] != parcial:
                parcial = agora["parcial"]
                yield linha({"parcial": parcial})
            if agora["resposta"] is not None:
                yield linha({"fim": agora["resposta"]})
                return
            if agora["erro"]:
                yield linha({"erro": agora["erro"]})
                return

    return StreamingResponse(linhas(), media_type="application/x-ndjson")


def responder(conversa, funcao, *args, pedido=""):
    """Roda o agente numa thread (que continua mesmo se a página fechar) e acompanha a resposta."""
    with conversa.mudou:
        if conversa.andamento:
            raise HTTPException(409, "Espere a resposta anterior terminar.")
        if pedido:
            if not conversa.titulo:
                conversa.titulo = pedido if len(pedido) <= TITULO_MAX else pedido[:TITULO_MAX - 2] + "…"
            conversa.historico.append({"tipo": "aluno", "texto": pedido})
        item = {"tipo": "modelia", "modo": conversa.estado.modo, "etapa": "", "parcial": "", "resposta": None,
                "erro": "", "decisao": "", "inicio": time.time(), "segundos": None, "versao": 0}
        conversa.historico.append(item)
        conversa.andamento = item

    def mudar(**campos):
        with conversa.mudou:
            item.update(campos)
            item["versao"] += 1
            if item["resposta"] is not None or item["erro"]:
                conversa.andamento = None
                item["segundos"] = round(time.time() - item["inicio"], 1)  # o cronômetro da tela
            conversa.mudou.notify_all()

    def trabalhar():
        try:
            resposta = funcao(*args, ao_escrever=lambda texto: mudar(parcial=texto),
                              ao_avisar=lambda etapa: mudar(etapa=etapa, parcial=""))
            mudar(resposta=para_json(resposta))
        except Exception as erro:
            mudar(erro=f"Algo deu errado: {erro}")

    threading.Thread(target=trabalhar, daemon=True).start()
    return acompanhar(conversa, item)


def criar_api(s):
    """Rotas /api/... sobre os serviços compartilhados `s` (banco, blender, conhecimento, agente)."""
    api = APIRouter(prefix="/api")
    conversas = {}  # id -> Conversa; somem quando o servidor reinicia (o histórico fica no banco)

    def conversa_de(conversa_id, livre=False):
        """A conversa pelo id. Com `livre`, recusa se ela ainda está gerando uma resposta."""
        if conversa_id not in conversas:
            raise HTTPException(404, "Conversa não encontrada. Comece uma nova conversa.")
        if livre and conversas[conversa_id].andamento:
            raise HTTPException(409, "Espere a resposta anterior terminar.")
        return conversas[conversa_id]

    @api.get("/situacao")
    def situacao():
        modelos = nomes_de_modelos()
        padrao = config.MODELO_PADRAO if config.MODELO_PADRAO in modelos else (modelos[0] if modelos else "")
        return {
            "ollama": llm.disponivel(), "blender": s.blender.disponivel(),
            "conhecimento_vazio": s.conhecimento.vazia(), "modelos": modelos, "modelo": padrao,
            "tarefas": [{"id": t.id, "nome": t.nome, "pedido": t.pedido} for t in TAREFAS.values()],
            # Ainda ligando o Ollama e carregando o modelo: o chat mostra "Abrindo o Modelia…" em vez de "Quase lá".
            "preparando": config.PREPARANDO.is_set(),
            # Segundos típicos por modo neste computador, para a estimativa de espera.
            "tempo_tipico": s.banco.tempo_tipico(padrao) if padrao else {},
        }

    @api.post("/conversas")
    def nova_conversa():
        conversa_id = uuid.uuid4().hex
        conversas[conversa_id] = Conversa(conversa_id, Estado(sessao_id=s.banco.iniciar_sessao(IDENTIFICADOR),
                                                              modelo=""))
        return {"conversa": conversa_id}

    @api.get("/conversas")
    def listar_conversas():
        """As conversas com pelo menos uma mensagem, da mais nova para a mais antiga (lista "Hoje")."""
        return [{"conversa": c.id, "titulo": c.titulo, "ativa": c.andamento is not None}
                for c in sorted(conversas.values(), key=lambda c: c.criada, reverse=True) if c.titulo]

    @api.get("/conversas/{conversa_id}")
    def ver_conversa(conversa_id: str):
        c = conversa_de(conversa_id)
        with c.mudou:
            historico = [{k: v for k, v in item.items() if k != "versao"} for item in c.historico]
        return {"conversa": c.id, "titulo": c.titulo, "historico": historico, "ativa": c.andamento is not None}

    @api.get("/conversas/{conversa_id}/acompanhar")
    def acompanhar_conversa(conversa_id: str):
        c = conversa_de(conversa_id)
        item = c.andamento
        return acompanhar(c, item) if item else Response(status_code=204)

    @api.post("/mensagem")
    def mensagem(m: Mensagem):
        c = conversa_de(m.conversa, livre=True)
        if m.modo not in MODOS:
            raise HTTPException(400, f"Modo desconhecido: {m.modo}.")
        if m.modo != c.estado.modo:
            s.agente.trocar_modo(c.estado, m.modo)
        c.estado.modelo = m.modelo or c.estado.modelo
        if not c.estado.modelo:
            raise HTTPException(400, "Nenhum modelo de LLM instalado. Peça ao Pesquisador para baixar um.")
        c.decidir("nao_executado", todos=True)  # mensagem nova cancela o código que esperava o clique
        return responder(c, s.agente.responder, c.estado, m.texto, m.tarefa, pedido=m.texto)

    @api.post("/executar")
    def executar(a: Acao):
        c = conversa_de(a.conversa, livre=True)
        c.decidir("executado")
        return responder(c, s.agente.executar, c.estado)

    @api.post("/cancelar")
    def cancelar(a: Acao):
        c = conversa_de(a.conversa, livre=True)
        c.decidir("cancelado")
        return responder(c, s.agente.cancelar, c.estado)

    @api.get("/cena")
    def cena():
        imagem = s.blender.captura() if s.blender.disponivel() else None
        if imagem is None:
            return Response(status_code=204)
        dados = io.BytesIO()
        imagem.save(dados, format="PNG")
        return Response(dados.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    return api
