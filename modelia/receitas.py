"""Receitas de objetos comuns para o modo Demonstrar.

Os modelos pequenos sabem COMO é uma estante, mas erram a conta de onde pôr cada peça
(trocam a altura da peça pela posição, empilham errado, fazem o corpo maciço). Aqui a LLM só
preenche uma ficha curta, em JSON com esquema fixo (o Ollama força o formato), e este
módulo faz a matemática e escreve o código com as funções prontas (blender.AJUDANTES).

Na CPU do notebook isso troca 250 a 900 tokens de código (40 a 150 s) por uns 60 (cerca de 15 s).
Pedido sem receita continua no caminho antigo: a LLM escreve o código inteiro.
"""

import json
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

CORES = {"madeira": "#8B5A2B", "madeira_clara": "#C19A6B", "branco": "#EDEDE8", "preto": "#2B2B2B",
         "cinza": "#8A8A8A", "azul": "#5B6C8F", "verde": "#6B8F5B", "vermelho": "#A23B3B",
         "amarelo": "#D9B44A", "laranja": "#D9823B", "rosa": "#D98FA6", "marrom": "#5C3A21", "bege": "#D9C7A3",
         "vidro": "#BFD8DE"}
E = 0.02  # espessura das placas (laterais, prateleiras)

SISTEMA = (
    "Você preenche a ficha de um objeto 3D que o Modelia vai montar no Blender. "
    "Responda só com o JSON da ficha. Medidas em metros. "
    "Se o pedido trouxer medidas, use as do pedido (40 cm = 0.4). "
    "Se não trouxer, use medidas reais típicas e adapte ao uso que o aluno descreveu.\n"
)


def _numero(minimo, maximo):
    return {"type": "number", "minimum": minimo, "maximum": maximo}


def _esquema(**campos):
    campos["cor"] = {"type": "string", "enum": list(CORES)}
    return {"type": "object", "properties": campos, "required": list(campos)}


# --- peças e código ---

@dataclass
class Peca:
    funcao: str  # "caixa" ou "cilindro"
    nome: str
    medidas: tuple  # caixa: (largura, profundidade, altura); cilindro: (raio, altura)
    x: float = 0.0
    y: float = 0.0
    base: float = 0.0


def _n(valor):
    return f"{round(valor, 3):g}"


def codigo_das_pecas(pecas, cor, titulo):
    linhas = [f"# {titulo} (ficha preenchida pela LLM; posições calculadas pelo Modelia)",
              f"cor = '{CORES.get(cor, CORES['madeira'])}'"]
    for p in pecas:
        args = [repr(p.nome), *(_n(m) for m in p.medidas)]
        args += [f"{k}={_n(v)}" for k, v in (("x", p.x), ("y", p.y), ("base", p.base)) if abs(v) > 1e-9]
        linhas.append(f"{p.funcao}({', '.join(args)}, cor=cor)")
    return "\n".join(linhas)


def _cm(valor):
    return f"{valor:.2f}".replace(".", ",")


# --- estante ---

def montar_estante(f):
    largura, prof, altura = f["largura"], f["profundidade"], f["altura"]
    nichos = [v for v in f["nichos"] if v > 0] or [0.35] * 4
    rodape = 0.06
    # Os nichos ocupam o que sobra entre o rodapé e o topo; a proporção entre eles é a da ficha.
    livre = altura - rodape - (len(nichos) + 1) * E
    if livre < 0.1 * len(nichos):  # nichos demais para a altura: ficam com 0,1 m cada
        livre = 0.1 * len(nichos)
        altura = livre + rodape + (len(nichos) + 1) * E
    nichos = [v * livre / sum(nichos) for v in nichos]
    dentro = largura - 2 * E
    pecas = [Peca("caixa", "LateralEsquerda", (E, prof, altura), x=-largura / 2 + E / 2),
             Peca("caixa", "LateralDireita", (E, prof, altura), x=largura / 2 - E / 2)]
    if f.get("fundo", True):
        pecas.append(Peca("caixa", "Fundo", (dentro, 0.01, altura - rodape), y=prof / 2 - 0.005, base=rodape))
    pecas.append(Peca("caixa", "Rodape", (dentro, E, rodape), y=-prof / 2 + 0.02 + E / 2))
    z = rodape
    for i in range(len(nichos) + 1):
        nome = "Base" if i == 0 else "Topo" if i == len(nichos) else f"Prateleira{i}"
        pecas.append(Peca("caixa", nome, (dentro, prof - 0.01, E), y=-0.005, base=z))
        if i < len(nichos):
            z += E + nichos[i]
    return pecas, (f"de {_cm(largura)} x {_cm(prof)} x {_cm(altura)} m com {len(nichos)} nichos "
                   f"de {', '.join(_cm(v) for v in nichos)} m (de baixo para cima)")


# --- mesa ---

def montar_mesa(f):
    largura, prof, altura = f["largura"], f["profundidade"], f["altura"]
    tampo, recuo = 0.04, 0.05
    grossura = 0.07 if largura > 1.6 else 0.05
    perna = altura - tampo
    px, py = largura / 2 - recuo - grossura / 2, prof / 2 - recuo - grossura / 2
    pecas = [Peca("caixa", "Tampo", (largura, prof, tampo), base=perna)]
    for i, (sx, sy) in enumerate([(-1, -1), (1, -1), (-1, 1), (1, 1)], 1):
        if f.get("pernas") == "redondas":
            pecas.append(Peca("cilindro", f"Perna{i}", (grossura / 2, perna), x=sx * px, y=sy * py))
        else:
            pecas.append(Peca("caixa", f"Perna{i}", (grossura, grossura, perna), x=sx * px, y=sy * py))
    if altura >= 0.6:  # saia: as tábuas logo abaixo do tampo, entre as pernas
        saia = 0.08
        pecas += [Peca("caixa", "SaiaFrente", (2 * px - grossura, E, saia), y=-py, base=perna - saia),
                  Peca("caixa", "SaiaTras", (2 * px - grossura, E, saia), y=py, base=perna - saia),
                  Peca("caixa", "SaiaEsquerda", (E, 2 * py - grossura, saia), x=-px, base=perna - saia),
                  Peca("caixa", "SaiaDireita", (E, 2 * py - grossura, saia), x=px, base=perna - saia)]
    return pecas, f"de {_cm(largura)} x {_cm(prof)} m com o tampo a {_cm(altura)} m do chão"


# --- cadeira ---

def montar_cadeira(f):
    largura, prof, assento = f["largura_assento"], f["profundidade_assento"], f["altura_assento"]
    total = max(f["altura_total"], assento + 0.25)
    grossura, tabua = 0.04, 0.04
    perna = assento - tabua
    px, py = largura / 2 - grossura / 2, prof / 2 - grossura / 2
    pecas = [Peca("caixa", f"Perna{i}", (grossura, grossura, perna), x=sx * px, y=sy * py)
             for i, (sx, sy) in enumerate([(-1, -1), (1, -1), (-1, 1), (1, 1)], 1)]
    pecas.append(Peca("caixa", "Assento", (largura, prof, tabua), base=perna))
    pecas.append(Peca("caixa", "Encosto", (largura, 0.03, total - assento), y=prof / 2 - 0.015, base=assento))
    if f.get("bracos"):
        for nome, sx in (("Esquerdo", -1), ("Direito", 1)):
            x = sx * (largura / 2 - 0.02)
            pecas += [Peca("caixa", f"Suporte{nome}", (0.03, 0.03, 0.2), x=x, y=-prof / 2 + 0.06, base=assento),
                      Peca("caixa", f"Braco{nome}", (0.04, prof - 0.05, 0.03), x=x, y=-0.01, base=assento + 0.2)]
    return pecas, (f"com assento de {_cm(largura)} x {_cm(prof)} m a {_cm(assento)} m do chão "
                   f"e encosto até {_cm(total)} m")


@dataclass(frozen=True)
class Receita:
    nome: str
    palavras: tuple  # como o aluno chama o objeto (sem acento); o plural com "s" também vale
    ficha: str  # o que a LLM precisa saber para preencher, com medidas típicas
    esquema: dict
    montar: Callable
    artigo: str = "uma"


RECEITAS = {r.nome: r for r in [
    Receita("estante", ("estante", "livreiro", "biblioteca de livros"),
            "Objeto: estante aberta (laterais, fundo e prateleiras). Campos:\n"
            "- largura, profundidade, altura: medidas de fora. Típico 0.8 x 0.3 x 1.8.\n"
            "- nichos: a altura livre de cada vão, de baixo para cima; um número por vão. "
            "\"4 prateleiras\" = 4 nichos. Escolha pelo que vai em cada vão: livros 0.30, estatuetas e "
            "enfeites 0.25 a 0.35, caixas e tralhas 0.40, objetos pequenos 0.20. Coisas de tamanhos "
            "diferentes pedem nichos de alturas diferentes.\n"
            "- fundo: true para ter a placa de trás.\n- cor: madeira, madeira_clara, branco, preto ou cinza.\n",
            _esquema(largura=_numero(0.3, 3), profundidade=_numero(0.15, 0.8), altura=_numero(0.3, 2.8),
                     nichos={"type": "array", "items": _numero(0.1, 1.2), "minItems": 1, "maxItems": 10},
                     fundo={"type": "boolean"}),
            montar_estante),
    Receita("mesa", ("mesa",),
            "Objeto: mesa de 4 pernas. Campos:\n"
            "- largura e profundidade do tampo; altura = do chão até a face de cima do tampo.\n"
            "Típico: jantar para 4 pessoas 1.2 x 0.8, para 6 pessoas 1.8 x 0.9, para 8 2.2 x 1.0, todas a 0.75; "
            "mesa de centro 1.0 x 0.5 a 0.4; mesa de cabeceira 0.45 x 0.4 a 0.55; escrivaninha 1.2 x 0.6 a 0.75.\n"
            "- pernas: quadradas ou redondas.\n- cor: madeira, madeira_clara, branco, preto ou cinza.\n",
            _esquema(largura=_numero(0.3, 3), profundidade=_numero(0.3, 1.5), altura=_numero(0.25, 1.1),
                     pernas={"type": "string", "enum": ["quadradas", "redondas"]}),
            montar_mesa),
    Receita("cadeira", ("cadeira",),
            "Objeto: cadeira com 4 pernas e encosto. Campos:\n"
            "- largura_assento e profundidade_assento (típico 0.45 x 0.45).\n"
            "- altura_assento: do chão até a face de cima do assento (típico 0.45; infantil 0.30; bar 0.75).\n"
            "- altura_total: do chão até o topo do encosto (típico 0.9).\n"
            "- bracos: true para cadeira com braços.\n- cor: madeira, madeira_clara, branco, preto ou cinza.\n",
            _esquema(largura_assento=_numero(0.3, 0.7), profundidade_assento=_numero(0.3, 0.7),
                     altura_assento=_numero(0.25, 0.85), altura_total=_numero(0.5, 1.3),
                     bracos={"type": "boolean"}),
            montar_cadeira),
]}


# --- do pedido às receitas ---

NUMEROS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
           "sete": 7, "oito": 8}


def sem_acento(texto):
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def identificar(pedido):
    """[(receita, quantidade)] dos objetos com receita citados no pedido, na ordem em que aparecem.

    "luminária de mesa" não é uma mesa: o nome logo depois de "de"/"da" só descreve outra coisa.
    """
    texto = sem_acento(pedido)
    achados = []
    for receita in RECEITAS.values():
        achado = _primeira_mencao(texto, receita)
        if achado:
            achados.append((*achado, receita))
    return [(r, q) for _, q, r in sorted(achados, key=lambda a: a[0])]


def _primeira_mencao(texto, receita):
    """(posição, quantidade) da primeira vez que o objeto aparece no texto, ou None."""
    for palavra in receita.palavras:
        for achado in re.finditer(rf"(?:(\w+)\s+)?\b{palavra}s?\b", texto):
            antes = achado.group(1) or ""
            if antes not in ("de", "da", "do"):
                quantidade = int(antes) if antes.isdigit() else NUMEROS.get(antes, 1)
                return achado.start(), max(1, min(quantidade, 8))
    return None


def mensagens_da_ficha(receita, pedido):
    return [{"role": "system", "content": SISTEMA + receita.ficha}, {"role": "user", "content": f"Pedido: {pedido}"}]


def ler_ficha(receita, texto):
    """JSON da LLM -> ficha com todos os campos, dentro dos limites do esquema. ValueError se não der."""
    dados = json.loads(texto)
    if not isinstance(dados, dict):
        raise ValueError("a ficha não é um objeto JSON")
    try:
        return _completar(receita, dados)
    except (TypeError, KeyError, IndexError) as erro:
        raise ValueError(f"ficha incompleta: {erro}") from erro


def _completar(receita, dados):
    ficha = {}
    for campo, regra in receita.esquema["properties"].items():
        valor = dados.get(campo)
        if regra["type"] == "number":
            valor = float(valor)
            ficha[campo] = min(max(valor, regra["minimum"]), regra["maximum"])
        elif regra["type"] == "array":
            itens = [min(max(float(v), regra["items"]["minimum"]), regra["items"]["maximum"]) for v in valor or []]
            if not itens:
                raise ValueError(f"o campo {campo} está vazio")
            ficha[campo] = itens[:regra["maxItems"]]
        elif regra["type"] == "boolean":
            ficha[campo] = bool(valor)
        else:
            ficha[campo] = valor if valor in regra.get("enum", [valor]) else regra["enum"][0]
    return ficha


def _deslocar(pecas, dx, dy, girar=False, prefixo=""):
    """Move as peças de um objeto; girar = meia-volta (a cadeira de frente para a mesa)."""
    sinal = -1 if girar else 1
    return [Peca(p.funcao, prefixo + p.nome, p.medidas, sinal * p.x + dx, sinal * p.y + dy, p.base) for p in pecas]


def _pegada(pecas):
    """(x mínimo, x máximo, y mínimo, y máximo) das peças no chão."""
    def meio(p):
        return p.medidas[0] if p.funcao == "cilindro" else p.medidas[0] / 2

    def meio_y(p):
        return p.medidas[0] if p.funcao == "cilindro" else p.medidas[1] / 2
    return (min(p.x - meio(p) for p in pecas), max(p.x + meio(p) for p in pecas),
            min(p.y - meio_y(p) for p in pecas), max(p.y + meio_y(p) for p in pecas))


def montar(objetos):
    """objetos = [(receita, quantidade, ficha)] -> (código, frase do que foi criado).

    Cadeiras junto de uma mesa ficam em volta dela, de frente; o resto fica lado a lado.
    """
    blocos, frases = [], []
    montados = [(r, q, f, *r.montar(f)) for r, q, f in objetos]
    mesa = next((m for m in montados if m[0].nome == "mesa"), None)
    cursor_x = None
    for receita, quantidade, ficha, pecas, frase in montados:
        quem = f"{receita.artigo} {receita.nome}" if quantidade == 1 else f"{quantidade} {receita.nome}s"
        frases.append(f"{quem} {frase}")
        for i in range(quantidade):
            prefixo = f"{receita.nome.capitalize()}{i + 1}_" if quantidade > 1 or len(montados) > 1 else ""
            if receita.nome == "cadeira" and mesa:
                lado_frente = i % 2 == 0
                por_lado = math.ceil(quantidade / 2)
                largura_mesa, prof_mesa = mesa[2]["largura"], mesa[2]["profundidade"]
                x = -largura_mesa / 2 + largura_mesa * (i // 2 + 0.5) / por_lado
                y = prof_mesa / 2 + ficha["profundidade_assento"] / 2 + 0.08
                colocadas = _deslocar(pecas, x, -y if lado_frente else y, girar=lado_frente, prefixo=prefixo)
            else:
                x0, x1, *_ = _pegada(pecas)
                dx = 0.0 if cursor_x is None else cursor_x - x0 + 0.4
                colocadas = _deslocar(pecas, dx, 0.0, prefixo=prefixo)
                cursor_x = dx + x1
            blocos.append(codigo_das_pecas(colocadas, ficha["cor"], f"{receita.nome} {i + 1}" if quantidade > 1
                                           else receita.nome))
    return "\n\n".join(blocos), "Pronto! Criei " + " e ".join(frases) + "."
