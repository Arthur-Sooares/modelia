"""Configurações do Modelia. Tudo que pode mudar entre máquinas fica aqui.

Os valores podem ser trocados por variáveis de ambiente, sem mexer no código.
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_DADOS = Path(os.environ.get("MODELIA_DADOS", RAIZ / "dados"))
PASTA_RESULTADOS = Path(os.environ.get("MODELIA_RESULTADOS", RAIZ / "resultados"))
CENA_BASE = Path(os.environ.get("MODELIA_CENA_BASE", RAIZ / "cenas" / "cena_base.blend"))

BANCO = PASTA_DADOS / "modelia.sqlite3"
PASTA_INDICE = PASTA_DADOS / "chroma"

# RNF1 e RNF2: só no endereço local, sem compartilhamento público.
HOST = "127.0.0.1"
PORTA = 7860

# RNF3: LLM local pelo Ollama.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODELO_PADRAO = os.environ.get("MODELIA_MODELO", "gemma4:12b-it-qat")
MODELO_EMBEDDINGS = "nomic-embed-text"
OPCOES_LLM = {"temperature": 0.6, "num_ctx": 8192}
# Quanto tempo o Ollama mantém o modelo na VRAM depois da última pergunta (o padrão dele é 5 min).
# Carregar de novo custa ~1 min (medido em 18/10/2026 com o qwen3.5:4b).
MANTER_CARREGADO = os.environ.get("MODELIA_MANTER_CARREGADO", "30m")
# Camadas na GPU para modelos maiores que a placa (RTX 2060, 6 GB). O Ollama sozinho põe poucas.
# Medido em 16/10/2026, com o Blender aberto: gemma4 com 42 camadas fez 5,9 tok/s e com o padrão fez 3,1.
# Com todas as camadas fica mais lento, porque o Windows transborda a VRAM para a RAM.
CAMADAS_GPU = {"gemma4:12b-it-qat": 42}
VRAM_GB = float(os.environ.get("MODELIA_VRAM_GB", "6"))  # RNF7: máquina de referência

# RNF8: servidor MCP oficial da Blender Lab, iniciado como subprocesso (stdio).
# O servidor conversa com o add-on dentro do Blender pela porta 9876.
MCP_PYTHON = os.environ.get("MODELIA_MCP_PYTHON", sys.executable)
MCP_ARGS = ["-m", "blmcp"]
PORTA_BLENDER = 9876

# RF3, RF8, RF9, RF10: abas restritas ao Pesquisador.
SENHA_PESQUISADOR = os.environ.get("MODELIA_SENHA", "modelia")

# RF6: tentativas de correção depois de um erro do Blender, por passo.
MAX_TENTATIVAS = 3
# Limite de idas e voltas LLM -> ferramenta -> LLM em uma resposta.
MAX_RODADAS = 8
