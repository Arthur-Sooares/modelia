from modelia.bateria import TAREFAS_BATERIA, pecas_sobrepostas


def peca(nome, x0, x1, y0, y1, z0, z1):
    dim = sorted(round(d, 3) for d in (x1 - x0, y1 - y0, z1 - z0))
    return {"nome": nome, "dim": dim, "local": [(x0 + x1) / 2, (y0 + y1) / 2, z0],
            "z_min": z0, "z_max": z1, "xy": [x0, x1, y0, y1]}


def estante_oca(vaos):
    pecas = [peca("LateralE", -0.4, -0.38, -0.15, 0.15, 0, 1.8), peca("LateralD", 0.38, 0.4, -0.15, 0.15, 0, 1.8),
             peca("Fundo", -0.38, 0.38, 0.13, 0.15, 0, 1.8)]
    z = 0.0
    for i, vao in enumerate(vaos):
        pecas.append(peca(f"Prateleira{i}", -0.38, 0.38, -0.15, 0.13, z, z + 0.02))
        z += 0.02 + vao
    return {"malhas": pecas, "outros": []}


def test_estante_oca_com_vaos_diferentes_esta_certa():
    nota, certo, falta = TAREFAS_BATERIA["estante_variada"].verificar(estante_oca([0.45, 0.35, 0.3, 0.25, 0.25]))
    assert nota == 3, falta


def test_estante_de_vaos_iguais_perde_o_criterio():
    nota, _, falta = TAREFAS_BATERIA["estante_variada"].verificar(estante_oca([0.35] * 5))
    assert nota == 2 and falta == ["nichos de alturas diferentes"]


def test_estante_macica_do_dia_21_e_reprovada():
    # A cena real de 21/10/2026: corpo maciço e "prateleiras" com a posição Z no lugar da espessura.
    cena = {"outros": [], "malhas": [
        peca("CorpoEstante", -0.4, 0.4, -0.15, 0.15, 0, 1.85), peca("BaseEstante", -0.4, 0.4, -0.15, 0.15, 0, 1.75),
        *[peca(f"Prateira_{i}", -0.4, 0.4, -0.13, 0.13, z - 0.01, z - 0.01 + z) for i, z in
          enumerate([0.35, 0.7, 1.05, 1.4], 1)]]}
    nota, _, falta = TAREFAS_BATERIA["estante_4"].verificar(cena)
    assert nota == 1
    assert any("dentro" in f for f in falta) and "é oca (feita de placas, não um bloco maciço)" in falta


def test_encaixe_pequeno_nao_conta_como_sobreposicao():
    tampo = peca("Tampo", -0.7, 0.7, -0.4, 0.4, 0.71, 0.75)
    perna = peca("Perna", -0.65, -0.59, -0.35, -0.29, 0, 0.72)
    assert pecas_sobrepostas([tampo, perna]) == []


def test_cena_vazia_nota_zero():
    assert TAREFAS_BATERIA["cadeira"].verificar({"malhas": [], "outros": []})[0] == 0
