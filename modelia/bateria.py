"""Bateria de testes do modo Demonstrar: pedidos de objetos do dia a dia, com conferência automática.

Mede se a LLM acerta o objeto pedido (forma, medidas, peças) e quanto tempo leva, para comparar
versões do Demonstrar antes e depois de cada mudança. Não aparece para o aluno: as tarefas-meta
do modo Verificar continuam em tarefas.py.

    uv run python -m modelia.bateria                     # todos os pedidos, uma vez
    uv run python -m modelia.bateria --tarefas cadeira,cama --repeticoes 3

A cena chega no formato de `Blender.medir_cena()`. Cada critério olha as caixas alinhadas aos
eixos das peças (xy e z_min/z_max), o que basta para móveis; uma peça girada ocupa uma caixa maior.
"""

import argparse
import csv
from datetime import datetime

from modelia import config
from modelia.agente import problemas_da_cena
from modelia.banco import Banco
from modelia.experimento import MODELIA, executar_uma
from modelia.tarefas import Tarefa

FINA = 0.06  # espessura máxima de uma placa (tampo, prateleira, porta)


# --- medidas de uma peça e do conjunto ---

def extensao(m):
    """(largura em X, profundidade em Y, altura em Z) da caixa da peça."""
    x0, x1, y0, y1 = m["xy"]
    return x1 - x0, y1 - y0, m["z_max"] - m["z_min"]


def volume(m):
    lx, ly, lz = extensao(m)
    return lx * ly * lz


def conjunto(malhas):
    """Caixa de todas as peças juntas: (x0, x1, y0, y1, z0, z1)."""
    return (min(m["xy"][0] for m in malhas), max(m["xy"][1] for m in malhas),
            min(m["xy"][2] for m in malhas), max(m["xy"][3] for m in malhas),
            min(m["z_min"] for m in malhas), max(m["z_max"] for m in malhas))


def tamanho(malhas):
    """(lado menor no chão, lado maior no chão, altura) do conjunto."""
    x0, x1, y0, y1, z0, z1 = conjunto(malhas)
    menor, maior = sorted((x1 - x0, y1 - y0))
    return menor, maior, z1 - z0


def sobreposicao(a, b):
    """Volume em que as caixas de a e b se cruzam."""
    lados = []
    for i, j in ((0, 1), (2, 3)):
        lados.append(min(a["xy"][j], b["xy"][j]) - max(a["xy"][i], b["xy"][i]))
    lados.append(min(a["z_max"], b["z_max"]) - max(a["z_min"], b["z_min"]))
    return 0.0 if min(lados) <= 0 else lados[0] * lados[1] * lados[2]


def pecas_sobrepostas(malhas, fracao=0.5):
    """Pares em que mais da `fracao` da peça menor está dentro da outra (encaixe pequeno não conta)."""
    pares = []
    for i, a in enumerate(malhas):
        for b in malhas[i + 1:]:
            menor = min(volume(a), volume(b))
            if menor > 0 and sobreposicao(a, b) > fracao * menor:
                pares.append((a["nome"], b["nome"]))
    return pares


def placas_horizontais(malhas, lado_min=0.15):
    lista = []
    for m in malhas:
        lx, ly, lz = extensao(m)
        if lz <= FINA and min(lx, ly) >= lado_min:
            lista.append(m)
    return sorted(lista, key=lambda m: m["z_min"])


def placas_verticais(malhas, altura_min):
    """Placas em pé (porta, lateral, encosto): finas em X ou em Y."""
    lista = []
    for m in malhas:
        lx, ly, lz = extensao(m)
        if lz >= altura_min and min(lx, ly) <= FINA:
            lista.append(m)
    return lista


def pernas(malhas, altura_min, grossura_max=0.12):
    """Peças finas em pé que encostam no chão."""
    lista = []
    for m in malhas:
        lx, ly, lz = extensao(m)
        if lz >= altura_min and max(lx, ly) <= grossura_max and m["z_min"] <= 0.02:
            lista.append(m)
    return lista


def area(m):
    lx, ly, _ = extensao(m)
    return lx * ly


def entre(valor, minimo, maximo):
    return minimo <= valor <= maximo


# --- critérios reaproveitados ---

def altura_entre(minimo, maximo):
    return (f"altura total entre {minimo} e {maximo} m", lambda ms: entre(tamanho(ms)[2], minimo, maximo))


def oca():
    """Nenhuma peça ocupa boa parte do volume total: móvel feito de placas, não um bloco."""
    def conferir(ms):
        x0, x1, y0, y1, z0, z1 = conjunto(ms)
        total = (x1 - x0) * (y1 - y0) * (z1 - z0)
        return total > 0 and all(volume(m) < 0.4 * total for m in ms)
    return "é oca (feita de placas, não um bloco maciço)", conferir


def pelo_menos(n, texto, funcao):
    return f"pelo menos {n} {texto}", lambda ms: len(funcao(ms)) >= n


def tampo_no_alto(altura_min, altura_max, area_min):
    def conferir(ms):
        return any(entre(m["z_max"], altura_min, altura_max) and area(m) >= area_min
                   for m in placas_horizontais(ms))
    return f"tampo fino com a face de cima entre {altura_min} e {altura_max} m", conferir


def vaos_diferentes(ms):
    alturas = [p["z_min"] for p in placas_horizontais(ms)]
    vaos = [b - a for a, b in zip(alturas, alturas[1:])]
    return len(vaos) >= 2 and max(vaos) - min(vaos) >= 0.05


def avaliar(*criterios):
    """Verificação no formato de tarefas.py: nota 0 a 3, o que está certo e o que falta.

    Todo pedido também confere que nada flutua nem fica abaixo do chão e que nenhuma peça
    está quase toda dentro de outra. 3 = tudo certo; 2 = pelo menos metade; 1 = criou algo errado.
    """
    def verificar(cena):
        malhas = cena["malhas"]
        if not malhas:
            return 0, [], ["Nenhum objeto de malha na cena."]
        certo, falta = [], []
        apoio = problemas_da_cena(cena)
        (falta if apoio else certo).append("; ".join(apoio) if apoio else "nada flutua nem fica abaixo do chão")
        pares = pecas_sobrepostas(malhas)
        if pares:
            falta.append("peças uma dentro da outra: " + ", ".join(f"{a} e {b}" for a, b in pares[:4]))
        else:
            certo.append("nenhuma peça dentro de outra")
        for texto, conferir in criterios:
            (certo if conferir(malhas) else falta).append(texto)
        if not falta:
            return 3, certo, falta
        return (2 if len(certo) >= len(falta) else 1), certo, falta
    return verificar


def perto_de(a, b, tol):
    return abs(a - b) <= tol


def empilhadas_diminuindo(ms):
    """As 3 maiores peças estão uma sobre a outra, cada uma mais estreita que a de baixo."""
    bolas = sorted(sorted(ms, key=volume, reverse=True)[:3], key=lambda m: m["z_min"])
    if len(bolas) < 3:
        return False
    larguras = [max(extensao(b)[:2]) for b in bolas]
    return larguras[0] > larguras[1] > larguras[2] and bolas[0]["z_max"] <= bolas[2]["z_min"] + 0.05


def gaveteiro_a_direita(ms):
    x0, x1, *_ = conjunto(ms)
    meio = (x0 + x1) / 2
    return any(extensao(m)[2] >= 0.4 and min(extensao(m)[:2]) >= 0.3 and (m["xy"][0] + m["xy"][1]) / 2 > meio
               and m["z_max"] <= 0.8 for m in ms)


PEDIDOS = [
    Tarefa("estante_4", "Estante com 4 prateleiras", "me faça uma estante com 4 prateleiras", avaliar(
        altura_entre(1.2, 2.2), oca(), pelo_menos(4, "prateleiras finas", placas_horizontais))),
    Tarefa("estante_variada", "Estante para coisas variadas",
           "Gostaria de montar uma estante para coisas varias, de tamanhos diferentes. Livros, estatuetas, "
           "objetos decorativos, tralhas do dia a dia", avaliar(
               altura_entre(1.0, 2.2), oca(), pelo_menos(3, "prateleiras finas", placas_horizontais),
               ("nichos de alturas diferentes", vaos_diferentes))),
    Tarefa("estante_medidas", "Estante com medidas dadas",
           "Crie uma estante de 1 m de largura, 2 m de altura e 40 cm de profundidade, com 5 prateleiras.", avaliar(
               ("medidas 1 x 0,4 x 2 m", lambda ms: all(perto_de(a, b, 0.05) for a, b in zip(tamanho(ms), (0.4, 1.0, 2.0)))),
               oca(), pelo_menos(5, "prateleiras finas", placas_horizontais))),
    Tarefa("mesa_jantar", "Mesa de jantar", "Crie uma mesa de jantar para 6 pessoas.", avaliar(
        altura_entre(0.7, 0.8), ("comprimento entre 1,6 e 2,4 m", lambda ms: entre(tamanho(ms)[1], 1.6, 2.4)),
        tampo_no_alto(0.7, 0.8, 1.0), pelo_menos(4, "pernas no chão", lambda ms: pernas(ms, 0.5)))),
    Tarefa("cadeira", "Cadeira", "Faça uma cadeira de madeira.", avaliar(
        altura_entre(0.75, 1.1), tampo_no_alto(0.4, 0.52, 0.12),
        pelo_menos(4, "pernas no chão", lambda ms: pernas(ms, 0.3)),
        ("encosto em pé acima do assento", lambda ms: any(
            p["z_min"] >= 0.35 and p["z_max"] >= 0.7 for p in placas_verticais(ms, 0.2))))),
    Tarefa("banqueta", "Banqueta de bar", "Crie uma banqueta alta de bar, redonda.", avaliar(
        altura_entre(0.6, 0.85), tampo_no_alto(0.6, 0.85, 0.06),
        pelo_menos(1, "perna ou coluna no chão", lambda ms: pernas(ms, 0.4)))),
    Tarefa("cama", "Cama de solteiro", "Crie uma cama de solteiro com cabeceira.", avaliar(
        ("cerca de 1,9 x 0,9 m no chão", lambda ms: entre(tamanho(ms)[0], 0.8, 1.1) and entre(tamanho(ms)[1], 1.8, 2.2)),
        ("colchão de 12 a 30 cm de espessura, a uma altura de cama", lambda ms: any(
            entre(extensao(m)[2], 0.12, 0.3) and entre(m["z_max"], 0.35, 0.75) and area(m) >= 1.4 for m in ms)),
        ("cabeceira em pé acima do colchão", lambda ms: any(p["z_max"] >= 0.8 for p in placas_verticais(ms, 0.3))))),
    Tarefa("criado_mudo", "Criado-mudo", "Faça um criado-mudo com uma gaveta.", avaliar(
        altura_entre(0.45, 0.75), ("cerca de 0,3 a 0,6 m de cada lado", lambda ms: entre(tamanho(ms)[0], 0.3, 0.6)
                                   and entre(tamanho(ms)[1], 0.3, 0.7)),
        pelo_menos(3, "peças (corpo, frente da gaveta, puxador)", lambda ms: ms))),
    Tarefa("guarda_roupa", "Guarda-roupa", "Crie um guarda-roupa de duas portas.", avaliar(
        altura_entre(1.6, 2.3), ("largura entre 0,8 e 1,6 m", lambda ms: entre(tamanho(ms)[1], 0.8, 1.6)),
        pelo_menos(2, "portas (placas finas em pé)", lambda ms: placas_verticais(ms, 1.2)))),
    Tarefa("sofa", "Sofá", "Faça um sofá de três lugares.", avaliar(
        altura_entre(0.65, 1.0), ("comprimento entre 1,7 e 2,5 m", lambda ms: entre(tamanho(ms)[1], 1.7, 2.5)),
        ("assento entre 0,35 e 0,55 m do chão", lambda ms: sum(
            area(m) for m in ms if entre(m["z_max"], 0.35, 0.55)) >= 0.8),
        ("encosto alto ao longo do sofá", lambda ms: any(
            m["z_max"] >= 0.65 and max(extensao(m)[:2]) >= 1.2 and m["z_min"] >= 0.2 for m in ms)))),
    Tarefa("luminaria", "Luminária de mesa", "Crie uma luminária de mesa.", avaliar(
        altura_entre(0.3, 0.7), pelo_menos(3, "peças (base, haste, cúpula)", lambda ms: ms),
        ("haste fina", lambda ms: any(max(extensao(m)[:2]) <= 0.04 and extensao(m)[2] >= 0.15 for m in ms)),
        ("cúpula larga na metade de cima", lambda ms: any(
            m["z_min"] >= tamanho(ms)[2] / 2 and max(extensao(m)[:2]) >= 0.12 for m in ms)))),
    Tarefa("caneca", "Caneca", "Faça uma caneca de café.", avaliar(
        altura_entre(0.07, 0.14), pelo_menos(2, "peças (corpo e alça)", lambda ms: ms))),
    Tarefa("mesa_cadeiras", "Mesa com duas cadeiras", "Crie uma mesa com duas cadeiras.", avaliar(
        tampo_no_alto(0.7, 0.8, 0.5),
        pelo_menos(2, "assentos de cadeira entre 0,4 e 0,52 m", lambda ms: [
            p for p in placas_horizontais(ms) if entre(p["z_max"], 0.4, 0.52) and entre(area(p), 0.1, 0.4)]))),
    Tarefa("boneco_neve", "Boneco de neve", "Faça um boneco de neve.", avaliar(
        altura_entre(0.5, 2.5), ("três bolas empilhadas, cada uma menor que a de baixo", empilhadas_diminuindo))),
    Tarefa("vaso_planta", "Vaso com planta", "Crie um vaso com uma planta.", avaliar(
        altura_entre(0.2, 1.5), pelo_menos(2, "peças (vaso e planta)", lambda ms: ms),
        ("planta acima da boca do vaso", lambda ms: any(m["z_min"] >= 0.1 for m in ms)))),
    Tarefa("escrivaninha", "Escrivaninha com gaveteiro", "Crie uma escrivaninha com gaveteiro do lado direito.", avaliar(
        altura_entre(0.7, 0.8), ("comprimento entre 1,0 e 1,7 m", lambda ms: entre(tamanho(ms)[1], 1.0, 1.7)),
        tampo_no_alto(0.7, 0.8, 0.5), ("gaveteiro embaixo do tampo, do lado direito (+X)", gaveteiro_a_direita))),
]


TAREFAS_BATERIA = {t.id: t for t in PEDIDOS}
CAMPOS = ["data", "tarefa", "repeticao", "modelo", "seed", "temperatura", "nota_0a3", "avaliacao",
          "chamadas_ferramenta", "erros_execucao", "segundos", "objetos_criados", "captura",
          "resposta_final", "codigo_gerado"]


def rodar(blender, modelo, ids, repeticoes=1, temperatura=None, seed=1):
    """Roda os pedidos e grava resultados/bateria_<data>.csv. Gerador: devolve cada linha ao terminar."""
    temperatura = config.OPCOES_LLM["temperature"] if temperatura is None else temperatura
    config.PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    arquivo_csv = config.PASTA_RESULTADOS / f"bateria_{carimbo}.csv"
    capturas = config.PASTA_RESULTADOS / f"bateria_{carimbo}"
    capturas.mkdir()
    banco = Banco(config.PASTA_RESULTADOS / f"bateria_{carimbo}.sqlite3")
    with arquivo_csv.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=CAMPOS, extrasaction="ignore")
        escritor.writeheader()
        for r in range(1, repeticoes + 1):
            for tarefa_id in ids:
                linha = {"data": datetime.now().isoformat(timespec="seconds"), "repeticao": r}
                try:
                    linha |= executar_uma(blender, modelo, TAREFAS_BATERIA[tarefa_id], seed + r - 1,
                                          temperatura, MODELIA, banco)
                    imagem = blender.captura()
                    if imagem:
                        linha["captura"] = f"{tarefa_id}_r{r}.png"
                        imagem.save(capturas / linha["captura"])
                except Exception as erro:
                    linha |= {"tarefa": tarefa_id, "modelo": modelo, "nota_0a3": 0,
                              "avaliacao": f"falha: {erro}"[:300]}
                escritor.writerow(linha)
                arquivo.flush()
                yield linha, arquivo_csv


def resumo(linhas):
    notas = [int(l["nota_0a3"]) for l in linhas]
    tempos = [float(l["segundos"]) for l in linhas if l.get("segundos")]
    acertos = sum(n == 3 for n in notas)
    return (f"{acertos} de {len(notas)} corretos ({100 * acertos / max(len(notas), 1):.0f}%), "
            f"nota média {sum(notas) / max(len(notas), 1):.2f}, "
            f"tempo médio {sum(tempos) / max(len(tempos), 1):.0f} s")


def main():
    from modelia.blender import Blender

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tarefas", default=",".join(TAREFAS_BATERIA), help="ids separados por vírgula")
    parser.add_argument("--repeticoes", type=int, default=1)
    parser.add_argument("--modelo", default=config.MODELO_PADRAO)
    args = parser.parse_args()
    if not config.CENA_BASE.exists():
        raise SystemExit(f"Crie a cena base antes ({config.CENA_BASE}).")
    linhas = []
    for linha, arquivo in rodar(Blender(), args.modelo, args.tarefas.split(","), args.repeticoes):
        linhas.append(linha)
        print(f"{linha['tarefa']:<16} nota {linha['nota_0a3']}  {linha.get('segundos', '-'):>6} s  "
              f"{linha['avaliacao'][:110]}", flush=True)
    print(f"\n{resumo(linhas)}\nResultados em {arquivo}")


if __name__ == "__main__":
    main()
