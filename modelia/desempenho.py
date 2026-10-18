"""Mede o desempenho de cada modelo no pedido do Demonstrar, para a seção de eficiência do TCC.

Rode com o Blender aberto (ele também ocupa a VRAM, como no uso real):
    uv run python -m modelia.desempenho qwen3.5:4b gemma4:12b-it-qat

Para cada modelo: descarrega, carrega de novo e faz o mesmo pedido, medindo o tempo de carga,
os tokens do prompt, a velocidade de leitura do prompt e de geração (tokens/s), e quanto do
modelo ficou na VRAM. Os números vêm do próprio Ollama (eval_count, eval_duration...).
"""

import sys
import time

from modelia import agente, config, llm

PEDIDO = "Pedido: crie uma mesa"


def medir(modelo, opcoes=None):
    llm.cliente.chat(model=modelo, messages=[], keep_alive=0)  # descarrega: mede a carga a frio
    time.sleep(2)
    opcoes = {**llm.opcoes_de(modelo), "seed": 1, **(opcoes or {})}  # `opcoes` vale mais que o config
    inicio = time.perf_counter()
    r = llm.cliente.chat(model=modelo, think=False, options=opcoes, keep_alive=config.MANTER_CARREGADO,
                         messages=[{"role": "system", "content": agente.SISTEMA_DEMONSTRAR},
                                   {"role": "user", "content": PEDIDO}])
    total = time.perf_counter() - inicio
    carregado = next(m for m in llm.cliente.ps().models if m.model == modelo)
    return {
        "modelo": modelo,
        "num_ctx": opcoes.get("num_ctx"), "num_gpu": opcoes.get("num_gpu", "auto"),
        "carga_s": round(r.load_duration / 1e9, 1),
        "tokens_prompt": r.prompt_eval_count,
        "leitura_tok_s": round(r.prompt_eval_count / (r.prompt_eval_duration / 1e9), 1),
        "tokens_gerados": r.eval_count,
        "geracao_tok_s": round(r.eval_count / (r.eval_duration / 1e9), 1),
        "total_s": round(total, 1),
        "vram_gb": round(carregado.size_vram / 1e9, 2),
        "na_gpu_%": round(100 * carregado.size_vram / carregado.size),
    }


if __name__ == "__main__":
    for nome in sys.argv[1:] or [config.MODELO_PADRAO]:
        print(medir(nome), flush=True)
