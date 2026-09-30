# Modelia

Tutor de modelagem 3D no Blender com LLM local. TCC de Sistemas de Informação, IFNMG Campus Januária.
Os requisitos estão na [Documentação de Requisitos](https://claude.ai/code/artifact/fb132e55-e77c-451d-886c-9d8a89a17bb4).

## Como rodar

1. Abra o **Ollama**.
2. Abra o **Blender 5.1** com o add-on MCP da Blender Lab ligado (porta 9876). O modo Explicar funciona sem ele.
3. Na pasta do projeto:

```
uv run main.py
```

O navegador abre em http://127.0.0.1:7860.

- **Estudante:** digite o código de participante (ex.: P07).
- **Pesquisador:** marque "Sou o Pesquisador" e use a senha da variável `MODELIA_SENHA` (padrão: `modelia`).

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
