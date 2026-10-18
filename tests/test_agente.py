"""Testes do orquestrador com um Blender e uma LLM falsos (não precisam do Blender nem do Ollama)."""

import json
from types import SimpleNamespace

import pytest

from modelia import agente
from modelia.agente import DEMONSTRAR, ENSINAR, EXPLICAR, VERIFICAR, Agente, Estado
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
        if mensagens[0]["content"] == agente.SISTEMA_BUSCA:
            return mensagem("Termos do Blender")  # reescrita da busca: fora da fila, para não mudar os testes
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


def test_ensinar_nao_executa_sem_confirmacao(banco):
    blender = BlenderFalso()
    llm = LLMFalsa(mensagem("Vou criar o tampo. Atalho: Shift+A.", codigo="import bpy"))
    ag = Agente(blender, None, banco, conversar=llm)
    estado = novo_estado(banco, ENSINAR)

    resposta = ag.responder(estado, "mostre como fazer uma mesa")

    assert resposta.codigo == "import bpy"
    assert blender.executados == []  # RF6: nada rodou ainda
    assert estado.pendente is not None


def test_executar_roda_o_codigo_e_espera_proximo(banco):
    blender = BlenderFalso()
    llm = LLMFalsa(mensagem("Passo 1", codigo="passo1"), mensagem("Passo 2", codigo="passo2"))
    ag = Agente(blender, None, banco, conversar=llm)
    estado = novo_estado(banco, ENSINAR)

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
    estado = novo_estado(banco, ENSINAR)

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
    estado = novo_estado(banco, ENSINAR)

    ag.responder(estado, "faça um cubo")
    resposta = ag.executar(estado)
    assert resposta.codigo == "c2"  # correção proposta, esperando nova confirmação
    assert "NameError" in resposta.texto

    resposta = ag.executar(estado)
    assert not estado.em_ensino and estado.pendente is None
    assert "parei" in resposta.texto


def test_digitar_com_codigo_pendente_pede_o_clique(banco):
    llm = LLMFalsa(mensagem("p", codigo="c1"))
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    estado = novo_estado(banco, ENSINAR)
    ag.responder(estado, "faça um cubo")
    resposta = ag.responder(estado, "e agora?")
    assert "Executar" in resposta.texto and len(llm.chamadas) == 1


def test_parar_encerra_a_demonstracao(banco):
    llm = LLMFalsa(mensagem("p", codigo="c1"))
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    estado = novo_estado(banco, ENSINAR)
    ag.responder(estado, "faça um cubo")
    ag.executar(estado)
    resposta = ag.responder(estado, "parar")
    assert "encerrada" in resposta.texto and not estado.em_ensino


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
    assert resposta.detalhes["tarefa"] == "Cubo no chão" and resposta.detalhes["nota"] == 3


def test_verificar_cena_vazia_sugere_ensinar(banco):
    ag = Agente(BlenderFalso(), None, banco, conversar=LLMFalsa())
    resposta = ag.responder(novo_estado(banco, VERIFICAR), "está certo?", tarefa_id="mesa")
    assert "Ensinar" in resposta.texto


def test_tudo_fica_registrado_no_historico(banco):
    llm = LLMFalsa(mensagem("Passo 1", codigo="passo1"))
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    estado = novo_estado(banco, ENSINAR)
    ag.responder(estado, "faça uma mesa")
    ag.executar(estado)
    linhas = banco.historico(estudante="P01")
    assert len(linhas) == 1
    assert linhas[0]["modo"] == ENSINAR and linhas[0]["codigo_executado"] == "passo1"


def test_explicar_llm_diz_que_nao_sabe(banco):
    llm = LLMFalsa(mensagem("NAO_SEI"))
    trechos = [{"texto": "Subdivision Surface.", "titulo": "Manual", "fonte": "", "distancia": 0.3}]
    ag = Agente(BlenderFalso(), ConhecimentoFalso(trechos), banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, EXPLICAR), "como faço uma mesa?")
    assert "não cobre" in resposta.texto and "Fontes" not in resposta.texto


def test_ensinar_usa_o_campo_explicacao(banco):
    chamada = SimpleNamespace(function=SimpleNamespace(
        name="execute_blender_code", arguments={"code": "c1", "explicacao": "Crio o tampo. Atalho: Shift+A."}))
    resposta_llm = SimpleNamespace(message=SimpleNamespace(role="assistant", content="", tool_calls=[chamada]))
    llm = LLMFalsa(resposta_llm)
    ag = Agente(BlenderFalso(), None, banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, ENSINAR), "faça uma mesa")
    assert "Shift+A" in resposta.texto and resposta.codigo == "c1"


def test_com_explicacao_torna_o_campo_obrigatorio():
    ferramentas = [{"type": "function", "function": {
        "name": "execute_blender_code", "parameters": {"type": "object", "properties": {"code": {}},
                                                      "required": ["code"]}}}]
    parametros = agente.com_explicacao(ferramentas)[0]["function"]["parameters"]
    assert parametros["required"] == ["explicacao", "code"]
    assert "explicacao" not in ferramentas[0]["function"]["parameters"]["properties"]  # original intacto


# --- Demonstrar: cria sozinho, confere a cena e corrige ---

PERNA_NO_CHAO = {"nome": "Perna", "dim": [0.06, 0.06, 0.71], "local": [0, 0, 0], "z_min": 0.0, "z_max": 0.71,
                 "xy": [-0.03, 0.03, -0.03, 0.03]}
TAMPO = {"nome": "Tampo", "dim": [0.04, 0.9, 1.6], "local": [0, 0, 0.71], "z_min": 0.71, "z_max": 0.75,
         "xy": [-0.8, 0.8, -0.45, 0.45]}
PERNA_FLUTUANDO = {**PERNA_NO_CHAO, "z_min": 0.3, "z_max": 1.01}


class BlenderDemonstrar(BlenderFalso):
    """Devolve uma medição da fila a cada medir_cena (uma por tentativa)."""

    def __init__(self, *cenas, erros=0):
        super().__init__(erros=erros)
        self.cenas = list(cenas)
        self.removidos = []

    def medir_cena(self):
        return self.cenas.pop(0)

    def executar_com_ajudantes(self, codigo):
        return self.executar(codigo)

    def remover_malhas_exceto(self, nomes):
        self.removidos.append(set(nomes))

    def enquadrar(self):
        self.enquadrado = True


def test_problemas_da_cena():
    assert agente.problemas_da_cena({"malhas": [PERNA_NO_CHAO, TAMPO]}) == []
    problemas = agente.problemas_da_cena({"malhas": [PERNA_FLUTUANDO]})
    assert len(problemas) == 1 and "flutuando" in problemas[0]
    abaixo = {**PERNA_NO_CHAO, "z_min": -0.5}
    assert "abaixo do chão" in agente.problemas_da_cena({"malhas": [abaixo]})[0]
    # assento na altura certa, mas ao lado da mesa: não encosta em nada
    assento = {**TAMPO, "nome": "Assento", "xy": [1.0, 1.4, -0.2, 0.2]}
    assert "Assento" in agente.problemas_da_cena({"malhas": [PERNA_NO_CHAO, TAMPO, assento]})[0]


def test_demonstrar_executa_sem_clique(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    llm = LLMFalsa(mensagem("Criei uma mesa de madeira.", codigo="caixa('P', 1, 1, 1)"))
    ag = Agente(blender, None, banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, DEMONSTRAR), "crie uma mesa")
    assert blender.executados == ["caixa('P', 1, 1, 1)"] and resposta.codigo == ""
    assert "mesa de madeira" in resposta.texto and resposta.atualizar_cena and blender.enquadrado
    assert llm.chamadas[0]["ferramentas"] == {"execute_blender_code"}


def test_demonstrar_corrige_peca_flutuando(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_FLUTUANDO], "outros": []},
                                {"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    llm = LLMFalsa(mensagem("", codigo="caixa('P', 1, 1, 1)"), mensagem("Mesa corrigida.", codigo="caixa('P', 2, 1, 1)"))
    ag = Agente(blender, None, banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, DEMONSTRAR), "crie uma mesa")
    assert blender.executados == ["caixa('P', 1, 1, 1)", "caixa('P', 2, 1, 1)"] and blender.removidos == [set(), set()]
    assert "flutuando" in llm.chamadas[1]["mensagens"][-1]["content"]
    assert resposta.texto.startswith("Mesa corrigida.")


def test_demonstrar_desiste_depois_das_tentativas(banco):
    flutuando = {"malhas": [PERNA_FLUTUANDO], "outros": []}
    blender = BlenderDemonstrar(*[flutuando] * 3)
    llm = LLMFalsa(*[mensagem("", codigo=f"caixa('P', {i}, 1, 1)") for i in range(3)])
    ag = Agente(blender, None, banco, conversar=llm)
    resposta = ag.responder(novo_estado(banco, DEMONSTRAR), "crie uma mesa")
    assert len(blender.executados) == 3 and "tentativas" in resposta.texto


def test_demonstrar_recusa_codigo_sem_as_funcoes_prontas(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    llm = LLMFalsa(mensagem("", codigo="bpy.ops.mesh.primitive_cube_add()"), mensagem("Mesa.", codigo="caixa('P', 1, 1, 1)"))
    ag = Agente(blender, None, banco, conversar=llm)
    ag.responder(novo_estado(banco, DEMONSTRAR), "crie uma mesa")
    assert blender.executados == ["caixa('P', 1, 1, 1)"]
    assert "caixa()" in llm.chamadas[1]["mensagens"][-1]["content"]


def test_sem_redefinicoes_tira_so_as_funcoes_prontas():
    codigo = "def caixa(nome):\n    pass\n\ndef ajuda():\n    pass\ncaixa('Tampo')\n"
    limpo = agente.sem_redefinicoes(codigo)
    assert "def caixa" not in limpo and "def ajuda" in limpo and "caixa('Tampo')" in limpo
    assert agente.sem_redefinicoes("caixa('A')  # comentário") == "caixa('A')  # comentário"
    assert agente.sem_redefinicoes("def (") == "def ("


def test_experimento_modelia_usa_o_demonstrar_com_a_mesma_seed(banco):
    from modelia import experimento, tarefas

    mesa = {"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []}
    blender = BlenderDemonstrar(mesa, mesa)
    blender.abrir_cena = lambda caminho: None
    llm = LLMFalsa(mensagem("Mesa pronta.", codigo="caixa('P', 1, 1, 1)"))
    opcoes_vistas = []
    def conversar(modelo, mensagens, ferramentas=None, opcoes=None):
        opcoes_vistas.append(opcoes)
        return llm(modelo, mensagens, ferramentas, opcoes)

    linha = experimento.executar_uma(blender, "falso", tarefas.TAREFAS["mesa"], 7, 0.3,
                                     experimento.MODELIA, banco, conversar)
    assert linha["condicao"] == "modelia" and linha["codigo_gerado"] == "caixa('P', 1, 1, 1)"
    assert linha["chamadas_ferramenta"] == 1 and linha["erros_execucao"] == 0
    assert opcoes_vistas[0]["seed"] == 7 and opcoes_vistas[0]["temperature"] == 0.3
    assert "Pedido:" in llm.chamadas[0]["mensagens"][1]["content"]  # prompt do Demonstrar, não o puro


def test_planejar_deixa_as_condicoes_lado_a_lado():
    from modelia import experimento

    plano = experimento.planejar(["m"], ["mesa"], 2, [1, 2])
    assert plano == [("puro", "m", "mesa", 1, 1), ("modelia", "m", "mesa", 1, 1),
                     ("puro", "m", "mesa", 2, 2), ("modelia", "m", "mesa", 2, 2)]


def test_pedidos_simultaneos_entram_na_fila(banco):
    import threading
    import time

    ativos, maximo = [0], [0]

    def llm_lenta(modelo, mensagens, ferramentas=None, opcoes=None):
        ativos[0] += 1
        maximo[0] = max(maximo[0], ativos[0])
        time.sleep(0.05)
        ativos[0] -= 1
        return mensagem("Resposta.")

    ag = Agente(None, ConhecimentoFalso([{"titulo": "Apostila", "fonte": "", "texto": "..."}]), banco,
                conversar=llm_lenta)
    pedidos = [threading.Thread(target=ag.responder, args=(novo_estado(banco, EXPLICAR), "o que é?"))
               for _ in range(3)]
    for p in pedidos:
        p.start()
    for p in pedidos:
        p.join()
    assert maximo[0] == 1  # nunca dois pedidos falando com a LLM ao mesmo tempo


def test_ensinar_aceita_codigo_escrito_no_texto(banco):
    texto = "Vou criar o tampo. Atalho: Shift+A.\n\n```python\nimport bpy\nbpy.ops.mesh.primitive_cube_add()\n```"
    blender = BlenderFalso()
    ag = Agente(blender, None, banco, conversar=LLMFalsa(mensagem(texto)))
    estado = novo_estado(banco, ENSINAR)

    resposta = ag.responder(estado, "me ensina a fazer uma mesa")

    assert resposta.codigo == "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    assert "Shift+A" in resposta.texto and "```" not in resposta.texto
    assert blender.executados == [] and estado.em_ensino  # continua esperando o clique (RF6)


def test_demonstrar_aceita_codigo_escrito_no_texto(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    ag = Agente(blender, None, banco, conversar=LLMFalsa(mensagem("Mesa:\n```python\ncaixa('P', 1, 1, 1)\n```")))
    resposta = ag.responder(novo_estado(banco, DEMONSTRAR), "crie uma mesa")
    assert blender.executados == ["caixa('P', 1, 1, 1)"] and resposta.atualizar_cena
    assert "```" not in resposta.texto and resposta.texto.startswith("Mesa:")


def test_demonstrar_nao_mostra_o_codigo_repetido_no_texto(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    llm = LLMFalsa(mensagem("Vou criar a mesa.\n```python\ncaixa('P', 1, 1, 1)\n```", codigo="caixa('P', 1, 1, 1)"))
    resposta = Agente(blender, None, banco, conversar=llm).responder(novo_estado(banco, DEMONSTRAR), "mesa")
    assert "```" not in resposta.texto and resposta.texto.startswith("Vou criar a mesa.")


def test_streaming_passa_o_texto_para_quem_pediu(banco):
    vistos = []

    def llm(modelo, mensagens, ferramentas=None, opcoes=None, ao_escrever=None):
        ao_escrever("Use G para mover.")
        return mensagem("Use G para mover.")

    ag = Agente(BlenderFalso(), ConhecimentoFalso([{"titulo": "Atalhos", "fonte": "", "texto": "G move"}]),
                banco, conversar=llm)
    ag.responder(novo_estado(banco, EXPLICAR), "como movo?", ao_escrever=vistos.append)
    assert vistos == ["Use G para mover."] and ag._ao_escrever is None


def test_demonstrar_avisa_as_etapas(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_FLUTUANDO], "outros": []},
                                {"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    llm = LLMFalsa(mensagem("", codigo="caixa('P', 1, 1, 1)"), mensagem("Mesa.", codigo="caixa('P', 2, 1, 1)"))
    etapas = []
    Agente(blender, None, banco, conversar=llm).responder(
        novo_estado(banco, DEMONSTRAR), "crie uma mesa", ao_avisar=etapas.append)
    assert etapas == ["Planejando as peças…", "Criando no Blender…", "Conferindo a cena…",
                      "Corrigindo o objeto (tentativa 2 de 3)…", "Criando no Blender…", "Conferindo a cena…"]


def test_validar_acha_parametros_errados_antes_do_blender():
    oculos = ("caixa('Lente', 0.04, 0.03, 0.01, x=-0.02, achatar=0.95)\n"  # caixa não tem achatar
              "esfera('Ponte')\n"                                           # falta o raio
              "cilindro('Haste', 0.003, 0.14, eixo='y')\n"                  # certo
              "cone('C', 1, 2, 0, 0, 0, '#fff', 0, 9)\n")                   # valores demais
    problemas = agente.validar(oculos)
    assert len(problemas) == 3
    assert "achatar não existe" in problemas[0] and "Linha 2, esfera(): falta raio" in problemas[1]
    assert "no máximo 8" in problemas[2]
    assert agente.validar("caixa('A', 1, 1, 1)\nwith ao_lado(0.5):\n    esfera('B', 0.1, achatar=0.5)") == []
    assert agente.validar("caixa('A', 1,")[0].startswith("Erro de sintaxe na linha 1")


def test_demonstrar_nao_manda_codigo_invalido_ao_blender(banco):
    blender = BlenderDemonstrar({"malhas": [PERNA_NO_CHAO, TAMPO], "outros": []})
    llm = LLMFalsa(mensagem("", codigo="caixa('A', 1, 1, 1, achatar=0.9)"), mensagem("Mesa.", codigo="caixa('P', 1, 1, 1)"))
    ag = Agente(blender, None, banco, conversar=llm)
    ag.responder(novo_estado(banco, DEMONSTRAR), "crie uma mesa")
    assert blender.executados == ["caixa('P', 1, 1, 1)"]
    assert "achatar não existe" in llm.chamadas[1]["mensagens"][-1]["content"]


def test_explicar_busca_com_os_termos_do_blender(banco):
    buscas = []

    class Conhecimento(ConhecimentoFalso):
        def buscar(self, pergunta):
            buscas.append(pergunta)
            return []

    def llm(reescrita):
        def conversar(modelo, mensagens, ferramentas=None, opcoes=None):
            return mensagem(reescrita)
        return conversar

    Agente(None, Conhecimento([]), banco, conversar=llm("Escala (Scale), atalho S")).responder(
        novo_estado(banco, EXPLICAR), "como aumento o objeto?")
    Agente(None, Conhecimento([]), banco, conversar=llm("FORA")).responder(
        novo_estado(banco, EXPLICAR), "capital da França?")
    assert buscas == ["como aumento o objeto?\nEscala (Scale), atalho S", "capital da França?"]
