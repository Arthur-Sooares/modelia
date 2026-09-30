"""Experimento de escolha da LLM (RF10, etapa B3), com base no protocolo experimental.

Cada execução: reabre a cena base limpa, envia a tarefa, deixa a LLM trabalhar sozinha,
mede a cena, dá a nota automática e grava uma linha no CSV.

Aqui não há confirmação passo a passo: o clique do pesquisador em "Iniciar" autoriza
a execução automática, sempre sobre a cópia limpa da cena base.
"""

import csv
import time
from datetime import datetime

from modelia import config, llm
from modelia.blender import FERRAMENTA_EXECUTAR, FERRAMENTAS_LEITURA
from modelia.tarefas import TAREFAS

SISTEMA = (
    "Você é o Modelia, um assistente que modela no Blender 5.1. "
    "Use a ferramenta execute_blender_code com código Python (bpy) para criar os objetos. "
    "Para devolver dados, atribua um dicionário à variável result. "
    "Quando terminar, responda em português com um resumo curto do que criou."
)

CAMPOS = ["data", "modelo", "tarefa", "repeticao", "seed", "temperatura", "nota_0a3", "avaliacao",
          "chamadas_ferramenta", "erros_execucao", "segundos", "objetos_criados", "captura",
          "resposta_final", "codigo_gerado"]

CODIGO_CENA_BASE = """
import bpy
for obj in list(bpy.context.scene.objects):
    if obj.type == 'MESH':
        bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath={caminho!r}, copy=True)
result = {{"salvo_em": {caminho!r}, "objetos": [o.name for o in bpy.context.scene.objects]}}
"""


def criar_cena_base(blender, caminho=config.CENA_BASE):
    """Salva uma cópia da cena aberta, sem as malhas, como ponto de partida limpo."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return blender.rodar_codigo_interno(CODIGO_CENA_BASE.format(caminho=str(caminho)))


def planejar(modelos, tarefas, repeticoes, seeds):
    """Lista de execuções (modelo, tarefa, repetição, seed). As seeds se repetem em ciclo."""
    seeds = seeds or [1]
    return [(modelo, tarefa, r + 1, seeds[r % len(seeds)])
            for modelo in modelos for tarefa in tarefas for r in range(repeticoes)]


def executar_uma(blender, modelo, tarefa, seed, temperatura, conversar=llm.conversar):
    """Uma execução completa. Devolve o dicionário da linha do CSV (sem data e captura)."""
    blender.abrir_cena(config.CENA_BASE)
    ferramentas = blender.ferramentas(FERRAMENTAS_LEITURA | {FERRAMENTA_EXECUTAR})
    opcoes = {**config.OPCOES_LLM, "temperature": temperatura, "seed": seed}
    mensagens = [{"role": "system", "content": SISTEMA}, {"role": "user", "content": tarefa.pedido}]
    chamadas, erros, codigos, final = 0, 0, [], ""
    inicio = time.perf_counter()

    for _ in range(config.MAX_RODADAS):
        mensagem = conversar(modelo, mensagens, ferramentas, opcoes).message
        mensagens.append(mensagem)
        if not mensagem.tool_calls:
            final = mensagem.content or ""
            break
        for chamada in mensagem.tool_calls:
            nome, argumentos = chamada.function.name, dict(chamada.function.arguments)
            chamadas += 1
            if nome == FERRAMENTA_EXECUTAR:
                codigos.append(str(argumentos.get("code", "")))
            if nome in FERRAMENTAS_LEITURA | {FERRAMENTA_EXECUTAR}:
                saida, teve_erro = blender.chamar(nome, argumentos)
            else:
                saida, teve_erro = f"Ferramenta '{nome}' não está disponível.", True
            erros += int(teve_erro)
            mensagens.append({"role": "tool", "content": saida[:4000], "tool_name": nome})

    segundos = time.perf_counter() - inicio
    cena = blender.medir_cena()
    nota, certo, falta = tarefa.verificar(cena)
    return {
        "modelo": modelo, "tarefa": tarefa.id, "seed": seed, "temperatura": temperatura,
        "nota_0a3": nota, "avaliacao": "; ".join(falta) or "correto",
        "chamadas_ferramenta": chamadas, "erros_execucao": erros, "segundos": round(segundos, 1),
        "objetos_criados": len(cena["malhas"]), "resposta_final": final.strip()[:500],
        "codigo_gerado": "\n\n# ---\n\n".join(codigos)[:4000],
    }


def rodar(blender, modelos, tarefas_ids, repeticoes, temperatura, seeds, deve_parar=lambda: False):
    """Gerador: roda o plano todo e devolve (feitas, total, linha) depois de cada execução.

    `deve_parar` é consultado entre execuções: a execução atual termina e o que já foi gravado fica.
    """
    if not config.CENA_BASE.exists():
        raise FileNotFoundError(f"Crie a cena base antes ({config.CENA_BASE}).")
    plano = planejar(modelos, tarefas_ids, repeticoes, seeds)
    config.PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    arquivo_csv = config.PASTA_RESULTADOS / f"experimento_{carimbo}.csv"
    pasta_capturas = config.PASTA_RESULTADOS / f"capturas_{carimbo}"
    pasta_capturas.mkdir()

    with arquivo_csv.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=CAMPOS)
        escritor.writeheader()
        for feitas, (modelo, tarefa_id, repeticao, seed) in enumerate(plano, 1):
            if deve_parar():
                break
            linha = {"data": datetime.now().isoformat(timespec="seconds"), "repeticao": repeticao}
            try:
                linha |= executar_uma(blender, modelo, TAREFAS[tarefa_id], seed, temperatura)
                captura = blender.captura()
                if captura:
                    nome = f"{modelo.replace(':', '_')}_{tarefa_id}_r{repeticao}.png"
                    captura.save(pasta_capturas / nome)
                    linha["captura"] = nome
            except Exception as erro:
                linha |= {"modelo": modelo, "tarefa": tarefa_id, "seed": seed, "temperatura": temperatura,
                          "nota_0a3": 0, "avaliacao": f"falha: {erro}"[:300]}
            escritor.writerow(linha)
            arquivo.flush()  # grava na hora: nada se perde se o app for fechado
            yield feitas, len(plano), linha, arquivo_csv
