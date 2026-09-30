"""Tarefas-meta do Modelia e a verificação automática de cada uma (RF7, RF10).

A cena chega no formato de `Blender.medir_cena()`:
    {"malhas": [{"nome", "dim" (ordenadas), "local", "z_min", "z_max"}, ...],
     "outros": [{"nome", "tipo", "subtipo", "local"}, ...]}

Cada verificação devolve (nota de 0 a 3, lista do que está certo, lista do que falta).
A nota segue a rubrica do protocolo experimental: 0 nada, 1 errado, 2 parcial, 3 correto.
"""

from collections.abc import Callable
from dataclasses import dataclass


def perto(a, b, tol):
    return abs(a - b) <= tol


def altura(malha):
    return malha["z_max"] - malha["z_min"]


def verificar_cubo(cena):
    malhas = cena["malhas"]
    if not malhas:
        return 0, [], ["Nenhum objeto de malha na cena."]
    cubos = [m for m in malhas if all(perto(d, 2.0, 0.05) for d in m["dim"])]
    if not cubos:
        return 1, [f"{len(malhas)} objeto(s) de malha na cena."], ["Nenhum cubo com 2 m de lado."]
    cubo = cubos[0]
    certo = [f"Cubo '{cubo['nome']}' com 2 m de lado."]
    if perto(cubo["z_min"], 0.0, 0.05):
        return 3, certo + ["O cubo está apoiado no chão (base em z = 0)."], []
    return 2, certo, [f"A base do cubo está em z = {cubo['z_min']}; ela deve ficar no chão (z = 0)."]


def verificar_mesa(cena):
    malhas = cena["malhas"]
    if not malhas:
        return 0, [], ["Nenhum objeto de malha na cena."]
    tampos = [m for m in malhas
              if perto(m["dim"][0], 0.1, 0.03) and perto(m["dim"][1], 1.0, 0.1) and perto(m["dim"][2], 2.0, 0.1)
              and perto(altura(m), 0.1, 0.03)]
    pernas = [m for m in malhas
              if perto(m["dim"][0], 0.1, 0.03) and perto(m["dim"][1], 0.1, 0.03) and 0.6 <= altura(m) <= 0.85]
    certo, falta = [], []
    if tampos:
        certo.append(f"Tampo '{tampos[0]['nome']}' de 2 m x 1 m x 0,1 m, deitado.")
        if any(perto(t["z_max"], 0.8, 0.05) for t in tampos):
            certo.append("A face de cima do tampo está a 0,8 m do chão.")
        else:
            falta.append(f"A face de cima do tampo está a {tampos[0]['z_max']} m; o certo é 0,8 m.")
    else:
        falta.append("Falta o tampo: um cubo achatado de 2 m x 1 m x 0,1 m.")
    no_chao = [p for p in pernas if perto(p["z_min"], 0.0, 0.05)]
    if len(pernas) == 4 and len(no_chao) == 4:
        certo.append("As 4 pernas finas vão do chão até o tampo.")
    elif pernas:
        certo.append(f"{len(pernas)} perna(s) com a medida certa.")
        falta.append(f"A mesa precisa de 4 pernas apoiadas no chão; há {len(no_chao)} no chão.")
    else:
        falta.append("Faltam as 4 pernas: cilindros de raio 0,05 m, do chão até o tampo.")
    if not falta:
        return 3, certo, falta
    return (2 if certo else 1), certo, falta


def verificar_cena_com_luz(cena):
    malhas, outros = cena["malhas"], cena["outros"]
    if not malhas and not outros:
        return 0, [], ["A cena está vazia."]
    certo, falta = [], []
    planos = [m for m in malhas
              if m["dim"][0] <= 0.01 and perto(m["dim"][1], 4.0, 0.2) and perto(m["dim"][2], 4.0, 0.2)]
    esferas = [m for m in malhas if all(perto(d, 1.0, 0.05) for d in m["dim"])]
    luzes = [o for o in outros if o["tipo"] == "LIGHT" and o["subtipo"] == "POINT"]
    if planos:
        certo.append("Chão: um plano de 4 m x 4 m.")
    else:
        falta.append("Falta o chão: um plano de 4 m x 4 m em z = 0.")
    if esferas and perto(esferas[0]["z_min"], 0.0, 0.05):
        certo.append("Esfera de raio 0,5 m apoiada no chão.")
    elif esferas:
        certo.append("Esfera de raio 0,5 m.")
        falta.append(f"A esfera deve encostar no chão; a base dela está em z = {esferas[0]['z_min']}.")
    else:
        falta.append("Falta a esfera de raio 0,5 m.")
    esfera = esferas[0] if esferas else None
    if luzes and esfera and any(luz["local"][2] > esfera["z_max"] for luz in luzes):
        certo.append("Luz pontual acima da esfera.")
    elif luzes:
        certo.append("Existe uma luz pontual.")
        falta.append("A luz pontual deve ficar acima da esfera.")
    else:
        falta.append("Falta uma luz do tipo Point acima da esfera.")
    if not falta:
        return 3, certo, falta
    return (2 if certo else 1), certo, falta


@dataclass(frozen=True)
class Tarefa:
    id: str
    nome: str
    pedido: str  # o texto enviado à LLM no experimento e no Demonstrar
    verificar: Callable


TAREFAS = {
    t.id: t for t in [
        Tarefa("cubo", "Cubo no chão",
               "Crie um cubo de 2 m de lado apoiado no chão, com a base em z = 0.",
               verificar_cubo),
        Tarefa("mesa", "Mesa simples",
               "Crie uma mesa simples: um tampo retangular (cubo achatado) de 2 m x 1 m x 0,1 m, com a "
               "face de cima a 0,8 m do chão, e quatro pernas cilíndricas finas (raio 0,05 m) nos cantos, "
               "indo do chão até o tampo.",
               verificar_mesa),
        Tarefa("cena_luz", "Cena com luz",
               "Crie um plano de 4 m x 4 m como chão em z = 0, uma esfera de raio 0,5 m apoiada sobre ele "
               "e uma luz do tipo Point acima da esfera.",
               verificar_cena_com_luz),
    ]
}
