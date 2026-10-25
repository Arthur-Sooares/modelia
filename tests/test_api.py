"""Testes da API do chat com um agente falso (não precisam do Blender nem do Ollama)."""

import json
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modelia.agente import EXPLICAR, VERIFICAR, Resposta
from modelia.api import criar_api
from modelia.banco import Banco


class AgenteFalso:
    def __init__(self):
        self.trocas = []

    def responder(self, estado, mensagem, tarefa_id="", ao_escrever=None, ao_avisar=None):
        ao_avisar("Planejando")
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
        {"etapa": "Planejando"}, {"parcial": "pensando..."},
        {"fim": {"texto": "Explicar: oi (m1)", "codigo": "", "atualizar_cena": False, "detalhes": {"tarefa": ""},
                 "problema": ""}}]
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


def test_conversa_fica_no_servidor_e_volta_inteira(tmp_path):
    c, _ = cliente(tmp_path)
    conversa = c.post("/api/conversas").json()["conversa"]
    assert c.get("/api/conversas").json() == []  # sem mensagem ainda, não entra na lista "Hoje"
    c.post("/api/mensagem", json={"conversa": conversa, "texto": "o que é extrusão?", "modo": EXPLICAR, "modelo": "m1"})
    assert c.get("/api/conversas").json() == [{"conversa": conversa, "titulo": "o que é extrusão?", "ativa": False}]
    d = c.get(f"/api/conversas/{conversa}").json()
    assert [i["tipo"] for i in d["historico"]] == ["aluno", "modelia"]
    assert d["historico"][1]["resposta"]["texto"] == "Explicar: o que é extrusão? (m1)"
    assert c.get(f"/api/conversas/{conversa}/acompanhar").status_code == 204  # nada em andamento


def test_resposta_continua_sem_a_pagina_e_pode_ser_retomada(tmp_path):
    c, s = cliente(tmp_path)
    liberar = threading.Event()

    def responder_devagar(estado, mensagem, tarefa_id="", ao_escrever=None, ao_avisar=None):
        ao_escrever("metade")
        liberar.wait(5)
        return Resposta("tudo")

    s.agente.responder = responder_devagar
    conversa = c.post("/api/conversas").json()["conversa"]
    primeira = threading.Thread(target=c.post, args=("/api/mensagem",),
                                kwargs={"json": {"conversa": conversa, "texto": "oi", "modo": EXPLICAR, "modelo": "m"}})
    primeira.start()  # a página que fez o pedido "sai" (ninguém lê a resposta dela)
    for _ in range(100):
        d = c.get(f"/api/conversas/{conversa}").json()
        if d["ativa"] and d["historico"][-1]["parcial"] == "metade":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("a resposta não começou")
    ocupada = c.post("/api/mensagem", json={"conversa": conversa, "texto": "e aí?", "modo": EXPLICAR})
    assert ocupada.status_code == 409
    threading.Timer(0.1, liberar.set).start()
    retomada = linhas(c.get(f"/api/conversas/{conversa}/acompanhar"))  # a página "volta" para o chat
    assert retomada[0] == {"parcial": "metade"} and retomada[-1]["fim"]["texto"] == "tudo"
    primeira.join(5)


def test_codigo_pendente_fica_marcado(tmp_path):
    c, s = cliente(tmp_path)
    s.agente.responder = lambda estado, *a, **k: Resposta("passo 1", codigo="print(1)")
    s.agente.cancelar = lambda estado, **k: Resposta("Cancelado.")
    conversa = c.post("/api/conversas").json()["conversa"]
    c.post("/api/mensagem", json={"conversa": conversa, "texto": "mesa", "modo": EXPLICAR, "modelo": "m"})
    c.post("/api/mensagem", json={"conversa": conversa, "texto": "outra", "modo": EXPLICAR})
    c.post("/api/cancelar", json={"conversa": conversa})
    decisoes = [i["decisao"] for i in c.get(f"/api/conversas/{conversa}").json()["historico"] if i["tipo"] == "modelia"]
    assert decisoes == ["nao_executado", "cancelado", ""]
