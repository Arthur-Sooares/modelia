import pytest

from modelia.banco import Banco, para_csv
from modelia.conhecimento import dividir
from modelia.tarefas import TAREFAS


@pytest.fixture
def banco(tmp_path):
    return Banco(tmp_path / "teste.sqlite3")


@pytest.mark.parametrize("identificador", ["", "   ", "Arthur Soares Cardoso", "a"])
def test_identificador_invalido(banco, identificador):
    with pytest.raises(ValueError):
        banco.iniciar_sessao(identificador)


def test_mesmo_estudante_varias_sessoes(banco):
    primeira = banco.iniciar_sessao("P07")
    segunda = banco.iniciar_sessao("P07")
    assert primeira != segunda
    assert banco.estudantes() == ["P07"]


def test_filtros_e_csv(banco):
    s1, s2 = banco.iniciar_sessao("P01"), banco.iniciar_sessao("P02")
    banco.registrar_interacao(s1, "Explicar", "m", "o que é?")
    banco.registrar_interacao(s2, "Verificar", "m", "está certo?")
    assert len(banco.historico()) == 2
    assert [l["estudante"] for l in banco.historico(modo="Verificar")] == ["P02"]
    assert banco.historico(desde="2999-01-01") == []
    assert para_csv(banco.historico(estudante="P01")).splitlines()[0].startswith("data,estudante,modo")


def malha(nome, dim, z_min, z_max, local=(0, 0, 0)):
    return {"nome": nome, "dim": sorted(dim), "local": list(local), "z_min": z_min, "z_max": z_max}


def test_mesa_correta_e_parcial():
    tampo = malha("Tampo", [2, 1, 0.1], 0.7, 0.8)
    pernas = [malha(f"Perna{i}", [0.1, 0.1, 0.7], 0.0, 0.7) for i in range(4)]
    assert TAREFAS["mesa"].verificar({"malhas": [tampo, *pernas], "outros": []})[0] == 3
    nota, certo, falta = TAREFAS["mesa"].verificar({"malhas": [tampo], "outros": []})
    assert nota == 2 and falta


def test_cubo_fora_do_chao():
    nota, _, falta = TAREFAS["cubo"].verificar({"malhas": [malha("Cube", [2, 2, 2], 1.0, 3.0)], "outros": []})
    assert nota == 2 and "z = 1.0" in falta[0]


def test_cena_com_luz():
    cena = {"malhas": [malha("Plano", [4, 4, 0], 0, 0), malha("Esfera", [1, 1, 1], 0, 1)],
            "outros": [{"nome": "Luz", "tipo": "LIGHT", "subtipo": "POINT", "local": [0, 0, 3]}]}
    assert TAREFAS["cena_luz"].verificar(cena)[0] == 3


def test_dividir_respeita_tamanho():
    texto = "\n\n".join(["parágrafo " * 30] * 10) + "\n\n" + "x" * 3000
    trechos = dividir(texto, tamanho=500, sobreposicao=50)
    assert all(len(t) <= 500 for t in trechos)
    assert sum(t.count("x") for t in trechos) >= 3000
