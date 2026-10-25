"""Avaliação do modo Explicar: o manual (RAG) melhora a resposta? Ablação pedida pelo orientador em 08/10/2026.

Duas condições, com o mesmo modelo, pergunta e seed:
- puro: a LLM responde só com o prompt básico do tutor, sem o manual.
- modelia: o modo Explicar inteiro (reescrita da dúvida, busca no manual do Blender, recusa fora do material).

Uma LLM juiz (gemma4, offline) dá a nota de 0 a 3 comparando cada resposta com o gabarito, sem saber a condição.
Perguntas de fora do Blender não vão ao juiz: o certo é recusar, e isso se confere direto.

    uv run python -m modelia.avaliacao                       # respostas (qwen) e notas (gemma4)
    uv run python -m modelia.avaliacao --repeticoes 3

As respostas de todas as execuções saem primeiro e as notas depois: assim o Ollama troca de modelo uma vez só.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime

from modelia import agente, config, llm
from modelia.banco import Banco
from modelia.conhecimento import Conhecimento

PURO, MODELIA = "puro", "modelia"
CONDICOES = [PURO, MODELIA]
JUIZ = "gemma4:12b-it-qat"  # o qwen3.5:4b julgou mal nos testes de 18/10/2026
FORA = ""  # gabarito vazio: pergunta de fora do Blender, o certo é recusar

# (pergunta do jeito de um aluno iniciante, gabarito com o que a resposta precisa ter)
PERGUNTAS = [
    ("como eu puxo uma face pra fora?", "Extrusão (Extrude), atalho E, no modo de edição com a face selecionada."),
    ("como arredondo as quinas de um cubo?", "Chanfro (Bevel): Ctrl+B no modo de edição, ou o modificador Bevel."),
    ("como deixo o objeto maior?", "Escala (Scale), atalho S, e mover o mouse ou digitar o valor."),
    ("como copio um objeto?", "Duplicar (Duplicate), atalho Shift+D."),
    ("como faço um buraco num objeto usando outro?", "Modificador Booleano (Boolean) com a operação Diferença."),
    ("como deixo meu modelo mais liso, menos quadrado?",
     "Modificador Subdivisão de Superfície (Subdivision Surface) e/ou Sombreamento Suave (Shade Smooth)."),
    ("como entro no modo de edição?", "Tecla Tab com o objeto selecionado (ou o menu de modos no cabeçalho)."),
    ("como faço vários objetos iguais enfileirados?", "Modificador Matriz (Array), com a contagem e o deslocamento."),
    ("como faço o lado esquerdo igual ao direito sem modelar duas vezes?",
     "Modificador Espelhar (Mirror), escolhendo o eixo."),
    ("como coloco mais um corte em volta do meu cubo?", "Corte em Laço (Loop Cut), atalho Ctrl+R, no modo de edição."),
    ("como junto dois objetos em um só?", "Unir (Join), atalho Ctrl+J, com os dois objetos selecionados."),
    ("como faço uma face menor dentro de outra face?", "Inserir faces (Inset), atalho I, no modo de edição."),
    ("como fecho um buraco criando uma face entre os vértices?",
     "Preencher / criar face (Fill), atalho F, com os vértices ou arestas selecionados."),
    ("como mexo o objeto só pra cima?", "Mover (G) e depois a tecla Z para travar no eixo Z."),
    ("como vejo o modelo de cima?", "Vista de cima (Top) com a tecla 7 do teclado numérico."),
    ("como seleciono tudo?", "Tecla A seleciona tudo (Alt+A tira a seleção)."),
    ("como apago um objeto?", "Tecla X (ou Delete) e confirmar Excluir."),
    ("como coloco cor no meu objeto?",
     "Criar um Material na aba Propriedades do Material e mudar a Cor Base (Base Color)."),
    ("como mudo o ponto de origem do objeto?",
     "Menu Objeto > Definir Origem (Set Origin), por exemplo Origem para Geometria."),
    ("depois que estiquei o objeto, como deixo a escala 1 de novo sem mudar o tamanho?",
     "Aplicar a Escala: Ctrl+A > Escala (Apply Scale)."),
    ("qual é a capital da França?", FORA),
    ("como faço uma planilha no Excel?", FORA),
    ("me passa uma receita de bolo de cenoura", FORA),
]

SISTEMA_JUIZ = (
    "Você avalia respostas de um tutor de Blender 5.1 para alunos iniciantes. "
    "Compare a resposta com o gabarito e dê uma nota:\n"
    "3 = correta e traz o que o gabarito pede (ferramenta certa e o jeito de usar).\n"
    "2 = parcialmente correta: ferramenta certa, mas falta algo importante ou tem um erro pequeno.\n"
    "1 = errada: ferramenta errada, atalho errado ou passos que não funcionam.\n"
    "0 = não respondeu (recusou, disse que não sabe ou ficou vazia).\n"
    "Outros caminhos que também funcionam no Blender valem como corretos. "
    "Marque inventou = true se a resposta afirmar algo falso sobre o Blender (menu, atalho ou comando que não existe)."
)
FORMATO_JUIZ = {
    "type": "object",
    "properties": {"justificativa": {"type": "string"}, "nota": {"type": "integer", "enum": [0, 1, 2, 3]},
                   "inventou": {"type": "boolean"}},
    "required": ["justificativa", "nota", "inventou"],
}

CAMPOS = ["condicao", "modelo", "pergunta", "repeticao", "seed", "fora", "recusou", "segundos",
          "nota_0a3", "inventou", "justificativa", "resposta"]


def sem_fontes(texto):
    """O juiz não pode ver a lista de fontes: ela entrega qual condição respondeu."""
    return texto.split("\n\n**Fontes:**")[0].strip()


def recusou(texto):
    return not texto.strip() or texto.startswith("O material da disciplina não cobre")


def responder_puro(modelo, pergunta, opcoes, conversar=llm.conversar):
    mensagens = [{"role": "system", "content": agente.BASE}, {"role": "user", "content": pergunta}]
    return conversar(modelo, mensagens, None, opcoes).message.content or ""


def responder_modelia(ag, banco, modelo, pergunta):
    estado = agente.Estado(banco.iniciar_sessao("AVALIACAO"), modelo, modo=agente.EXPLICAR)
    return sem_fontes(ag.responder(estado, pergunta).texto)


def julgar(pergunta, gabarito, resposta, conversar=llm.conversar):
    """Nota do juiz: dicionário com nota (0 a 3), inventou e justificativa."""
    mensagens = [{"role": "system", "content": SISTEMA_JUIZ},
                 {"role": "user", "content": f"Pergunta do aluno: {pergunta}\nGabarito: {gabarito}\n"
                                             f"Resposta do tutor:\n{resposta}"}]
    saida = conversar(JUIZ, mensagens, None, {"temperature": 0, "seed": 1}, formato=FORMATO_JUIZ)
    return json.loads(saida.message.content)


def nota_da_linha(linha, gabarito, conversar=llm.conversar):
    """Fora do Blender: 3 se recusou, 1 se respondeu mesmo assim. No Blender: recusa = 0, senão o juiz decide."""
    if linha["fora"]:
        return {"nota": 3 if linha["recusou"] else 1, "inventou": False,
                "justificativa": "recusou a pergunta de fora" if linha["recusou"] else "respondeu fora do Blender"}
    if linha["recusou"]:
        return {"nota": 0, "inventou": False, "justificativa": "recusou uma pergunta do Blender"}
    return julgar(linha["pergunta"], gabarito, linha["resposta"], conversar)


def resumo(linhas):
    """Média das notas por condição, separando perguntas do Blender e de fora. Texto pronto para imprimir."""
    grupos = defaultdict(list)
    for linha in linhas:
        grupos[(linha["condicao"], "fora" if linha["fora"] else "blender")].append(linha)
    saida = []
    for (condicao, tipo), grupo in sorted(grupos.items()):
        notas = [int(g["nota_0a3"]) for g in grupo]
        inventou = sum(1 for g in grupo if g["inventou"] in (True, "True"))
        saida.append(f"{condicao:8} {tipo:8} n={len(grupo):3}  nota média {sum(notas) / len(notas):.2f}  "
                     f"notas 3: {notas.count(3):3}  inventou: {inventou}")
    return "\n".join(saida)


def gravar(arquivo, linhas):
    with arquivo.open("w", newline="", encoding="utf-8") as saida:
        escritor = csv.DictWriter(saida, fieldnames=CAMPOS)
        escritor.writeheader()
        escritor.writerows(linhas)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modelo", default="qwen3.5:4b")
    parser.add_argument("--repeticoes", type=int, default=1)
    parser.add_argument("--temperatura", type=float, default=config.OPCOES_LLM["temperature"])
    args = parser.parse_args()

    config.PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    arquivo = config.PASTA_RESULTADOS / f"avaliacao_explicar_{carimbo}.csv"
    banco = Banco(config.PASTA_RESULTADOS / f"avaliacao_explicar_{carimbo}.sqlite3")
    gabaritos = dict(PERGUNTAS)
    linhas = []

    for r in range(1, args.repeticoes + 1):
        opcoes = {**config.OPCOES_LLM, "temperature": args.temperatura, "seed": r}

        def conversar_com_seed(modelo, mensagens, ferramentas=None, _opcoes=None, opcoes=opcoes, **extras):
            return llm.conversar(modelo, mensagens, ferramentas, opcoes, **extras)  # mesma seed do puro

        ag = agente.Agente(None, Conhecimento(), banco, conversar=conversar_com_seed)
        for pergunta, gabarito in PERGUNTAS:
            for condicao in CONDICOES:
                inicio = datetime.now()
                resposta = (responder_modelia(ag, banco, args.modelo, pergunta) if condicao == MODELIA
                            else responder_puro(args.modelo, pergunta, opcoes))
                linhas.append({"condicao": condicao, "modelo": args.modelo, "pergunta": pergunta, "repeticao": r,
                               "seed": r, "fora": gabarito == FORA, "recusou": recusou(resposta),
                               "segundos": round((datetime.now() - inicio).total_seconds(), 1),
                               "resposta": resposta.strip()})
                print(f"[{len(linhas)}] {condicao:8} {pergunta[:50]}", flush=True)

    gravar(arquivo, linhas)  # as respostas ficam salvas mesmo se o juiz falhar
    for i, linha in enumerate(linhas, 1):
        nota = nota_da_linha(linha, gabaritos[linha["pergunta"]])
        linha |= {"nota_0a3": nota["nota"], "inventou": nota["inventou"], "justificativa": nota["justificativa"]}
        print(f"juiz {i}/{len(linhas)}: {linha['condicao']:8} nota {nota['nota']}  {linha['pergunta'][:40]}",
              flush=True)

    gravar(arquivo, linhas)
    print(f"\n{resumo(linhas)}\n\nResultados em {arquivo}")


if __name__ == "__main__":
    main()
