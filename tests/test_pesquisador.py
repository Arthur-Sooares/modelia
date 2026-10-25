"""Testes da API do Pesquisador com serviços falsos (não precisam do Blender nem do Ollama)."""

import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modelia import config, llm, manual
from modelia.banco import Banco
from modelia.pesquisador import criar_api_pesquisador

SENHA = {"X-Senha": config.SENHA_PESQUISADOR}


class ConhecimentoFalso:
    def __init__(self, documentos):
        self.documentos = documentos

    def listar(self):
        return list(self.documentos)

    def remover(self, doc_id):
        self.documentos = [d for d in self.documentos if d["doc_id"] != doc_id]


def doc(doc_id, titulo, trechos=3):
    return {"doc_id": doc_id, "titulo": titulo, "fonte": "", "trechos": trechos, "adicionado": "2026-10-19"}


def cliente(tmp_path, documentos=()):
    s = SimpleNamespace(banco=Banco(tmp_path / "t.sqlite3"), conhecimento=ConhecimentoFalso(documentos),
                        blender=SimpleNamespace(disponivel=lambda: False), parar_experimento=threading.Event())
    app = FastAPI()
    app.include_router(criar_api_pesquisador(s))
    return TestClient(app), s


def esperar(c, nome):
    for _ in range(100):
        trabalho = c.get("/api/pesquisador/trabalhos", headers=SENHA).json().get(nome, {})
        if trabalho and not trabalho["ativo"]:
            return trabalho
        time.sleep(0.02)
    raise AssertionError(f"o trabalho {nome} não terminou")


def test_aberto_sem_senha_definida(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SENHA_PESQUISADOR", "")
    c, _ = cliente(tmp_path)
    assert c.get("/api/pesquisador/historico").status_code == 200


def test_com_senha_definida_nao_entra_sem_ela(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SENHA_PESQUISADOR", "segredo")
    c, _ = cliente(tmp_path)
    assert c.get("/api/pesquisador/historico").status_code == 401
    assert c.get("/api/pesquisador/historico", headers={"X-Senha": "errada"}).status_code == 401
    assert c.get("/api/pesquisador/historico", headers={"X-Senha": "segredo"}).status_code == 200


def test_manual_aparece_como_um_cartao_so(tmp_path):
    paginas = [doc(f"m{i}", f"{manual.TITULO}: Página {i}", trechos=2) for i in range(5)]
    c, s = cliente(tmp_path, [*paginas, doc("a", "Apostila", trechos=10)])
    r = c.get("/api/pesquisador/conhecimento", headers=SENHA).json()
    assert r["manual"]["na_base"] and r["manual"]["paginas"] == 5 and r["manual"]["trechos"] == 10
    assert [d["titulo"] for d in r["documentos"]] == ["Apostila"]
    c.post("/api/pesquisador/manual/remover", headers=SENHA)
    assert [d["doc_id"] for d in s.conhecimento.documentos] == ["a"]  # a apostila fica


def test_historico_conta_por_modo(tmp_path):
    c, s = cliente(tmp_path)
    sessao = s.banco.iniciar_sessao("P07")
    for modo in ("Explicar", "Explicar", "Ensinar"):
        s.banco.registrar_interacao(sessao, modo, "m1", "oi")
    r = c.get("/api/pesquisador/historico", headers=SENHA).json()
    assert r["total"] == 3 and r["por_modo"]["Explicar"] == 2 and r["estudantes"] == ["P07"]
    assert c.get("/api/pesquisador/historico?desde=ontem", headers=SENHA).status_code == 400
    csv = c.get("/api/pesquisador/historico.csv?modo=Ensinar", headers=SENHA)
    assert csv.status_code == 200 and csv.content.decode("utf-8-sig").count("\n") == 2  # cabeçalho + 1 linha


def test_baixar_modelo_roda_em_segundo_plano(tmp_path, monkeypatch):
    def baixar(nome):
        yield 0.5, "Baixando… 1,0 de 2,0 GB"
        yield 1, "success"

    monkeypatch.setattr(llm, "baixar", baixar)
    c, _ = cliente(tmp_path)
    assert c.post("/api/pesquisador/modelos/baixar", json={"nome": " "}, headers=SENHA).status_code == 400
    assert c.post("/api/pesquisador/modelos/baixar", json={"nome": "m2"}, headers=SENHA).status_code == 200
    trabalho = esperar(c, "modelo")
    assert trabalho["nome"] == "m2" and trabalho["fracao"] == 1 and not trabalho["erro"]


def test_erro_do_trabalho_aparece_na_tela(tmp_path, monkeypatch):
    def baixar(nome):
        raise ConnectionError("sem internet")
        yield

    monkeypatch.setattr(llm, "baixar", baixar)
    c, _ = cliente(tmp_path)
    c.post("/api/pesquisador/modelos/baixar", json={"nome": "m2"}, headers=SENHA)
    assert esperar(c, "modelo")["erro"] == "sem internet"


def test_usar_como_padrao_fica_salvo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PASTA_DADOS", tmp_path)
    monkeypatch.setattr(config, "ARQUIVO_MODELO_PADRAO", tmp_path / "modelo_padrao.txt")
    monkeypatch.setattr(config, "MODELO_PADRAO", config.MODELO_PADRAO)
    monkeypatch.setattr(config, "MODELOS_NECESSARIOS", config.MODELOS_NECESSARIOS)
    monkeypatch.setattr(llm, "usa_ferramentas", lambda nome: nome != "gemma3:4b")
    c, _ = cliente(tmp_path)
    padrao = "/api/pesquisador/modelos/padrao"
    assert c.post(padrao, json={"nome": "qwen3-embedding:0.6b"}, headers=SENHA).status_code == 400
    assert c.post(padrao, json={"nome": "gemma3:4b"}, headers=SENHA).status_code == 400  # sem tools
    assert c.post(padrao, json={"nome": "mistral:7b"}, headers=SENHA).status_code == 200
    assert config.MODELO_PADRAO == "mistral:7b" and config.MODELOS_NECESSARIOS[0] == "mistral:7b"
    assert (tmp_path / "modelo_padrao.txt").read_text(encoding="utf-8") == "mistral:7b"
    assert c.post("/api/pesquisador/modelos/apagar", json={"nome": "mistral:7b"}, headers=SENHA).status_code == 400
