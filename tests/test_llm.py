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
