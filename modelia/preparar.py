"""Prepara um computador novo para o Modelia, sem sair do app: Ollama, modelos e manual do Blender.

    uv run python -m modelia.preparar

As etapas são geradores que devolvem (fração de 0 a 1 ou None, texto do status), para a interface
mostrar a barra de progresso. Cada etapa pula o que já está pronto, então dá para rodar de novo
depois de uma queda de internet: o download continua do ponto em que parou.
"""

import base64
import functools
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from modelia import config, llm, manual

INSTALADOR_WINDOWS = "https://ollama.com/download/OllamaSetup.exe"
PASTA_INSTALADORES = config.PASTA_DADOS / "instaladores"
AGENTE = "Modelia/0.1 (+https://github.com/Arthur-Sooares/modelia)"
INSTRUCOES = {
    "linux": "No terminal: curl -fsSL https://ollama.com/install.sh | sh",
    "darwin": "Baixe o Ollama em https://ollama.com/download/mac e arraste para Aplicativos.",
}


def baixar_arquivo(endereco, destino):
    """Baixa com progresso. Usa um arquivo .part e continua de onde parou se a internet cair."""
    destino = Path(destino)
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".part")
    feito = parcial.stat().st_size if parcial.exists() else 0
    # O docs.blender.org recusa (403) o identificador padrão do Python ("Python-urllib").
    cabecalhos = {"User-Agent": AGENTE} | ({"Range": f"bytes={feito}-"} if feito else {})
    pedido = urllib.request.Request(endereco, headers=cabecalhos)
    with urllib.request.urlopen(pedido, timeout=60) as resposta:
        if feito and resposta.status != 206:  # o servidor não aceita continuar: começa do zero
            feito = 0
        total = int(resposta.headers.get("Content-Length", 0)) + feito
        with open(parcial, "ab" if feito else "wb") as arquivo:
            while bloco := resposta.read(1 << 20):
                arquivo.write(bloco)
                feito += len(bloco)
                yield (feito / total if total else None), f"Baixando {destino.name}: {feito / 1e6:.0f} MB"
    parcial.replace(destino)
    return destino


# --- Ollama ---

def executavel_ollama():
    """Caminho do ollama.exe, ou None. Logo depois de instalar ele ainda não está no PATH deste processo."""
    achado = shutil.which("ollama")
    if achado:
        return Path(achado)
    padrao = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return padrao if sys.platform == "win32" and padrao.exists() else None


def ligar_ollama(espera=30):
    """Abre o Ollama em segundo plano, se estiver instalado e desligado. Devolve se ficou disponível."""
    if llm.disponivel():
        return True
    exe = executavel_ollama()
    if not exe:
        return False
    # O servidor direto, sem o app da bandeja: num notebook o app abria e o servidor dele não subia (20/10/2026).
    comando = [str(exe), "serve"]
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(comando, creationflags=sem_janela, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(espera):
        time.sleep(1)
        if llm.disponivel():
            return True
    return False


def instalar_ollama():
    """Instala o Ollama (só no Windows; nos outros sistemas avisa o comando). Depois liga."""
    if llm.disponivel():
        yield 1, "Ollama já está instalado e ligado."
        return
    if not executavel_ollama():
        if sys.platform != "win32":
            raise RuntimeError(f"Instale o Ollama: {INSTRUCOES.get(sys.platform, 'veja https://ollama.com')}")
        instalador = PASTA_INSTALADORES / "OllamaSetup.exe"
        yield from baixar_arquivo(INSTALADOR_WINDOWS, instalador)
        yield None, "Instalando o Ollama (leva cerca de 1 minuto)..."
        # Instala só para este usuário, sem pedir senha de administrador. /VERYSILENT: sem janelas.
        resultado = subprocess.run([str(instalador), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"])
        if resultado.returncode != 0 or not executavel_ollama():
            raise RuntimeError(f"O instalador do Ollama falhou (código {resultado.returncode}). "
                               f"Tente abrir {instalador} com dois cliques.")
        instalador.unlink(missing_ok=True)  # ~1 GB que não serve mais
    yield None, "Ligando o Ollama..."
    if not ligar_ollama():
        raise RuntimeError("O Ollama foi instalado, mas não ligou. Abra o aplicativo Ollama pelo menu Iniciar.")
    yield 1, "Ollama instalado e ligado."


# --- modelos ---

def modelos_faltando():
    instalados = {m["nome"] for m in llm.modelos()}
    return [m for m in config.MODELOS_NECESSARIOS
            if m not in instalados and f"{m}:latest" not in instalados]


def baixar_modelos():
    """Baixa os modelos que o Modelia precisa (chat e embeddings). O Ollama continua downloads parados."""
    faltando = modelos_faltando()
    for i, nome in enumerate(faltando, 1):
        for fracao, status in llm.baixar(nome):
            yield fracao, f"Modelo {i} de {len(faltando)} ({nome}): {status}"
    yield 1, "Modelos prontos."


# --- manual do Blender ---

def paginas_do_manual(conhecimento):
    return [d for d in conhecimento.listar() if d["titulo"].startswith(manual.TITULO)]


def manual_indexado(conhecimento):
    """Se o manual está na base inteiro (uma indexação que parou no meio não conta)."""
    indexadas = paginas_do_manual(conhecimento)
    return bool(indexadas) and not manual.faltando(indexadas)


def preparar_manual(conhecimento, zip_proprio=None, pasta_propria=None):
    """Baixa o manual (ou usa o .zip ou a pasta já extraída que a pessoa tem) e indexa no modo Explicar."""
    if manual_indexado(conhecimento):
        yield 1, "O manual do Blender já está na base."
        return
    if pasta_propria:  # lê direto da pasta, sem copiar (o manual extraído tem mais de 2 GB)
        raiz, versao, idioma = manual.achar_pasta(pasta_propria)
        yield None, f"Lendo o manual {versao} ({idioma}) da pasta escolhida…"
        yield from manual.indexar(conhecimento, raiz, manual.endereco(versao, idioma))
        aviso = "" if versao == manual.VERSAO else f" Atenção: é o manual {versao}; o do curso é o {manual.VERSAO}."
        yield 1, f"Manual do Blender {versao} indexado.{aviso}"
        return
    if not zip_proprio and manual.EMBUTIDO.exists():  # o texto já vem no projeto: sem internet
        falta = manual.faltando(paginas_do_manual(conhecimento)) or None  # continua de onde parou
        if falta:
            yield None, f"Continuando o manual: faltam {len(falta)} páginas…"
        else:
            yield None, "Lendo o manual que vem com o Modelia…"
        yield from manual.indexar(conhecimento, lista=falta)
        yield 1, "Manual do Blender indexado."
        return
    if zip_proprio:
        manual.usar_zip(zip_proprio)
    elif not manual.raiz().exists():
        yield from baixar_arquivo(manual.ENDERECO + "blender_manual_html.zip", manual.PASTA / "manual.zip")
    yield None, "Abrindo o manual..."
    raiz = manual.extrair()
    yield from manual.indexar(conhecimento, raiz)
    yield 1, "Manual do Blender indexado."


# --- atalho na área de trabalho (Windows): abre o Modelia com dois cliques, sem terminal ---

PASTA_ATALHO = Path(__file__).parent / "atalho"


def _powershell(script):
    # -EncodedCommand (UTF-16 em base64): aspas e espaços dos caminhos chegam intactos ao PowerShell.
    # A saída sai em UTF-8 (sem isso, "Área de Trabalho" chegava com o acento trocado).
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script
    codificado = base64.b64encode(script.encode("utf-16-le")).decode()
    resultado = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
                               capture_output=True, text=True, encoding="utf-8",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if resultado.returncode != 0:
        raise RuntimeError(f"Não consegui criar o atalho: {resultado.stderr.strip()[:300]}")
    return resultado.stdout.strip()


@functools.cache  # a tela Instalação pergunta a cada atualização; o PowerShell leva quase meio segundo
def caminho_do_atalho():
    """Modelia.lnk na área de trabalho (que pode estar dentro do OneDrive), ou None fora do Windows."""
    if sys.platform != "win32":
        return None
    return Path(_powershell("[Environment]::GetFolderPath('Desktop')")) / "Modelia.lnk"


def criar_atalho():
    """Cria (ou refaz) o atalho: wscript.exe roda atalho/abrir.vbs, com o ícone do Modelia."""
    destino = caminho_do_atalho()
    if destino is None:
        raise RuntimeError("O atalho só é criado no Windows. Nos outros sistemas, rode: uv run main.py")
    aspas = lambda p: str(p).replace("'", "''")  # noqa: E731 (texto entre aspas simples no PowerShell)
    _powershell(
        "$a = (New-Object -ComObject WScript.Shell).CreateShortcut('" + aspas(destino) + "'); "
        "$a.TargetPath = 'wscript.exe'; "
        "$a.Arguments = '\"" + aspas(PASTA_ATALHO / "abrir.vbs") + "\"'; "
        "$a.WorkingDirectory = '" + aspas(PASTA_ATALHO.parent.parent) + "'; "
        "$a.IconLocation = '" + aspas(PASTA_ATALHO / "modelia.ico") + "'; "
        "$a.Description = 'Abrir o Modelia'; $a.Save()"
    )
    return destino


def situacao(conhecimento):
    """O que falta preparar, para a tela de instalação."""
    ligado = llm.disponivel()
    return {
        "ollama_instalado": ligado or executavel_ollama() is not None,
        "ollama_ligado": ligado,
        "modelos_faltando": modelos_faltando() if ligado else list(config.MODELOS_NECESSARIOS),
        "manual": manual_indexado(conhecimento),
    }


def preparar_tudo(conhecimento):
    yield from instalar_ollama()
    yield from baixar_modelos()
    yield from preparar_manual(conhecimento)


if __name__ == "__main__":
    from modelia.conhecimento import Conhecimento

    for _, texto in preparar_tudo(Conhecimento()):
        print(texto, flush=True)
