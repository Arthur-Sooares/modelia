import json

import pytest

from modelia import agente
from modelia.pecas import montar, resolver, _limpar


def peca(nome, forma, l, p, a, posicao="centro", de="chao", **extra):
    return {"nome": nome, "forma": forma, "largura": l, "profundidade": p, "altura": a, "posicao": posicao,
            "de": de, "cor": "branco", **extra}


def colocar(*lista):
    return {c.nome: c for c in resolver(_limpar({"objeto": "teste", "pecas": list(lista)})[1])}


def test_pernas_vao_do_chao_ate_o_tampo_nos_cantos():
    pecas = colocar(peca("Tampo", "caixa", 1.4, 0.8, 0.04, altura_do_chao=0.71),
                    peca("Perna", "caixa", 0.06, 0.06, 0.5, "embaixo_cantos", "Tampo"))
    pernas = [pecas[f"Perna{i}"] for i in range(1, 5)]
    assert all(p.base == 0 and p.altura == pytest.approx(0.71) for p in pernas)  # a altura vem do tampo
    assert {round(abs(p.x), 2) for p in pernas} == {0.65} and {round(abs(p.y), 2) for p in pernas} == {0.35}


def test_empilhar_e_encostar():
    pecas = colocar(peca("Base", "cilindro", 0.16, 0.16, 0.02),
                    peca("Haste", "cilindro", 0.02, 0.02, 0.3, "em_cima", "Base"),
                    peca("Cupula", "cilindro_oco", 0.25, 0.25, 0.15, "em_cima", "Haste", topo=0.5))
    assert pecas["Haste"].base == pytest.approx(0.02) and pecas["Cupula"].base == pytest.approx(0.32)
    encosto = colocar(peca("Assento", "caixa", 0.45, 0.45, 0.04, altura_do_chao=0.41),
                      peca("Encosto", "caixa", 0.45, 0.03, 0.45, "em_cima_tras", "Assento"))["Encosto"]
    assert encosto.base == pytest.approx(0.45) and encosto.y == pytest.approx(0.21)


def test_alca_encosta_por_fora_no_meio_da_altura():
    pecas = colocar(peca("Corpo", "cilindro_oco", 0.08, 0.08, 0.1),
                    peca("Alca", "alca", 0.03, 0.01, 0.06, "ao_lado_direita", "Corpo"))
    alca = pecas["Alca"]
    assert 0.03 < alca.x < 0.04 and alca.base == pytest.approx(0.02) and alca.giro == 0


def test_repetir_em_grade_e_dentro():
    pecas = colocar(peca("Cubinho", "caixa", 0.019, 0.019, 0.019, repetir_x=3, repetir_y=3, repetir_z=3))
    assert len(pecas) == 27 and min(c.base for c in pecas.values()) == 0
    vaso = colocar(peca("Vaso", "cilindro_oco", 0.25, 0.25, 0.25),
                   peca("Planta", "esfera", 0.4, 0.4, 0.4, "dentro", "Vaso"))
    assert vaso["Planta"].largura == pytest.approx(0.225) and vaso["Planta"].base == pytest.approx(0.125)


def test_referencia_em_ciclo_ou_inexistente_nao_trava():
    pecas = colocar(peca("A", "caixa", 1, 1, 1, "em_cima", "B"), peca("B", "caixa", 1, 1, 1, "em_cima", "A"),
                    peca("C", "caixa", 1, 1, 1, "em_cima", "NaoExiste"))
    assert set(pecas) == {"A", "B", "C"}


def test_montar_gera_codigo_com_as_funcoes_prontas():
    ficha = {"objeto": "caneca", "pecas": [peca("Corpo", "cilindro_oco", 0.08, 0.08, 0.1),
                                           peca("Alca", "alca", 0.03, 0.01, 0.06, "ao_lado_direita", "Corpo")]}
    codigo, frase = montar(json.dumps(ficha))
    assert "cilindro_oco('Corpo', 0.04, 0.1" in codigo and "alca('Alca'" in codigo
    assert agente.validar(codigo) == [] and frase.startswith("Pronto! Criei caneca com 2 peças")
    with pytest.raises(ValueError):
        montar('{"objeto": "x", "pecas": []}')


def test_cena_vazia_e_um_problema():
    assert "vazia" in agente.problemas_da_cena({"malhas": [], "outros": []})[0]


def test_rodas_deitadas_nos_lados_da_carroceria():
    pecas = colocar(peca("Carroceria", "caixa", 4, 1.8, 0.8, altura_do_chao=0.3),
                    peca("Roda", "cilindro", 0.6, 0.6, 0.2, "rodas", "Carroceria"))
    rodas = [pecas[f"Roda{i}"] for i in range(1, 5)]
    assert all(r.eixo == "y" and r.base == 0 and r.altura == pytest.approx(0.6) for r in rodas)
    assert {round(abs(r.y), 2) for r in rodas} == {0.9} and {round(abs(r.x), 2) for r in rodas} == {1.46}
    assert "eixo='y'" in montar(json.dumps({"objeto": "carro", "descricao": "carro\nvermelho", "pecas": [
        peca("Carroceria", "caixa", 4, 1.8, 0.8, altura_do_chao=0.3),
        peca("Roda", "cilindro", 0.6, 0.6, 0.2, "rodas", "Carroceria")]}))[0]


def test_toda_a_biblioteca_monta_codigo_valido():
    from modelia.exemplos_fichas import EXEMPLOS
    for nomes, ficha in EXEMPLOS:
        codigo, _ = montar(json.dumps(ficha))
        assert agente.validar(codigo) == [], nomes


def test_exemplos_para_poe_o_mais_parecido_por_ultimo():
    from modelia.pecas import exemplos_para
    assert exemplos_para("faça uma casinha de cachorro")[-1]["objeto"] == "casinha de cachorro"
    assert exemplos_para("quero um copo grande")[-1]["objeto"] == "copo"
    assert len(exemplos_para("um dragão")) == 3  # nada parecido: exemplos variados
