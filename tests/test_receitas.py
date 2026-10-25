import ast

import pytest

from modelia import receitas
from modelia.receitas import RECEITAS, identificar, ler_ficha, montar


@pytest.mark.parametrize("pedido, esperado", [
    ("me faça uma estante com 4 prateleiras", [("estante", 1)]),
    ("Crie uma mesa com duas cadeiras", [("mesa", 1), ("cadeira", 2)]),
    ("quero 3 cadeiras", [("cadeira", 3)]),
    ("Crie uma luminária de mesa.", []),
    ("Faça um boneco de neve.", []),
])
def test_identificar(pedido, esperado):
    assert [(r.nome, q) for r, q in identificar(pedido)] == esperado


def test_ler_ficha_limita_e_completa():
    ficha = ler_ficha(RECEITAS["estante"], '{"largura": 9, "profundidade": 0.3, "altura": 1.8, '
                                           '"nichos": [0.3, 5], "fundo": true, "cor": "roxo"}')
    assert ficha["largura"] == 3 and ficha["nichos"] == [0.3, 1.2] and ficha["cor"] == "madeira"
    with pytest.raises(ValueError):
        ler_ficha(RECEITAS["mesa"], '{"largura": 1.2}')
    with pytest.raises(ValueError):
        ler_ficha(RECEITAS["mesa"], "não é json")


def pecas_do_codigo(codigo):
    """{nome: (função, medidas, x, y, base)} das chamadas no código montado."""
    pecas = {}
    for n in ast.walk(ast.parse(codigo)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("caixa", "cilindro"):
            args = {k.arg: ast.literal_eval(k.value) for k in n.keywords if k.arg != "cor"}
            pecas[n.args[0].value] = (n.func.id, [ast.literal_eval(a) for a in n.args[1:]],
                                      args.get("x", 0), args.get("y", 0), args.get("base", 0))
    return pecas


def test_estante_respeita_altura_e_proporcao_dos_nichos():
    ficha = {"largura": 0.8, "profundidade": 0.3, "altura": 1.8, "nichos": [0.4, 0.2, 0.2], "fundo": True,
             "cor": "madeira"}
    pecas = pecas_do_codigo(montar([(RECEITAS["estante"], 1, ficha)])[0])
    topo = pecas["Topo"]
    assert topo[4] + topo[1][2] == pytest.approx(1.8)  # o topo termina na altura pedida
    base, p1, p2 = pecas["Base"][4], pecas["Prateleira1"][4], pecas["Prateleira2"][4]
    assert (p1 - base) - receitas.E == pytest.approx(2 * ((p2 - p1) - receitas.E))  # 0,4 : 0,2


def test_cadeiras_em_volta_da_mesa_sem_encostar():
    mesa = {"largura": 1.2, "profundidade": 0.8, "altura": 0.75, "pernas": "quadradas", "cor": "madeira"}
    cadeira = {"largura_assento": 0.45, "profundidade_assento": 0.45, "altura_assento": 0.45,
               "altura_total": 0.9, "bracos": False, "cor": "madeira"}
    codigo, frase = montar([(RECEITAS["mesa"], 1, mesa), (RECEITAS["cadeira"], 2, cadeira)])
    pecas = pecas_do_codigo(codigo)
    assert frase.startswith("Pronto! Criei uma mesa") and "2 cadeiras" in frase
    frente, tras = pecas["Cadeira1_Assento"][3], pecas["Cadeira2_Assento"][3]
    assert frente < -0.4 and tras > 0.4  # um de cada lado, fora do tampo
    # o encosto fica do lado de fora: a cadeira olha para a mesa
    assert pecas["Cadeira1_Encosto"][3] < frente and pecas["Cadeira2_Encosto"][3] > tras
