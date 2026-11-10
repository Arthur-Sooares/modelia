"""Avaliação do Faz pra mim (ficha de peças): como escolher as fichas de exemplo que a LLM imita?

Condições, com o mesmo modelo, pedido e seed:
- palavra: o jeito atual, fichas escolhidas pelo nome do objeto no pedido (pecas.exemplos_para).
- embedding: as mesmas 50 fichas, escolhidas por semelhança de embeddings (RAG sobre a biblioteca).

Não precisa do Blender: avalia a ficha. Para cada pedido há um gabarito com as peças que não podem
faltar; uma LLM juiz (gemma4, offline) marca quais delas a ficha tem. Mede também se a ficha monta
sem erro e o tempo.

    uv run python -m modelia.avaliacao_fichas --repeticoes 3
"""

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from datetime import datetime

from modelia import config, llm, pecas
from modelia.avaliacao import JUIZ
from modelia.exemplos_fichas import EXEMPLOS

PALAVRA, EMBEDDING = "palavra", "embedding"
CONDICOES = [PALAVRA, EMBEDDING]
PREFIXO = "Instruct: Given a request to build a 3D object, retrieve similar objects\nQuery: "

# (pedido do jeito de um aluno, peças que não podem faltar). Rascunho de 10/11/2026, a revisar com o Arthur.
# 1–20: objetos fora da biblioteca. 21–25: parecidos com fichas da biblioteca, mas com outro nome.
PEDIDOS = [
    ("faz uns óculos", ["2 lentes", "ponte entre as lentes", "2 hastes"]),
    ("faz uma bicicleta", ["2 rodas", "quadro", "guidão", "selim"]),
    ("faz uma moto", ["2 rodas", "corpo", "guidão", "banco"]),
    ("faz uma geladeira", ["corpo", "porta", "puxador"]),
    ("faz um fogão", ["corpo", "4 bocas", "porta do forno", "botões"]),
    ("faz uma chaleira", ["corpo", "bico", "alça", "tampa"]),
    ("faz um garfo", ["cabo", "dentes (3 ou 4)"]),
    ("faz um martelo", ["cabo", "cabeça"]),
    ("faz um ventilador de mesa", ["base", "haste", "motor", "pás (3 ou mais)"]),
    ("faz um violão", ["corpo", "braço", "cabeça", "boca"]),
    ("faz um fone de ouvido", ["arco", "2 conchas"]),
    ("faz um escorregador", ["escada", "plataforma", "rampa"]),
    ("faz um balanço", ["2 postes", "travessa", "2 cordas", "assento"]),
    ("faz um barco a vela", ["casco", "mastro", "vela"]),
    ("faz um trem", ["corpo", "cabine", "chaminé", "rodas"]),
    ("faz um castelo", ["muralhas", "4 torres", "telhados das torres", "portão"]),
    ("faz um poste de luz", ["base", "poste", "lâmpada"]),
    ("faz um regador", ["corpo", "bico", "alça"]),
    ("faz uma mochila", ["corpo", "bolso da frente", "2 alças"]),
    ("faz um cachorro", ["corpo", "cabeça", "4 patas", "rabo", "orelhas"]),
    ("faz um tablet", ["corpo fino", "tela"]),
    ("faz uma jarra", ["corpo", "alça", "bico"]),
    ("faz um pufe", ["assento acolchoado sem pernas"]),
    ("faz uma taça de vinho", ["bojo", "haste", "base"]),
    ("faz um beliche", ["2 camas empilhadas", "escada"]),
]

SISTEMA_JUIZ = (
    "Você confere a ficha de peças de um objeto 3D simples, montado com caixas, cilindros, cones e esferas. "
    "Para cada parte da lista de partes obrigatórias, decida se a ficha tem uma peça que faz esse papel. "
    "O nome da peça pode ser diferente (Aro serve de lente; Tampo serve de assento). Quantidades contam: "
    "\"2 hastes\" pede duas peças ou uma peça repetida 2 vezes (repetir_x/y/z). "
    "Em presentes, liste só as partes obrigatórias que a ficha tem, escritas exatamente como na lista."
)


def formato_juiz(partes):
    return {"type": "object", "properties": {
        "justificativa": {"type": "string"},
        "presentes": {"type": "array", "items": {"type": "string", "enum": partes}}},
        "required": ["justificativa", "presentes"]}


def cosseno(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))


def mais_parecidas(vetor, vetores, quantos=3):
    """Índices dos `quantos` vetores mais parecidos, o mais parecido por último (como exemplos_para)."""
    ordem = sorted(range(len(vetores)), key=lambda i: cosseno(vetor, vetores[i]), reverse=True)[:quantos]
    return list(reversed(ordem))


def fichas_por_embedding(pedido, vetores_biblioteca):
    vetor = llm.embeddings([PREFIXO + pedido])[0]
    return [EXEMPLOS[i][1] for i in mais_parecidas(vetor, vetores_biblioteca)]


def resumo_da_ficha(texto):
    """O que o juiz vê: as peças, sem a descrição (que pode citar partes que não viraram peça)."""
    dados = json.loads(texto)
    campos = ("nome", "forma", "posicao", "de", "repetir_x", "repetir_y", "repetir_z")
    return json.dumps([{k: p[k] for k in campos if k in p} for p in dados.get("pecas", [])], ensure_ascii=False)


def julgar(pedido, partes, ficha, conversar=llm.conversar):
    mensagens = [{"role": "system", "content": SISTEMA_JUIZ},
                 {"role": "user", "content": f"Pedido: {pedido}\nPartes obrigatórias: {json.dumps(partes, ensure_ascii=False)}"
                                             f"\nPeças da ficha: {ficha}"}]
    saida = conversar(JUIZ, mensagens, None, {"temperature": 0, "seed": 1}, formato=formato_juiz(partes))
    return json.loads(saida.message.content)


CAMPOS = ["condicao", "modelo", "pedido", "grupo", "repeticao", "exemplos", "segundos", "monta", "erro",
          "pecas", "cobertura", "faltando", "justificativa", "ficha"]


def gravar(arquivo, linhas):
    with arquivo.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=CAMPOS)
        escritor.writeheader()
        escritor.writerows(linhas)


def resumo(linhas):
    grupos = defaultdict(list)
    for linha in linhas:
        grupos[(linha["condicao"], linha["grupo"])].append(linha)
        grupos[(linha["condicao"], "todos")].append(linha)
    saida = []
    for (condicao, grupo), g in sorted(grupos.items()):
        cobertura = sum(float(x["cobertura"]) for x in g) / len(g)
        completas = sum(1 for x in g if float(x["cobertura"]) == 1)
        monta = sum(1 for x in g if x["monta"])
        segundos = sum(float(x["segundos"]) for x in g) / len(g)
        saida.append(f"{condicao:9} {grupo:9} n={len(g):3}  cobertura {cobertura:.0%}  completas {completas:3}  "
                     f"montam {monta:3}  {segundos:5.1f} s")
    return "\n".join(saida)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modelo", default="qwen3.5:4b")
    parser.add_argument("--repeticoes", type=int, default=1)
    parser.add_argument("--temperatura", type=float, default=config.OPCOES_LLM["temperature"])
    args = parser.parse_args()

    config.PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    arquivo = config.PASTA_RESULTADOS / f"avaliacao_fichas_{datetime.now():%Y%m%d_%H%M%S}.csv"
    biblioteca = llm.embeddings([" / ".join(nomes) + ": " + f["descricao"] for nomes, f in EXEMPLOS])
    linhas = []

    for r in range(1, args.repeticoes + 1):
        opcoes = {**config.OPCOES_LLM, "temperature": args.temperatura, "seed": r}
        for n, (pedido, partes) in enumerate(PEDIDOS, 1):
            for condicao in CONDICOES:
                inicio = time.perf_counter()
                fichas = (fichas_por_embedding(pedido, biblioteca) if condicao == EMBEDDING
                          else pecas.exemplos_para(pedido))
                texto = llm.conversar(args.modelo, pecas.mensagens_da_ficha(pedido, fichas), None, opcoes,
                                      formato=pecas.ESQUEMA).message.content or ""
                segundos = round(time.perf_counter() - inicio, 1)
                try:
                    pecas.montar(texto)
                    monta, erro = True, ""
                except Exception as e:  # ficha que não serve conta como falha, não derruba o teste
                    monta, erro = False, str(e)
                linhas.append({"condicao": condicao, "modelo": args.modelo, "pedido": pedido,
                               "grupo": "novo" if n <= 20 else "parecido", "repeticao": r,
                               "exemplos": ", ".join(f["objeto"] for f in fichas), "segundos": segundos,
                               "monta": monta, "erro": erro, "ficha": texto})
                print(f"[{len(linhas)}] {condicao:9} {pedido:28} {segundos:5.1f} s  exemplos: "
                      f"{linhas[-1]['exemplos']}", flush=True)

    gravar(arquivo, linhas)  # as fichas ficam salvas mesmo se o juiz falhar
    partes_de = dict(PEDIDOS)
    for i, linha in enumerate(linhas, 1):
        partes = partes_de[linha["pedido"]]
        try:
            quantas = len(json.loads(linha["ficha"]).get("pecas", []))
            nota = julgar(linha["pedido"], partes, resumo_da_ficha(linha["ficha"]))
            presentes = set(nota["presentes"]) & set(partes)
        except Exception as e:  # ficha que nem é JSON: nenhuma parte
            quantas, nota, presentes = 0, {"justificativa": f"ficha ilegível: {e}"}, set()
        linha |= {"pecas": quantas,
                  "cobertura": round(len(presentes) / len(partes), 3),
                  "faltando": "; ".join(p for p in partes if p not in presentes),
                  "justificativa": nota["justificativa"]}
        print(f"juiz {i}/{len(linhas)}: {linha['condicao']:9} {linha['cobertura']:.0%}  {linha['pedido']}", flush=True)

    gravar(arquivo, linhas)
    print(f"\n{resumo(linhas)}\n\nResultados em {arquivo}")


if __name__ == "__main__":
    main()
