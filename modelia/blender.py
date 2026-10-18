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
    pontos = [obj.matrix_world @ v.co for v in obj.data.vertices] or [obj.location]
    xs, ys, zs = ([p[i] for p in pontos] for i in range(3))
    malhas.append({
        "nome": obj.name,
        "dim": sorted(round(d, 3) for d in obj.dimensions),
        "local": [round(c, 3) for c in obj.location],
        "z_min": round(min(zs), 3),
        "z_max": round(max(zs), 3),
        "xy": [round(min(xs), 3), round(max(xs), 3), round(min(ys), 3), round(max(ys), 3)],
    })
outros = [
    {"nome": o.name, "tipo": o.type, "subtipo": o.data.type if o.type == 'LIGHT' else "",
     "local": [round(c, 3) for c in o.location]}
    for o in bpy.context.scene.objects if o.type != 'MESH'
]
result = {"malhas": malhas, "outros": outros}
"""

# Centraliza todas as malhas na viewport, numa vista 3/4 de frente, para a captura mostrar o objeto
# inteiro (na vista do aluno ele pode estar pequeno ou fora do quadro). A seleção do aluno volta igual.
CODIGO_ENQUADRAR = """
import bpy, math, mathutils
malhas = [o for o in bpy.context.scene.objects if o.type == 'MESH']
feito = False
suave = bpy.context.preferences.view.smooth_view
bpy.context.preferences.view.smooth_view = 0  # sem animação: a captura vem logo depois
for janela in bpy.context.window_manager.windows:
    for area in janela.screen.areas:
        if feito or area.type != 'VIEW_3D' or not malhas:
            continue
        regiao = next(r for r in area.regions if r.type == 'WINDOW')
        vista = area.spaces.active.region_3d
        vista.view_perspective = 'PERSP'
        vista.view_rotation = mathutils.Euler((math.radians(65), 0, math.radians(35))).to_quaternion()
        with bpy.context.temp_override(window=janela, area=area, region=regiao):
            selecionados = list(bpy.context.selected_objects)
            for o in bpy.context.scene.objects:
                o.select_set(o in malhas)
            bpy.ops.view3d.view_selected()
            for o in bpy.context.scene.objects:
                o.select_set(o in selecionados)
        feito = True
bpy.context.preferences.view.smooth_view = suave
result = {"ok": feito}
"""


# Funções prontas que vão antes do código da LLM no modo Demonstrar. Elas fazem a matemática
# (centro, altura da base) e o acabamento (chanfro, sombreamento suave, material), que os
# modelos pequenos erravam ou esqueciam. A malha é montada direto nos dados, sem bpy.ops,
# para não depender do contexto da interface do Blender.
AJUDANTES = '''
import bpy, contextlib, math

def _cor_linear(cor):
    cor = cor.lstrip("#")
    srgb = [int(cor[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb] + [1.0]

def _material(cor):
    nome = "Modelia " + cor.upper()
    mat = bpy.data.materials.get(nome) or bpy.data.materials.new(nome)
    rgba = _cor_linear(cor)
    mat.diffuse_color = rgba
    try:
        mat.use_nodes = True
    except AttributeError:
        pass
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = 0.55
    return mat

_deslocamento = [0.0, 0.0]

@contextlib.contextmanager
def ao_lado(dx=0, dy=0):
    _deslocamento[0] += dx
    _deslocamento[1] += dy
    try:
        yield
    finally:
        _deslocamento[0] -= dx
        _deslocamento[1] -= dy

def _objeto(nome, verts, faces, x, y, base, cor, chanfro, giro):
    malha = bpy.data.meshes.new(nome)
    malha.from_pydata(verts, [], faces)
    malha.update()
    malha.polygons.foreach_set("use_smooth", [True] * len(malha.polygons))
    obj = bpy.data.objects.new(nome, malha)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (x + _deslocamento[0], y + _deslocamento[1], base)
    obj.rotation_euler[2] = math.radians(giro)
    obj.data.materials.append(_material(cor))
    if chanfro > 0:
        mod = obj.modifiers.new("Chanfro", "BEVEL")
        mod.width = chanfro
        mod.segments = 3
        mod.limit_method = "ANGLE"
        mod.harden_normals = True
    return obj

def caixa(nome, largura, profundidade, altura, x=0, y=0, base=0, cor="#B8B8B8", chanfro=0.01, giro=0):
    l, p = largura / 2, profundidade / 2
    verts = [(-l, -p, 0), (l, -p, 0), (l, p, 0), (-l, p, 0),
             (-l, -p, altura), (l, -p, altura), (l, p, altura), (-l, p, altura)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    chanfro = min(chanfro, min(largura, profundidade, altura) / 4)
    return _objeto(nome, verts, faces, x, y, base, cor, chanfro, giro)

def cilindro(nome, raio, altura, x=0, y=0, base=0, cor="#B8B8B8", chanfro=0.005, raio_topo=None,
             eixo="z", lados=32):
    topo = raio if raio_topo is None else raio_topo
    def anel(r):
        return [(r * math.cos(2 * math.pi * i / lados), r * math.sin(2 * math.pi * i / lados)) for i in range(lados)]
    if eixo == "z":  # em pé, de base até base + altura
        verts = [(a, b, 0) for a, b in anel(raio)] + [(a, b, altura) for a, b in anel(topo)]
    else:  # deitado, centrado em x e y ao longo do eixo, com a parte de baixo em base
        r = max(raio, topo)
        pontas = [(a, b, -altura / 2) for a, b in anel(raio)] + [(a, b, altura / 2) for a, b in anel(topo)]
        if eixo == "x":
            verts = [(h, a, b + r) for a, b, h in pontas]
        else:
            verts = [(a, h, b + r) for a, b, h in pontas]
    faces = [tuple(reversed(range(lados))), tuple(range(lados, 2 * lados))]
    faces += [(i, (i + 1) % lados, lados + (i + 1) % lados, lados + i) for i in range(lados)]
    if eixo == "y":  # trocar y com z espelha a malha; inverte as faces para as normais apontarem para fora
        faces = [tuple(reversed(f)) for f in faces]
    chanfro = min(chanfro, max(min(raio, topo), 0.001) / 4, altura / 4)
    return _objeto(nome, verts, faces, x, y, base, cor, chanfro, 0)

def cone(nome, raio, altura, x=0, y=0, base=0, cor="#B8B8B8", raio_topo=0.0):
    return cilindro(nome, raio, altura, x, y, base, cor, 0, raio_topo=max(raio_topo, 0.0005))

def esfera(nome, raio, x=0, y=0, base=0, cor="#B8B8B8", achatar=1.0, aneis=16, lados=32):
    """achatar < 1 deixa a esfera mais baixa (almofada, cúpula); a altura fica 2 * raio * achatar."""
    verts = [(0, 0, 0)]
    for i in range(1, aneis):
        fi = math.pi * i / aneis
        for j in range(lados):
            te = 2 * math.pi * j / lados
            verts.append((raio * math.sin(fi) * math.cos(te), raio * math.sin(fi) * math.sin(te),
                          raio * achatar * (1 - math.cos(fi))))
    verts.append((0, 0, 2 * raio * achatar))
    faces = []
    for j in range(lados):
        faces.append((0, 1 + (j + 1) % lados, 1 + j))
    for i in range(aneis - 2):
        a, b = 1 + i * lados, 1 + (i + 1) * lados
        for j in range(lados):
            faces.append((a + j, a + (j + 1) % lados, b + (j + 1) % lados, b + j))
    ultimo, polo = 1 + (aneis - 2) * lados, len(verts) - 1
    for j in range(lados):
        faces.append((ultimo + j, ultimo + (j + 1) % lados, polo))
    return _objeto(nome, verts, faces, x, y, base, cor, 0, 0)

def limpar_cena():
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        bpy.data.objects.remove(obj, do_unlink=True)

'''


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

    def enquadrar(self):
        return self.rodar_codigo_interno(CODIGO_ENQUADRAR)

    def executar_com_ajudantes(self, codigo):
        """Modo Demonstrar: o código da LLM roda depois das funções prontas (AJUDANTES)."""
        return self.executar(AJUDANTES + codigo)

    def remover_malhas_exceto(self, nomes):
        """Desfaz uma tentativa do Demonstrar: apaga as malhas que não estavam na cena antes."""
        codigo = (f"import bpy\nmanter = {sorted(nomes)!r}\n"
                  "for o in [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.name not in manter]:\n"
                  "    bpy.data.objects.remove(o, do_unlink=True)\nresult = {'ok': True}")
        self.rodar_codigo_interno(codigo)

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
