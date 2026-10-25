"""Indexa o Manual do Blender 5.1 na base de conhecimento (RF8), para o Explicar responder com ele.

    uv run python -m modelia.manual

O texto das páginas usadas (seções de modelagem e interface, ~0,3 MB) já vem no projeto, em
manual_embutido/: indexar não precisa de internet. Para gerar esse arquivo de novo (nova versão do manual):

    uv run python -m modelia.manual --embutir

Isso baixa o manual em HTML (docs.blender.org, ~350 MB), extrai o texto do artigo de cada página e
guarda com o título e o link oficial, que vira a fonte de cada trecho.
O manual tem licença CC BY-SA 4.0: pode ser usado citando a fonte, e o Explicar sempre cita.
Cerca de 20% das páginas da versão em português ainda estão em inglês; ficam assim mesmo
(a LLM responde em português).
"""

import gzip
import html
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

from modelia import config

VERSAO = "5.1"
ENDERECO = f"https://docs.blender.org/manual/pt/{VERSAO}/"
TITULO = "Manual do Blender"
PASTA = config.PASTA_DADOS / f"manual_blender_{VERSAO}_pt"
EMBUTIDO = Path(__file__).parent / "manual_embutido" / f"manual_blender_{VERSAO}_pt.json.gz"
# Só o que um curso de modelagem usa; o resto (animação, vídeo, composição...) só traria trechos errados.
SECOES = ["getting_started", "interface", "editors/3dview", "scene_layout", "modeling/meshes",
          "modeling/modifiers", "glossary"]

# Nome da página inicial do manual: blender_manual_v510_pt.html = versão 5.1, português.
_NOME_DO_MANUAL = re.compile(r"blender_manual_v(\d)(\d)\d_([a-z]{2}(?:_[a-z]+)?)\.html$", re.I)
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


def raiz(pasta=PASTA):
    """Página inicial do manual extraído."""
    return pasta / "html" / f"blender_manual_v{VERSAO.replace('.', '')}0_pt.html"


def extrair(pasta=PASTA):
    """Extrai o manual.zip da pasta, se ainda não foi extraído. Devolve a raiz do HTML."""
    if not raiz(pasta).exists():
        with zipfile.ZipFile(pasta / "manual.zip") as z:
            z.extractall(pasta / "html")
    return raiz(pasta)


def usar_zip(caminho, pasta=PASTA):
    """Usa um manual.zip que a pessoa já baixou (blender_manual_html.zip do docs.blender.org)."""
    erro = ValueError(f"Esse arquivo não é o manual do Blender {VERSAO} em português (HTML). "
                      f"Baixe em {ENDERECO}blender_manual_html.zip")
    if not zipfile.is_zipfile(caminho):
        raise erro
    with zipfile.ZipFile(caminho) as z:
        if not any(n.startswith(raiz(pasta).name + "/") for n in z.namelist()):
            raise erro
    pasta.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(caminho, pasta / "manual.zip")


def endereco(versao=VERSAO, idioma="pt"):
    """Endereço oficial do manual dessa versão e idioma (vai como fonte de cada trecho)."""
    return f"https://docs.blender.org/manual/{idioma}/{versao}/"


def achar_pasta(caminho):
    """Para um manual já extraído numa pasta: devolve (raiz do HTML, versão, idioma).

    A raiz é a pasta com modeling/ e interface/; aceita ela ou a pasta de cima (até dois níveis).
    A versão e o idioma vêm do nome blender_manual_v500_pt.html. Levanta ValueError explicando o problema."""
    caminho = Path(str(caminho).strip().strip('"').strip())
    if not caminho.is_dir():
        raise ValueError(f"Não achei a pasta “{caminho}”. Confira o caminho "
                         "(copie da barra de endereço do Explorador de Arquivos).")
    nivel, candidatas = [caminho], [caminho]
    for _ in range(2):
        nivel = [filha for pasta in nivel for filha in sorted(pasta.iterdir()) if filha.is_dir()]
        candidatas += nivel
    for pasta in candidatas:
        if (pasta / "modeling").is_dir() and (pasta / "interface").is_dir():
            for nome in [pasta.name, *(f.name for f in pasta.iterdir())]:
                achado = _NOME_DO_MANUAL.match(nome)
                if achado:
                    return pasta, f"{achado[1]}.{achado[2]}", achado[3].lower()
            return pasta, VERSAO, "pt"
    raise ValueError("Essa pasta não parece o manual do Blender em HTML: não achei as pastas modeling e interface. "
                     "Use a pasta que tem o arquivo blender_manual_v…_pt.html.")


def baixar(pasta=PASTA):
    """Baixa e extrai o manual, se ainda não estiver na pasta. Devolve a raiz do HTML."""
    if not raiz(pasta).exists() and not (pasta / "manual.zip").exists():
        from modelia.preparar import baixar_arquivo

        print("Baixando o manual (~350 MB)...", flush=True)
        for _ in baixar_arquivo(ENDERECO + "blender_manual_html.zip", pasta / "manual.zip"):
            pass
    return extrair(pasta)


def paginas(raiz, secoes=SECOES, link_base=ENDERECO):
    """(título, texto, link oficial) de cada página das seções escolhidas."""
    for secao in secoes:
        for caminho in sorted((raiz / secao).rglob("*.html")):
            titulo, texto = texto_da_pagina(caminho.read_text(encoding="utf-8", errors="replace"))
            if len(texto) >= 200:  # páginas-índice só têm a lista de links
                yield titulo, texto, link_base + caminho.relative_to(raiz).as_posix()


def embutir(raiz, destino=EMBUTIDO):
    """Guarda no projeto só o texto das páginas usadas, para o Modelia indexar sem baixar o manual."""
    lista = [{"titulo": titulo, "texto": texto, "link": link} for titulo, texto, link in paginas(raiz)]
    destino.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: o arquivo só muda no Git quando o texto muda
    with open(destino, "wb") as bruto, gzip.GzipFile(fileobj=bruto, mode="wb", mtime=0) as arquivo:
        arquivo.write(json.dumps(lista, ensure_ascii=False, indent=0).encode("utf-8"))
    return len(lista)


def paginas_embutidas(arquivo=None):
    with gzip.open(arquivo or EMBUTIDO, "rt", encoding="utf-8") as entrada:
        return [(p["titulo"], p["texto"], p["link"]) for p in json.load(entrada)]


def faltando(indexadas):
    """Páginas do manual embutido que ainda não estão na base (a indexação parou no meio).
    `indexadas`: os documentos do manual que já estão na base. Um manual de outra versão conta como completo."""
    if not EMBUTIDO.exists() or not any(d["fonte"].startswith(ENDERECO) for d in indexadas):
        return []
    feitas = {(d["titulo"], d["fonte"]) for d in indexadas}
    return [p for p in paginas_embutidas() if (f"{TITULO}: {p[0]}", p[2]) not in feitas]


def indexar(conhecimento, raiz=None, link_base=ENDERECO, lista=None):
    """Indexa as páginas. É um gerador: devolve (fração de 0 a 1, texto do status) a cada página.
    Sem `raiz`, usa o texto que vem embutido no projeto; com `lista`, só essas páginas."""
    if lista is None:
        lista = paginas_embutidas() if raiz is None else list(paginas(raiz, link_base=link_base))
    if not lista:
        raise ValueError("Não achei páginas do manual nessa pasta (interface, modelagem, modificadores).")
    total = 0
    for i, (titulo, texto, link) in enumerate(lista, 1):
        total += conhecimento.adicionar_texto(texto, f"{TITULO}: {titulo}", link)
        yield i / len(lista), f"Indexando o manual: página {i} de {len(lista)} ({total} trechos)"
    return total


def importar(conhecimento, raiz):
    """Como indexar, mas mostrando o status no terminal. Devolve quantos trechos criou."""
    gerador, i = indexar(conhecimento, raiz), 0
    while True:
        try:
            _, status = next(gerador)
        except StopIteration as fim:
            return fim.value
        i += 1
        if i % 50 == 0:
            print(status, flush=True)


if __name__ == "__main__":
    from modelia.conhecimento import Conhecimento

    if "--embutir" in sys.argv:
        print(f"Pronto: {embutir(baixar())} páginas guardadas em {EMBUTIDO}.")
        sys.exit()
    inicio = None if EMBUTIDO.exists() and "--medir" not in sys.argv else baixar()
    if "--medir" in sys.argv:  # tamanho de cada seção, sem indexar
        for secao in SECOES:
            lista = list(paginas(inicio, [secao]))
            print(f"{secao}: {len(lista)} páginas, {sum(len(t) for _, t, _ in lista) / 1e6:.2f} M caracteres")
    else:
        print(f"Pronto: {importar(Conhecimento(), inicio)} trechos indexados.")
