"""Acesso ao Ollama (RNF3, RF3): conversa com a LLM e gerencia os modelos instalados."""

import ollama

from modelia import config

cliente = ollama.Client(host=config.OLLAMA_HOST)


def disponivel():
    try:
        cliente.list()
        return True
    except Exception:
        return False


def modelos():
    """Modelos instalados: nome, tamanho em GB e data, do mais novo para o mais antigo."""
    lista = [
        {
            "nome": m.model,
            "tamanho_gb": round((m.size or 0) / 1e9, 1),
            "modificado": m.modified_at.strftime("%d/%m/%Y %H:%M") if m.modified_at else "",
        }
        for m in cliente.list().models
    ]
    return sorted(lista, key=lambda m: m["modificado"], reverse=True)


def modelos_de_chat():
    """Só os modelos que conversam (tira os de embeddings, que não servem para o chat)."""
    return [m["nome"] for m in modelos() if "embed" not in m["nome"]]


def cabe_na_vram(tamanho_gb, vram_gb=config.VRAM_GB):
    """Regra prática: o arquivo do modelo mais ~1 GB de contexto precisa caber na VRAM (RNF7)."""
    return tamanho_gb + 1.0 <= vram_gb


def baixar(nome):
    """Baixa um modelo. É um gerador: devolve (fração de 0 a 1, texto do status) enquanto baixa."""
    for parte in cliente.pull(nome.strip(), stream=True):
        if parte.total:
            yield (parte.completed or 0) / parte.total, parte.status
        else:
            yield None, parte.status


def apagar(nome):
    cliente.delete(nome)


def opcoes_de(modelo, opcoes=None):
    opcoes = dict(opcoes or config.OPCOES_LLM)
    if modelo in config.CAMADAS_GPU:
        opcoes["num_gpu"] = config.CAMADAS_GPU[modelo]
    return opcoes


def aquecer(modelo):
    """Carrega o modelo na VRAM antes da primeira pergunta (a primeira carga leva ~1 min).
    Usa as mesmas opções das conversas: com num_ctx ou num_gpu diferentes o Ollama recarregaria."""
    try:
        cliente.chat(model=modelo, messages=[], options=opcoes_de(modelo), keep_alive=config.MANTER_CARREGADO)
        return True
    except Exception:
        return False  # Ollama desligado ou modelo não instalado: a primeira pergunta carrega, como antes.


def conversar(modelo, mensagens, ferramentas=None, opcoes=None, ao_escrever=None):
    """Uma rodada de conversa. Desliga o modo "pensar" quando o modelo aceita (fica mais rápido).

    Com `ao_escrever`, a resposta vem aos pedaços (streaming): a função recebe o texto escrito
    até agora a cada pedaço, e no fim volta a mesma resposta completa de sempre."""
    opcoes = opcoes_de(modelo, opcoes)
    argumentos = {"model": modelo, "messages": mensagens, "options": opcoes,
                  "keep_alive": config.MANTER_CARREGADO}
    if ferramentas:
        argumentos["tools"] = ferramentas

    def chamar(**extras):
        if not ao_escrever:
            return cliente.chat(**argumentos, **extras)
        return juntar(cliente.chat(**argumentos, **extras, stream=True), ao_escrever)

    try:
        return chamar(think=False)
    except ollama.ResponseError as erro:
        if "num_gpu" in opcoes and erro.status_code == 500:
            # Outro programa (um jogo, o Blender) ocupou a VRAM e as camadas não couberam: o Ollama decide.
            argumentos["options"] = {k: v for k, v in opcoes.items() if k != "num_gpu"}
            return chamar(think=False)
        if "think" not in str(erro).lower():
            raise
        return chamar()


def juntar(partes, ao_escrever):
    """Junta os pedaços do streaming numa resposta só, avisando o texto a cada pedaço."""
    texto, chamadas, ultima = "", [], None
    for ultima in partes:
        if ultima.message.content:
            texto += ultima.message.content
            ao_escrever(texto)
        chamadas += ultima.message.tool_calls or []
    ultima.message.content = texto
    ultima.message.tool_calls = chamadas or None
    return ultima


def embeddings(textos):
    """Vetores de embeddings (RF8) com o modelo nomic-embed-text."""
    return cliente.embed(model=config.MODELO_EMBEDDINGS, input=textos).embeddings
