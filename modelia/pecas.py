"""Ficha de peças para objetos sem receita (copo, carro, árvore, cubo mágico...).

A LLM primeiro descreve o objeto em uma frase (as partes que fazem ele ser reconhecido) e depois
lista as peças: forma, medidas e onde cada uma fica em relação a outra ("em_cima" de X,
"ao_lado_direita" de X, "dentro" de X, pernas "embaixo_cantos" de X, "rodas" de X). Este módulo
resolve as posições, que é a parte em que os modelos pequenos erram, e escreve o código com as
funções prontas (blender.AJUDANTES). O Ollama força o formato da ficha, como nas receitas.
"""

import json
import math
import re
from dataclasses import dataclass

from modelia.exemplos_fichas import EXEMPLOS, PADRAO
from modelia.receitas import CORES, SISTEMA, sem_acento

FORMAS = ["caixa", "cilindro", "cone", "esfera", "cilindro_oco", "alca", "piramide", "telhado"]
REDONDAS = {"cilindro", "cone", "esfera", "cilindro_oco"}
POSICOES = ["centro", "em_cima", "em_cima_frente", "em_cima_tras", "em_cima_esquerda", "em_cima_direita",
            "embaixo_cantos", "embaixo_centro", "rodas", "ao_lado_esquerda", "ao_lado_direita", "ao_lado_frente",
            "ao_lado_tras", "dentro", "no_meio"]
MAX_PECAS = 16
MAX_COPIAS = 64  # repetir_x * repetir_y * repetir_z de uma peça
FOLGA = 0.002   # entre as cópias repetidas (cubinhos do cubo mágico)


def _numero(minimo, maximo):
    return {"type": "number", "minimum": minimo, "maximum": maximo}


def _inteiro(minimo, maximo):
    return {"type": "integer", "minimum": minimo, "maximum": maximo}


ESQUEMA = {
    "type": "object",
    "properties": {
        "objeto": {"type": "string"},
        "descricao": {"type": "string"},  # vem antes das peças: o modelo pensa no objeto real primeiro
        "pecas": {"type": "array", "minItems": 1, "maxItems": MAX_PECAS, "items": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "forma": {"type": "string", "enum": FORMAS},
                "largura": _numero(0.002, 100), "profundidade": _numero(0.002, 100), "altura": _numero(0.002, 100),
                "posicao": {"type": "string", "enum": POSICOES},
                "de": {"type": "string"},
                "cor": {"type": "string", "enum": list(CORES)},
                "topo": _numero(0, 3),
                "deitado": {"type": "boolean"},
                "altura_do_chao": _numero(0, 50),
                "mover_x": _numero(-50, 50), "mover_y": _numero(-50, 50), "mover_z": _numero(-50, 50),
                "repetir_x": _inteiro(1, 10), "repetir_y": _inteiro(1, 10), "repetir_z": _inteiro(1, 10),
                "espaco": _numero(0, 20),
            },
            "required": ["nome", "forma", "largura", "profundidade", "altura", "posicao", "de", "cor"],
        }},
    },
    "required": ["objeto", "descricao", "pecas"],
}

FICHA = (
    "Primeiro escreva em descricao, numa frase, como o objeto real é e quais partes fazem alguém reconhecer "
    "ele. Depois crie uma peça para cada parte da descrição, e nenhuma a mais. Simples, mas parecido com o real.\n"
    "Cada peça tem:\n"
    "- nome: curto, sem espaço (Corpo, Tampa, Roda, Copa).\n"
    "- forma: caixa; cilindro; cone (afina para cima); esfera; cilindro_oco (aberto em cima e com fundo: copo, "
    "vaso, caneca, balde, panela, cúpula); alca (alça em arco; só se o objeto real tem alça: caneca, xícara, "
    "balde sim; copo, vaso, garrafa não); piramide (base retangular que afina); telhado (duas águas, a "
    "cumeeira ao longo da largura).\n"
    "- largura (X), profundidade (Y) e altura (Z). Em cilindro, cone, esfera e cilindro_oco, largura = diâmetro.\n"
    "- deitado (opcional): true deita o cilindro (roda, rolo, fuselagem); aí altura = comprimento, para a frente.\n"
    "- topo (opcional, cilindro, cone, cilindro_oco e piramide): largura do topo dividida pela da base. "
    "1 = reto, 0 = ponta, 0.4 = gargalo, 1.3 = boca mais larga que o fundo.\n"
    "- posicao e de (o nome de outra peça; a peça principal usa de = \"chao\"):\n"
    "  centro: no meio da cena. em_cima: apoiada em cima de 'de', no meio. em_cima_frente, em_cima_tras, "
    "em_cima_esquerda, em_cima_direita: em cima, encostada nessa borda.\n"
    "  embaixo_cantos: 4 pernas embaixo de 'de', do chão até ela. embaixo_centro: uma coluna embaixo de 'de'.\n"
    "  rodas: 4 rodas deitadas nos lados de 'de', no chão.\n"
    "  ao_lado_esquerda, ao_lado_direita, ao_lado_frente (a frente é a face que se vê), ao_lado_tras: encostada "
    "por fora em 'de', na meia altura dela.\n"
    "  dentro: dentro de 'de' (terra no vaso). no_meio: atravessando 'de' pelo meio (asas no avião, fita no "
    "presente).\n"
    "- altura_do_chao (opcional): onde a peça começa, medido da base de 'de' (ou do chão, em centro).\n"
    "- mover_x, mover_y, mover_z (opcional): desloca a peça depois de posta (duas janelas, uma de cada lado).\n"
    "- repetir_x, repetir_y, repetir_z e espaco (opcional): cópias iguais em grade, com espaco entre elas.\n"
    "- cor: " + ", ".join(CORES) + ". Use as cores do objeto real.\n"
    "Copie a ideia dos exemplos parecidos abaixo e adapte ao pedido (medidas, cores, partes a mais ou a menos)."
)


def exemplos_para(pedido, quantos=3):
    """As fichas da biblioteca mais parecidas com o pedido, a mais parecida por último (mais perto do pedido).

    Nome inteiro no pedido vale mais que palavra solta; nomes mais longos ganham ("casa de cachorro"
    antes de "casa"). Sem nada parecido, vão exemplos variados (PADRAO).
    """
    texto = sem_acento(pedido)
    palavras = {p.rstrip("s") for p in re.findall(r"\w{4,}", texto)}
    notas = []
    for nomes, ficha in EXEMPLOS:
        nota = 0.0
        for nome in nomes:
            if re.search(rf"\b{re.escape(nome)}s?\b", texto):
                nota = max(nota, 2 + len(nome) / 100)
            elif palavras & {p.rstrip("s") for p in re.findall(r"\w{4,}", nome)}:
                nota = max(nota, 1)
        if nota:
            notas.append((nota, ficha))
    escolhidos = [f for _, f in sorted(notas, key=lambda n: n[0], reverse=True)[:quantos]]
    for nome in PADRAO:
        if len(escolhidos) >= quantos:
            break
        ficha = next(f for nomes, f in EXEMPLOS if nomes[0] == nome)
        if ficha not in escolhidos:
            escolhidos.append(ficha)
    return list(reversed(escolhidos))


def mensagens_da_ficha(pedido):
    exemplos = "\n".join(f"Exemplo, {f['objeto']}:\n" + json.dumps(f, ensure_ascii=False, separators=(",", ":"))
                         for f in exemplos_para(pedido))
    return [{"role": "system", "content": SISTEMA + FICHA + "\n" + exemplos},
            {"role": "user", "content": f"Pedido: {pedido}"}]


# --- posições ---

@dataclass
class Colocada:
    """Uma peça já posta. largura, profundidade e altura são o tamanho dela em X, Y e Z."""
    nome: str
    forma: str
    largura: float
    profundidade: float
    altura: float
    x: float
    y: float
    base: float
    cor: str
    topo: float = 1.0
    giro: int = 0
    eixo: str = "z"  # cilindro deitado: "x" ou "y"

    def caixa(self):
        """(x0, x1, y0, y1, z0, z1) que a peça ocupa."""
        if self.forma == "alca":  # sai da parede para fora; o giro diz para que lado
            fora = self.altura / 2
            dx, dy = {0: (fora, 0), 180: (-fora, 0), -90: (0, -fora), 90: (0, fora)}[self.giro]
            return (min(self.x, self.x + dx), max(self.x, self.x + dx), min(self.y, self.y + dy) - 0.01,
                    max(self.y, self.y + dy) + 0.01, self.base, self.base + self.altura)
        return (self.x - self.largura / 2, self.x + self.largura / 2, self.y - self.profundidade / 2,
                self.y + self.profundidade / 2, self.base, self.base + self.altura)


def _juntar(caixas):
    return (min(c[0] for c in caixas), max(c[1] for c in caixas), min(c[2] for c in caixas),
            max(c[3] for c in caixas), min(c[4] for c in caixas), max(c[5] for c in caixas))


def _limpar(dados):
    """Confere e completa a ficha da LLM. Devolve (objeto, peças). ValueError se faltar o essencial."""
    if not isinstance(dados, dict) or not isinstance(dados.get("pecas"), list) or not dados["pecas"]:
        raise ValueError("a ficha não tem peças")
    pecas, nomes = [], set()
    for bruta in dados["pecas"][:MAX_PECAS]:
        try:
            forma = bruta["forma"] if bruta["forma"] in FORMAS else "caixa"
            p = {"nome": str(bruta["nome"]).strip().replace(" ", "") or "Peca",
                 "forma": forma,
                 "largura": min(max(float(bruta["largura"]), 0.002), 100),
                 "profundidade": min(max(float(bruta["profundidade"]), 0.002), 100),
                 "altura": min(max(float(bruta["altura"]), 0.002), 100),
                 "posicao": bruta["posicao"] if bruta["posicao"] in POSICOES else "centro",
                 "de": str(bruta.get("de", "chao")).strip().replace(" ", ""),
                 "cor": bruta["cor"] if bruta.get("cor") in CORES else "cinza",
                 "topo": min(max(float(bruta.get("topo", 0.0 if forma in ("cone", "piramide") else 1.0)), 0), 3),
                 "deitado": forma == "cilindro" and (bool(bruta.get("deitado")) or bruta["posicao"] == "rodas"),
                 # None = não veio: cada posição usa o seu padrão (a base de 'de', a meia altura...)
                 "altura_do_chao": (None if bruta.get("altura_do_chao") is None
                                    else min(max(float(bruta["altura_do_chao"]), 0), 50)),
                 "mover": [min(max(float(bruta.get(f"mover_{e}", 0) or 0), -50), 50) for e in "xyz"],
                 "repetir": [min(max(int(bruta.get(f"repetir_{e}", 1)), 1), 10) for e in "xyz"],
                 "espaco": min(max(float(bruta.get("espaco", FOLGA) or 0), 0), 20)}
        except (KeyError, TypeError, ValueError) as erro:
            raise ValueError(f"peça incompleta: {erro}") from erro
        while p["nome"] in nomes:  # nome repetido: a LLM às vezes chama duas peças de "Perna"
            p["nome"] += "_2"
        nomes.add(p["nome"])
        if forma in REDONDAS:
            p["profundidade"] = p["largura"]
        if math.prod(p["repetir"]) > MAX_COPIAS:
            p["repetir"] = [1, 1, 1]
        pecas.append(p)
    return str(dados.get("objeto", "")).strip() or "o objeto", pecas


def _tamanho(p, eixo):
    """(X, Y, Z) da peça. Cilindro deitado: o diâmetro (largura) fica em pé e o comprimento (altura) deitado."""
    if eixo == "x":
        return p["altura"], p["largura"], p["largura"]
    if eixo == "y":
        return p["largura"], p["altura"], p["largura"]
    return p["largura"], p["profundidade"], p["altura"]


def _colocar(p, ref):
    """Cópias da peça p, posta em relação à caixa `ref` da peça 'de' (None = sem referência)."""
    posicao, giro = p["posicao"], 0
    eixo = "y" if p["deitado"] else "z"
    if ref is not None and posicao == "rodas" and ref[3] - ref[2] > ref[1] - ref[0]:
        eixo = "x"  # o objeto é comprido em Y: as rodas giram em volta de X
    largura, prof, altura = _tamanho(p, eixo)
    nx, ny, nz = p["repetir"]
    espaco = p["espaco"]
    # a grade de cópias é posta como um bloco só: as contas abaixo usam o tamanho do bloco
    gl, gp, ga = (n * t + (n - 1) * espaco for n, t in ((nx, largura), (ny, prof), (nz, altura)))
    chao = p["altura_do_chao"]
    lugares = []  # (x, y, base) do bloco
    if ref is None or posicao == "centro":
        lugares = [(0.0, 0.0, chao or 0.0)]
    else:
        x0, x1, y0, y1, z0, z1 = ref
        cx, cy, alto = (x0 + x1) / 2, (y0 + y1) / 2, z1 - z0
        if posicao.startswith("em_cima"):
            x = {"em_cima_esquerda": x0 + gl / 2, "em_cima_direita": x1 - gl / 2}.get(posicao, cx)
            y = {"em_cima_frente": y0 + gp / 2, "em_cima_tras": y1 - gp / 2}.get(posicao, cy)
            lugares = [(x, y, z1)]
        elif posicao.startswith("embaixo"):
            if z0 > 0.01:  # vai do chão até a peça de cima
                altura = ga = z0
            if posicao == "embaixo_centro":
                lugares = [(cx, cy, 0.0)]
            else:
                recuo = min(0.02, (x1 - x0) * 0.05)
                ax, ay = x1 - x0 - gl - 2 * recuo, y1 - y0 - gp - 2 * recuo
                lugares = [(cx + sx * ax / 2, cy + sy * ay / 2, 0.0) for sy in (-1, 1) for sx in (-1, 1)]
        elif posicao == "rodas":
            # metade da roda para fora da lateral, perto das pontas do lado comprido
            if eixo == "y":
                dx = max((x1 - x0) / 2 - gl * 0.9, 0)
                lugares = [(cx + sx * dx, y, 0.0) for sx in (-1, 1) for y in (y0, y1)]
            else:
                dy = max((y1 - y0) / 2 - gp * 0.9, 0)
                lugares = [(x, cy + sy * dy, 0.0) for sy in (-1, 1) for x in (x0, x1)]
        elif posicao.startswith("ao_lado"):
            lado = posicao.removeprefix("ao_lado_")
            if p["forma"] == "alca":
                # as pontas da alça entram um pouco na parede para não ficar uma fresta
                encaixe = min(0.004, altura / 8)
                x, y, giro = {"direita": (x1 - encaixe, cy, 0), "esquerda": (x0 + encaixe, cy, 180),
                              "frente": (cx, y0 + encaixe, -90), "tras": (cx, y1 - encaixe, 90)}[lado]
            else:
                x, y = {"direita": (x1 + gl / 2, cy), "esquerda": (x0 - gl / 2, cy),
                        "frente": (cx, y0 - gp / 2), "tras": (cx, y1 + gp / 2)}[lado]
            base = z0 + chao if chao is not None else z0 + max(alto - ga, 0) / 2  # padrão: na meia altura
            lugares = [(x, y, base)]
        elif posicao == "no_meio":
            lugares = [(cx, cy, z0 + (alto - ga) / 2)]
        else:  # dentro
            largura, prof = min(largura, 0.9 * (x1 - x0)), min(prof, 0.9 * (y1 - y0))
            base = z0 + min(0.01, alto * 0.1) if ga <= alto else z0 + alto / 2
            lugares = [(cx, cy, base)]
        if chao is not None and posicao.startswith(("em_cima", "dentro", "no_meio")):
            lugares = [(x, y, z0 + chao) for x, y, _ in lugares]
    mx, my, mz = p["mover"]
    lugares = [(x + mx, y + my, base + mz) for x, y, base in lugares]
    copias = []
    for x, y, base in lugares:
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    copias.append(Colocada(
                        p["nome"], p["forma"], largura, prof, altura,
                        x + (i - (nx - 1) / 2) * (largura + espaco), y + (j - (ny - 1) / 2) * (prof + espaco),
                        base + k * (altura + espaco), p["cor"], p["topo"], giro, eixo))
    if len(copias) > 1:
        for n, c in enumerate(copias, 1):
            c.nome = f"{c.nome}{n}"
    return copias


def resolver(pecas):
    """Posição de todas as peças, seguindo as referências ('de'). Referência que não existe ou que
    forma um ciclo vira "sem referência": a peça vai para o centro da cena em vez de travar."""
    por_nome = {p["nome"]: p for p in pecas}
    feitas, visitando = {}, set()

    def caixa_de(nome):
        if nome not in por_nome or nome in visitando:
            return None
        if nome not in feitas:
            visitando.add(nome)
            p = por_nome[nome]
            ref = caixa_de(p["de"]) if p["posicao"] != "centro" and p["de"] != nome else None
            feitas[nome] = _colocar(p, ref)
            visitando.discard(nome)
        return _juntar([c.caixa() for c in feitas[nome]])

    for p in pecas:
        caixa_de(p["nome"])
    return [c for p in pecas for c in feitas[p["nome"]]]


# --- código ---

def _n(valor):
    return f"{round(valor, 4):g}"


def _linha(c):
    cor = repr(CORES[c.cor])
    lugar = "".join(f", {k}={_n(v)}" for k, v in (("x", c.x), ("y", c.y), ("base", c.base)) if abs(v) > 1e-9)
    if c.forma == "caixa":
        return f"caixa({c.nome!r}, {_n(c.largura)}, {_n(c.profundidade)}, {_n(c.altura)}{lugar}, cor={cor})"
    if c.forma == "telhado":
        return f"telhado({c.nome!r}, {_n(c.largura)}, {_n(c.profundidade)}, {_n(c.altura)}{lugar}, cor={cor})"
    if c.forma == "piramide":
        topo = "" if c.topo == 0 else f", topo={_n(c.topo)}"
        return f"piramide({c.nome!r}, {_n(c.largura)}, {_n(c.profundidade)}, {_n(c.altura)}{lugar}, cor={cor}{topo})"
    if c.forma == "esfera":
        return f"esfera({c.nome!r}, {_n(c.largura / 2)}{lugar}, cor={cor}, achatar={_n(c.altura / c.largura)})"
    if c.forma == "alca":
        grossura = max(0.003, c.altura * 0.08)
        return (f"alca({c.nome!r}, {_n(c.altura / 2 - grossura)}, {_n(grossura)}{lugar}, cor={cor}, "
                f"giro={c.giro})")
    if c.eixo != "z":  # cilindro deitado: o diâmetro é a altura da caixa; o comprimento fica no eixo
        comprimento = c.largura if c.eixo == "x" else c.profundidade
        return (f"cilindro({c.nome!r}, {_n(c.altura / 2)}, {_n(comprimento)}{lugar}, cor={cor}, "
                f"eixo={c.eixo!r})")
    raio = c.largura / 2
    topo = "" if c.topo == 1 and c.forma != "cone" else f", raio_topo={_n(raio * c.topo)}"
    return f"{c.forma}({c.nome!r}, {_n(raio)}, {_n(c.altura)}{lugar}, cor={cor}{topo})"


def montar(texto):
    """JSON da LLM -> (código, frase do que foi criado). ValueError se a ficha não servir."""
    dados = json.loads(texto)
    objeto, pecas = _limpar(dados)
    colocadas = resolver(pecas)
    descricao = " ".join(str(dados.get("descricao", "")).split())  # numa linha só: vira comentário
    codigo = (f"# {objeto}: {descricao}\n"
              "# (ficha de peças preenchida pela LLM; posições calculadas pelo Modelia)\n"
              + "\n".join(_linha(c) for c in colocadas))
    nomes = ", ".join(p["nome"] for p in pecas)
    quantas = "1 peça" if len(colocadas) == 1 else f"{len(colocadas)} peças"
    return codigo, f"Pronto! Criei {objeto} com {quantas} ({nomes})."
