# Modelia

Tutor de modelagem 3D no Blender com LLM local. TCC de Sistemas de Informação, IFNMG Campus Januária.
Os requisitos estão na [Documentação de Requisitos](https://claude.ai/code/artifact/fb132e55-e77c-451d-886c-9d8a89a17bb4).

## Primeira vez num computador novo

Precisa do [Ollama](https://ollama.com), do [uv](https://docs.astral.sh/uv/), do Git e do Blender 5.1 com o add-on MCP da Blender Lab.

```
ollama pull qwen3.5:4b
ollama pull qwen3-embedding:0.6b
uv sync
uv run python -m modelia.manual
```

O último comando baixa o manual do Blender (~350 MB) e monta a base do modo Explicar. Leva alguns minutos e só precisa rodar uma vez.
Sem ele, o Explicar responde que o assunto não está no material.

O gemma4 (`ollama pull gemma4:12b-it-qat`) só é necessário para o experimento. Ele precisa de uma placa de vídeo com 6 GB ou mais.

## Como rodar

1. Abra o **Ollama**.
2. Abra o **Blender 5.1** com o add-on MCP ligado (porta 9876). O modo Explicar funciona sem ele.
3. Na pasta do projeto, no PowerShell:

```
$env:MODELIA_MODELO="qwen3.5:4b"; uv run main.py
```

O navegador abre em http://127.0.0.1:7860. Clique em **Entrar** para ir ao chat.

As telas do pesquisador ficam em http://127.0.0.1:7860/app. Para entrar, use a senha da variável `MODELIA_SENHA` (padrão: `modelia`).

## Estrutura

| Arquivo | O que faz | Requisitos |
| --- | --- | --- |
| `modelia/interface.py` | Telas e abas em Gradio | RF1 a RF10, RNF1, RNF2, RNF5, RNF6 |
| `modelia/agente.py` | Orquestrador dos modos Explicar, Demonstrar e Verificar | RF4 a RF7 |
| `modelia/blender.py` | Cliente MCP do Blender | RNF8 |
| `modelia/llm.py` | Conversa com o Ollama e gerência de modelos | RF3, RNF3 |
| `modelia/conhecimento.py` | Base de conhecimento (RAG com ChromaDB) | RF8 |
| `modelia/banco.py` | Sessões e histórico em SQLite | RF1, RF9, RNF10 |
| `modelia/tarefas.py` | Tarefas-meta e correção automática (nota 0 a 3) | RF7, RF10 |
| `modelia/experimento.py` | Bateria de testes das LLMs | RF10 |
| `modelia/config.py` | Endereços, caminhos e limites | |

Os dados ficam em `dados/` (banco e índice) e `resultados/` (experimentos). As duas pastas estão fora do Git.

## Testes

```
uv run pytest
uv run ruff check .
```

Os testes usam um Blender e uma LLM falsos, então rodam sem nada aberto.
