"""Cliente MCP do Blender (RNF8).

    Modelia  --(MCP, stdio)-->  servidor blmcp  --(porta 9876)-->  add-on dentro do Blender

O SDK do MCP é assíncrono, mas o Gradio chama funções comuns. Por isso a sessão MCP
vive numa thread própria, com seu próprio laço asyncio, e as funções desta classe
enviam o trabalho para lá e esperam a resposta.
"""

import asyncio
import base64
import io
import json
import socket
import threading
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from modelia import config

# Ferramentas que só leem a cena. São as únicas oferecidas nos modos Verificar (RF7) e no
# início de cada passo do Demonstrar.
FERRAMENTAS_LEITURA = {"get_objects_summary", "get_object_detail_summary"}
FERRAMENTA_EXECUTAR = "execute_blender_code"

# Mede as malhas da cena: dimensões e altura do topo e da base de cada uma.
CODIGO_MEDIR = """
import bpy
malhas = []
for obj in bpy.context.scene.objects:
    if obj.type != 'MESH':
        continue
    zs = [(obj.matrix_world @ v.co).z for v in obj.data.vertices] or [0.0]
    malhas.append({
        "nome": obj.name,
        "dim": sorted(round(d, 3) for d in obj.dimensions),
        "local": [round(c, 3) for c in obj.location],
        "z_min": round(min(zs), 3),
        "z_max": round(max(zs), 3),
    })
outros = [
    {"nome": o.name, "tipo": o.type, "subtipo": o.data.type if o.type == 'LIGHT' else "",
     "local": [round(c, 3) for c in o.location]}
    for o in bpy.context.scene.objects if o.type != 'MESH'
]
result = {"malhas": malhas, "outros": outros}
"""


def blender_escutando(host="127.0.0.1", porta=config.PORTA_BLENDER, espera=0.5):
    """Teste rápido: o add-on do Blender está aceitando conexões?"""
    try:
        with socket.create_connection((host, porta), timeout=espera):
            return True
    except OSError:
        return False


def texto_do_resultado(resultado):
    partes = [c.text for c in resultado.content if getattr(c, "text", None)]
    return "\n".join(partes) or "(sem texto)"


def resultado_teve_erro(resultado, texto):
    if resultado.isError:
        return True
    try:
        return json.loads(texto).get("status") == "error"
    except (ValueError, AttributeError):
        return False


class Blender:
    def __init__(self):
        self._laco = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._laco.run_forever, name="mcp-blender", daemon=True)
        self._thread.start()
        self._pilha = None
        self._sessao = None
        self._ferramentas = []
        self._trava = threading.Lock()

    # --- infraestrutura ---

    def _rodar(self, corrotina, limite=120):
        futuro = asyncio.run_coroutine_threadsafe(corrotina, self._laco)
        return futuro.result(timeout=limite + 5)

    async def _abrir(self):
        pilha = AsyncExitStack()
        servidor = StdioServerParameters(command=config.MCP_PYTHON, args=config.MCP_ARGS)
        leitura, escrita = await pilha.enter_async_context(stdio_client(servidor))
        sessao = await pilha.enter_async_context(ClientSession(leitura, escrita))
        await sessao.initialize()
        self._ferramentas = (await sessao.list_tools()).tools
        self._pilha, self._sessao = pilha, sessao

    async def _fechar(self):
        if self._pilha:
            try:
                await self._pilha.aclose()
            except Exception:  # o servidor pode já ter morrido; não há o que fazer
                pass
        self._pilha = self._sessao = None

    def conectar(self):
        """Abre a sessão MCP se ainda não houver uma. Levanta ConnectionError com instrução clara."""
        with self._trava:
            if self._sessao:
                return
            if not blender_escutando():
                raise ConnectionError(
                    "O Blender não está conectado. Abra o Blender 5.1 e ligue o add-on MCP "
                    f"(porta {config.PORTA_BLENDER})."
                )
            try:
                self._rodar(self._abrir(), limite=60)
            except Exception as erro:
                self._rodar(self._fechar())
                raise ConnectionError(
                    f"Não consegui iniciar o servidor MCP do Blender ({erro}). "
                    "Confira se o pacote blender-mcp está instalado no projeto."
                ) from erro

    def desconectar(self):
        with self._trava:
            self._rodar(self._fechar())

    def disponivel(self):
        return blender_escutando()

    def ferramentas(self, nomes):
        """Descrições das ferramentas pedidas, no formato que o Ollama espera."""
        self.conectar()
        return [
            {"type": "function",
             "function": {"name": f.name, "description": f.description or "", "parameters": f.inputSchema}}
            for f in self._ferramentas if f.name in nomes
        ]

    def chamar(self, nome, argumentos, limite=120):
        """Chama uma ferramenta MCP. Devolve (texto, teve_erro).

        Se a conexão caiu (o Blender foi fechado), tenta reconectar uma vez.
        """
        for tentativa in range(2):
            self.conectar()
            try:
                resultado = self._rodar(
                    asyncio.wait_for(self._sessao.call_tool(nome, argumentos), timeout=limite), limite)
                texto = texto_do_resultado(resultado)
                return texto, resultado_teve_erro(resultado, texto)
            except (ConnectionError, OSError, EOFError, RuntimeError):
                self.desconectar()
                if tentativa:
                    raise

    # --- operações usadas pelo Modelia ---

    def executar(self, codigo):
        """RF6: só é chamada depois do clique em "Executar"."""
        return self.chamar(FERRAMENTA_EXECUTAR, {"code": codigo})

    def rodar_codigo_interno(self, codigo):
        """Código escrito pelo próprio Modelia (medir, limpar, abrir cena). Devolve o `result`."""
        texto, erro = self.executar(codigo)
        dados = json.loads(texto)
        if erro:
            raise RuntimeError(dados.get("message", texto))
        return dados.get("result")

    def medir_cena(self):
        return self.rodar_codigo_interno(CODIGO_MEDIR)

    def abrir_cena(self, caminho):
        """RF10: reabre a cena base limpa antes de cada execução do experimento."""
        codigo = f"import bpy\nbpy.ops.wm.open_mainfile(filepath={str(caminho)!r})\nresult = {{'ok': True}}"
        try:
            self.rodar_codigo_interno(codigo)
        except (ValueError, ConnectionError, OSError):
            # Abrir outro arquivo pode derrubar a resposta; basta reconectar.
            self.desconectar()

    def captura(self):
        """Imagem PIL da viewport 3D, ou None se não der para capturar."""
        from PIL import Image

        try:
            self.conectar()
            resultado = self._rodar(self._sessao.call_tool(
                "get_screenshot_of_area_as_image", {"area_ui_type": "VIEW_3D"}), limite=30)
        except Exception:
            return None
        for conteudo in resultado.content:
            if getattr(conteudo, "type", "") == "image":
                return Image.open(io.BytesIO(base64.b64decode(conteudo.data)))
        return None
