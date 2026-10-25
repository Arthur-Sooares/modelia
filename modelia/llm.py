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


_capacidades = {}  # nome -> lista do Ollama; só guarda quando a consulta deu certo


def usa_ferramentas(nome):
    """Se o modelo sabe chamar ferramentas (tool calling). O Modelia precisa disso para mexer no Blender:
    o gemma3, por exemplo, não sabe, e o Ollama recusa o pedido com erro 400."""
    if nome not in _capacidades:
        try:
            _capacidades[nome] = cliente.show(nome).capabilities or []
        except Exception:
            return False
    return "tools" in _capacidades[nome]


def modelos_de_chat():
    """Só os modelos que servem para o chat: sabem chamar ferramentas (tira os de embeddings e os sem tools)."""
    return [m["nome"] for m in modelos() if "embed" not in m["nome"] and usa_ferramentas(m["nome"])]


def cabe_na_vram(tamanho_gb, vram_gb=config.VRAM_GB):
    """Regra prática: o arquivo do modelo mais ~1 GB de contexto precisa caber na VRAM (RNF7)."""
    return tamanho_gb + 1.0 <= vram_gb


def baixar(nome):
    """Baixa um modelo. É um gerador: devolve (fração de 0 a 1, texto do status) enquanto baixa."""
    for parte in cliente.pull(nome.strip(), stream=True):
        if parte.total:
            feito = parte.completed or 0
            yield feito / parte.total, f"Baixando… {feito / 1e9:.1f} de {parte.total / 1e9:.1f} GB".replace(".", ",")
        else:
            yield None, parte.status


def apagar(nome):
    cliente.delete(nome)


def vram_em_uso():
    """GB de VRAM ocupados agora pelos modelos carregados no Ollama."""
    return round(sum(m.size_vram or 0 for m in cliente.ps().models) / 1e9, 1)


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


def conversar(modelo, mensagens, ferramentas=None, opcoes=None, ao_escrever=None, formato=None):
    """Uma rodada de conversa. Desliga o modo "pensar" quando o modelo aceita (fica mais rápido).

    Com `ao_escrever`, a resposta vem aos pedaços (streaming): a função recebe o texto escrito
    até agora a cada pedaço, e no fim volta a mesma resposta completa de sempre.
    Com `formato` (um esquema JSON), o Ollama só deixa o modelo escrever um JSON nesse formato."""
    opcoes = opcoes_de(modelo, opcoes)
    argumentos = {"model": modelo, "messages": mensagens, "options": opcoes,
                  "keep_alive": config.MANTER_CARREGADO}
    if ferramentas:
        argumentos["tools"] = ferramentas
    if formato:
        argumentos["format"] = formato

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
    """Vetores de embeddings (RF8) com o modelo config.MODELO_EMBEDDINGS."""
    return cliente.embed(model=config.MODELO_EMBEDDINGS, input=textos).embeddings
