"""Testes do orquestrador com um Blender e uma LLM falsos (não precisam do Blender nem do Ollama)."""

import json
from types import SimpleNamespace

import pytest

from modelia import agente
from modelia.agente import DEMONSTRAR, EXPLICAR, VERIFICAR, Agente, Estado
from modelia.banco import Banco


def mensagem(texto="", codigo=None, leitura=None):
    chamadas = []
    if leitura:
        chamadas.append(SimpleNamespace(function=SimpleNamespace(name=leitura, arguments={})))
    if codigo is not None:
        chamadas.append(SimpleNamespace(function=SimpleNamespace(name="execute_blender_code",
                                                                 arguments={"code": codigo})))
    return SimpleNamespace(message=SimpleNamespace(role="assistant", content=texto, tool_calls=chamadas or None))


class LLMFalsa:
    """Devolve as respostas da fila, na ordem, e guarda o que recebeu."""

    def __init__(self, *respostas):
        self.fila = list(respostas)
        self.chamadas = []

    def __call__(self, modelo, mensagens, ferramentas=None, opcoes=None):
        nomes = {f["function"]["name"] for f in ferramentas or []}
        self.chamadas.append({"mensagens": list(mensagens), "ferramentas": nomes})
        return self.fila.pop(0)


class BlenderFalso:
    def __init__(self, erros=0, cena=None):
        self.executados = []
        self.erros = erros
        self.cena = cena or {"malhas": [], "outros": []}

    def disponivel(self):
        return True

    def ferramentas(self, nomes):
        return [{"type": "function", "function": {"name": n, "description": "", "parameters": {}}} for n in nomes]

    def executar(self, codigo):
        self.executados.append(codigo)
        if self.erros:
            self.erros -= 1
            return json.dumps({"status": "error", "message": "NameError: bpyy"}), True
        return json.dumps({"status": "ok", "result": None}), False

    def chamar(self, nome, argumentos):
        return json.dumps({"status": "ok", "result": {"objetos": []}}), False

    def medir_cena(self):
        return self.cena


class ConhecimentoFalso:
    def __init__(self, trechos):
        self.trechos = trechos

    def buscar(self, pergunta):
        return self.trechos


@pytest.fixture
def banco(tmp_path):
    return Banco(tmp_path / "teste.sqlite3")


def novo_estado(banco, modo):
    return Estado(sessao_id=banco.iniciar_sessao("P01"), modelo="falso", modo=modo)


def test_demonstrar_nao_executa_sem_confirmacao(banco):
    blender = BlenderFalso()
    llm = LLMFalsa(mensagem("Vou criar o tampo. Atalho: Shift+A.", codigo="import bpy"))
    ag = Agente(blender, None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)

    resposta = ag.responder(estado, "mostre como fazer uma mesa")

    assert resposta.codigo == "import bpy"
    assert blender.executados == []  # RF6: nada rodou ainda
    assert estado.pendente is not None


def test_executar_roda_o_codigo_e_espera_proximo(banco):
    blender = BlenderFalso()
    llm = LLMFalsa(mensagem("Passo 1", codigo="passo1"), mensagem("Passo 2", codigo="passo2"))
    ag = Agente(blender, None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)

    ag.responder(estado, "mostre como fazer uma mesa")
    resposta = ag.executar(estado)
    assert blender.executados == ["passo1"]
    assert resposta.atualizar_cena
    assert len(llm.chamadas) == 1  # não pede o próximo passo sozinho

    resposta = ag.responder(estado, "Próximo!")
    assert resposta.codigo == "passo2"
    assert blender.executados == ["passo1"]


def test_cancelar_nao_executa_e_avisa_a_llm(banco):
    blender = BlenderFalso()
    llm = LLMFalsa(mensagem("Passo 1", codigo="apagar_tudo()"), mensagem("Tudo bem, paro aqui."))
    ag = Agente(blender, None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)

    ag.responder(estado, "mostre algo")
    ag.cancelar(estado)
    ag.responder(estado, "próximo")

    assert blender.executados == []
    ultima_ferramenta = [m for m in llm.chamadas[-1]["mensagens"] if isinstance(m, dict) and m["role"] == "tool"]
    assert "NÃO foi executado" in ultima_ferramenta[-1]["content"]


def test_erro_volta_para_a_llm_ate_o_limite(banco, monkeypatch):
    monkeypatch.setattr(agente.config, "MAX_TENTATIVAS", 2)
    blender = BlenderFalso(erros=5)
    llm = LLMFalsa(mensagem("p", codigo="c1"), mensagem("corrigido", codigo="c2"))
    ag = Agente(blender, None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)

    ag.responder(estado, "faça um cubo")
    resposta = ag.executar(estado)
    assert resposta.codigo == "c2"  # correção proposta, esperando nova confirmação
    assert "NameError" in resposta.texto

    resposta = ag.executar(estado)
    assert not estado.em_demonstracao and estado.pendente is None
    assert "parei" in resposta.texto


def test_digitar_com_codigo_pendente_pede_o_clique(banco):
    llm = LLMFalsa(mensagem("p", codigo="c1"))
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)
    ag.responder(estado, "faça um cubo")
    resposta = ag.responder(estado, "e agora?")
    assert "Executar" in resposta.texto and len(llm.chamadas) == 1


def test_parar_encerra_a_demonstracao(banco):
    llm = LLMFalsa(mensagem("p", codigo="c1"))
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)
    ag.responder(estado, "faça um cubo")
    ag.executar(estado)
    resposta = ag.responder(estado, "parar")
    assert "encerrada" in resposta.texto and not estado.em_demonstracao


def test_explicar_nao_oferece_ferramentas_e_cita_fonte(banco):
    llm = LLMFalsa(mensagem("O Subdivision Surface suaviza a malha."))
    trechos = [{"texto": "Subdivision Surface divide as faces.", "titulo": "Manual do Blender",
                "fonte": "docs.blender.org", "distancia": 0.2}]
    ag = Agente(BlenderFalso(), ConhecimentoFalso(trechos), banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, EXPLICAR), "o que é subdivision surface?")
    assert llm.chamadas[0]["ferramentas"] == set()
    assert "Manual do Blender" in resposta.texto


def test_explicar_sem_material_nao_chama_a_llm(banco):
    llm = LLMFalsa()
    ag = Agente(BlenderFalso(), ConhecimentoFalso([]), banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, EXPLICAR), "quem ganhou a copa?")
    assert "não cobre" in resposta.texto and llm.chamadas == []


def test_verificar_so_oferece_ferramentas_de_leitura(banco):
    cena = {"malhas": [{"nome": "Cube", "dim": [2.0, 2.0, 2.0], "local": [0, 0, 1], "z_min": 0.0, "z_max": 2.0}],
            "outros": []}
    llm = LLMFalsa(mensagem("", leitura="get_objects_summary"), mensagem("Está certo!"))
    blender = BlenderFalso(cena=cena)
    ag = Agente(blender, None, banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, VERIFICAR), "minha cena está certa?", tarefa_id="cubo")
    assert "execute_blender_code" not in llm.chamadas[0]["ferramentas"]
    assert blender.executados == []
    assert resposta.texto == "Está certo!"


def test_verificar_cena_vazia_sugere_demonstrar(banco):
    ag = Agente(BlenderFalso(), None, banco, conversar=LLMFalsa())
    resposta = ag.responder(novo_estado(banco, VERIFICAR), "está certo?", tarefa_id="mesa")
    assert "Demonstrar" in resposta.texto


def test_tudo_fica_registrado_no_historico(banco):
    llm = LLMFalsa(mensagem("Passo 1", codigo="passo1"))
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    estado = novo_estado(banco, DEMONSTRAR)
    ag.responder(estado, "faça uma mesa")
    ag.executar(estado)
    linhas = banco.historico(estudante="P01")
    assert len(linhas) == 1
    assert linhas[0]["modo"] == DEMONSTRAR and linhas[0]["codigo_executado"] == "passo1"


def test_explicar_llm_diz_que_nao_sabe(banco):
    llm = LLMFalsa(mensagem("NAO_SEI"))
    trechos = [{"texto": "Subdivision Surface.", "titulo": "Manual", "fonte": "", "distancia": 0.3}]
    ag = Agente(BlenderFalso(), ConhecimentoFalso(trechos), banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, EXPLICAR), "como faço uma mesa?")
    assert "não cobre" in resposta.texto and "Fontes" not in resposta.texto
