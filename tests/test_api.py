"""Testes da API do chat com um agente falso (não precisam do Blender nem do Ollama)."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modelia.agente import EXPLICAR, VERIFICAR, Resposta
from modelia.api import criar_api
from modelia.banco import Banco


class AgenteFalso:
    def __init__(self):
        self.trocas = []

    def responder(self, estado, mensagem, tarefa_id="", ao_escrever=None):
        ao_escrever("pensando...")
        return Resposta(f"{estado.modo}: {mensagem} ({estado.modelo})", detalhes={"tarefa": tarefa_id} or None)

    def trocar_modo(self, estado, modo):
        self.trocas.append(modo)
        estado.modo = modo


def linhas(r):
    return [json.loads(linha) for linha in r.text.splitlines()]


def cliente(tmp_path):
    s = SimpleNamespace(banco=Banco(tmp_path / "t.sqlite3"), agente=AgenteFalso(),
                        blender=SimpleNamespace(disponivel=lambda: False), conhecimento=None)
    app = FastAPI()
    app.include_router(criar_api(s))
    return TestClient(app), s


def test_conversa_troca_de_modo_e_responde(tmp_path):
    c, s = cliente(tmp_path)
    conversa = c.post("/api/conversas").json()["conversa"]
    r = c.post("/api/mensagem", json={"conversa": conversa, "texto": "oi", "modo": EXPLICAR, "modelo": "m1"})
    assert r.status_code == 200 and linhas(r) == [
        {"parcial": "pensando..."},
        {"fim": {"texto": "Explicar: oi (m1)", "codigo": "", "atualizar_cena": False, "detalhes": {"tarefa": ""}}}]
    r = c.post("/api/mensagem", json={"conversa": conversa, "texto": "certo?", "modo": VERIFICAR, "tarefa": "mesa"})
    assert linhas(r)[-1]["fim"]["detalhes"] == {"tarefa": "mesa"} and s.agente.trocas == [VERIFICAR]  # modelo m1 continua


def test_erros_claros(tmp_path):
    c, _ = cliente(tmp_path)
    assert c.post("/api/mensagem", json={"conversa": "x", "texto": "oi", "modo": EXPLICAR}).status_code == 404
    conversa = c.post("/api/conversas").json()["conversa"]
    assert c.post("/api/mensagem", json={"conversa": conversa, "texto": "oi", "modo": "Dançar"}).status_code == 400
    sem_modelo = c.post("/api/mensagem", json={"conversa": conversa, "texto": "oi", "modo": EXPLICAR})
    assert sem_modelo.status_code == 400 and "modelo" in sem_modelo.json()["detail"]
    assert c.get("/api/cena").status_code == 204  # Blender fechado
