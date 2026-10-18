"""Testes de llm.conversar com um cliente do Ollama falso."""

import ollama

from modelia import config, llm


def test_sem_vram_tenta_de_novo_sem_num_gpu(monkeypatch):
    opcoes_vistas = []

    def chat(**argumentos):
        opcoes_vistas.append(argumentos["options"])
        if "num_gpu" in argumentos["options"]:
            raise ollama.ResponseError("CUDA error: shared object initialization failed", 500)
        return "ok"

    monkeypatch.setattr(llm.cliente, "chat", chat)
    monkeypatch.setitem(config.CAMADAS_GPU, "grande", 42)
    assert llm.conversar("grande", []) == "ok"
    assert opcoes_vistas[0]["num_gpu"] == 42 and "num_gpu" not in opcoes_vistas[1]


def test_juntar_pedacos_do_streaming():
    from types import SimpleNamespace as N

    from modelia.llm import juntar
    chamada = N(function=N(name="execute_blender_code", arguments={"code": "x"}))
    partes = [N(message=N(content="Ola", tool_calls=None)), N(message=N(content=", aluno", tool_calls=None)),
              N(message=N(content="", tool_calls=[chamada]))]
    vistos = []
    final = juntar(iter(partes), vistos.append)
    assert vistos == ["Ola", "Ola, aluno"]
    assert final.message.content == "Ola, aluno" and final.message.tool_calls == [chamada]
