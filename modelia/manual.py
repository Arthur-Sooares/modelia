"""Indexa o Manual do Blender 5.1 na base de conhecimento (RF8), para o Explicar responder com ele.

    uv run python -m modelia.manual

Baixa o manual em HTML (docs.blender.org, ~350 MB, uma vez só), extrai o texto do artigo de cada
página das seções de modelagem e interface e indexa com o título da página e o link oficial como fonte.
O manual tem licença CC BY-SA 4.0: pode ser usado citando a fonte, e o Explicar sempre cita.
Cerca de 20% das páginas da versão em português ainda estão em inglês; ficam assim mesmo
(a LLM responde em português).
"""

import html
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

from modelia import config

VERSAO = "5.1"
ENDERECO = f"https://docs.blender.org/manual/pt/{VERSAO}/"
PASTA = config.PASTA_DADOS / f"manual_blender_{VERSAO}_pt"
# Só o que um curso de modelagem usa; o resto (animação, vídeo, composição...) só traria trechos errados.
SECOES = ["getting_started", "interface", "editors/3dview", "scene_layout", "modeling/meshes",
          "modeling/modifiers", "glossary"]

_BLOCOS = re.compile(r"</?(p|div|section|h[1-6]|li|ul|ol|dt|dd|dl|tr|table|pre|br|figure|figcaption)\b[^>]*>", re.I)


def texto_da_pagina(conteudo):
    """(título, texto) do artigo de uma página do manual, sem menu, rodapé e marcação."""
    artigo = re.search(r'<article role="main"[^>]*>(.*?)</article>', conteudo, re.S)
    if not artigo:
        return "", ""
    corpo = re.sub(r'<a class="headerlink".*?</a>', "", artigo.group(1), flags=re.S)
    titulo = re.search(r"<h1[^>]*>(.*?)</h1>", corpo, re.S)
    titulo = html.unescape(re.sub(r"<[^>]+>", "", titulo.group(1))).strip() if titulo else ""
    corpo = _BLOCOS.sub("\n\n", corpo)
    texto = html.unescape(re.sub(r"<[^>]+>", "", corpo))
    linhas = (re.sub(r"[ \t]+", " ", linha).strip() for linha in texto.splitlines())
    return titulo, re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


def baixar(pasta=PASTA):
    """Baixa e extrai o manual, se ainda não estiver na pasta. Devolve a raiz do HTML."""
    raiz = pasta / "html" / f"blender_manual_v{VERSAO.replace('.', '')}0_pt.html"
    if not raiz.exists():
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / "manual.zip"
        if not arquivo.exists():
            print("Baixando o manual (~350 MB)...", flush=True)
            urllib.request.urlretrieve(ENDERECO + "blender_manual_html.zip", arquivo)
        with zipfile.ZipFile(arquivo) as z:
            z.extractall(pasta / "html")
    return raiz


def paginas(raiz, secoes=SECOES):
    """(título, texto, link oficial) de cada página das seções escolhidas."""
    for secao in secoes:
        for caminho in sorted((raiz / secao).rglob("*.html")):
            titulo, texto = texto_da_pagina(caminho.read_text(encoding="utf-8", errors="replace"))
            if len(texto) >= 200:  # páginas-índice só têm a lista de links
                yield titulo, texto, ENDERECO + caminho.relative_to(raiz).as_posix()


def importar(conhecimento, raiz):
    total = 0
    for i, (titulo, texto, link) in enumerate(paginas(raiz), 1):
        total += conhecimento.adicionar_texto(texto, f"Manual do Blender: {titulo}", link)
        if i % 50 == 0:
            print(f"{i} páginas, {total} trechos", flush=True)
    return total


if __name__ == "__main__":
    from modelia.conhecimento import Conhecimento

    raiz = baixar()
    if "--medir" in sys.argv:  # tamanho de cada seção, sem indexar
        for secao in SECOES:
            lista = list(paginas(raiz, [secao]))
            print(f"{secao}: {len(lista)} páginas, {sum(len(t) for _, t, _ in lista) / 1e6:.2f} M caracteres")
    else:
        print(f"Pronto: {importar(Conhecimento(), raiz)} trechos indexados.")
