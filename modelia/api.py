"""API JSON do chat novo (site/chat.html). Usa o mesmo Agente do app em Gradio.

Cada conversa do navegador vira um Estado guardado na memória do servidor, com uma
sessão própria no banco (RNF10). Ainda sem login: todas as conversas são do "visitante".
"""

import io
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from modelia import config, llm
from modelia.agente import MODOS, Estado
from modelia.tarefas import TAREFAS

# ponytail: identificador fixo até a tela de entrar voltar (RF1); aí vem do login.
IDENTIFICADOR = "visitante"


class Mensagem(BaseModel):
    conversa: str
    texto: str
    modo: str
    modelo: str = ""
    tarefa: str = ""


class Acao(BaseModel):
    conversa: str


def nomes_de_modelos():
    try:
        return llm.modelos_de_chat()
    except Exception:
        return []


def para_json(resposta):
    return {"texto": resposta.texto, "codigo": resposta.codigo,
            "atualizar_cena": resposta.atualizar_cena, "detalhes": resposta.detalhes}


def criar_api(s):
    """Rotas /api/... sobre os serviços compartilhados `s` (banco, blender, conhecimento, agente)."""
    api = APIRouter(prefix="/api")
    conversas = {}  # id -> Estado; somem quando o servidor reinicia (o histórico fica no banco)

    def estado_de(conversa):
        if conversa not in conversas:
            raise HTTPException(404, "Conversa não encontrada. Comece uma nova conversa.")
        return conversas[conversa]

    def responder(funcao, *args):
        try:
            return para_json(funcao(*args))
        except HTTPException:
            raise
        except Exception as erro:
            raise HTTPException(502, f"Algo deu errado: {erro}") from erro

    @api.get("/situacao")
    def situacao():
        modelos = nomes_de_modelos()
        padrao = config.MODELO_PADRAO if config.MODELO_PADRAO in modelos else (modelos[0] if modelos else "")
        return {
            "ollama": llm.disponivel(), "blender": s.blender.disponivel(),
            "conhecimento_vazio": s.conhecimento.vazia(), "modelos": modelos, "modelo": padrao,
            "tarefas": [{"id": t.id, "nome": t.nome, "pedido": t.pedido} for t in TAREFAS.values()],
        }

    @api.post("/conversas")
    def nova_conversa():
        conversa = uuid.uuid4().hex
        conversas[conversa] = Estado(sessao_id=s.banco.iniciar_sessao(IDENTIFICADOR), modelo="")
        return {"conversa": conversa}

    @api.post("/mensagem")
    def mensagem(m: Mensagem):
        estado = estado_de(m.conversa)
        if m.modo not in MODOS:
            raise HTTPException(400, f"Modo desconhecido: {m.modo}.")
        if m.modo != estado.modo:
            s.agente.trocar_modo(estado, m.modo)
        estado.modelo = m.modelo or estado.modelo
        if not estado.modelo:
            raise HTTPException(400, "Nenhum modelo de LLM instalado. Peça ao Pesquisador para baixar um.")
        return responder(s.agente.responder, estado, m.texto, m.tarefa)

    @api.post("/executar")
    def executar(a: Acao):
        return responder(s.agente.executar, estado_de(a.conversa))

    @api.post("/cancelar")
    def cancelar(a: Acao):
        return responder(s.agente.cancelar, estado_de(a.conversa))

    @api.get("/cena")
    def cena():
        imagem = s.blender.captura() if s.blender.disponivel() else None
        if imagem is None:
            return Response(status_code=204)
        dados = io.BytesIO()
        imagem.save(dados, format="PNG")
        return Response(dados.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    return api
