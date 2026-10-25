"""Etapas da modelagem para a página inicial: o mesmo objeto do vértice ao render.

Saída (PNG transparente com sombra no chão, via shadow catcher do Cycles):
  etapas/pontos.png   só os vértices
  etapas/arame.png    aramado + vértices
  etapas/argila.png   forma sem cor (argila)
  etapas/final.png    materiais finais
  etapas/selecao.png  final com contorno laranja de seleção, como no Blender

Rodar:  blender -b -P etapas.py
"""
import math
import os

import bpy

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etapas")
LADO = 1600


def srgb(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def material(nome, cor, rug=0.45, emissao=0.0):
    m = bpy.data.materials.new(nome)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*srgb(cor), 1)
    p.inputs["Roughness"].default_value = rug
    if emissao:
        p.inputs["Emission Color"].default_value = (*srgb(cor), 1)
        p.inputs["Emission Strength"].default_value = emissao
    return m


bpy.ops.wm.read_factory_settings(use_empty=True)
cena = bpy.context.scene
col = cena.collection

LARANJA = material("Laranja", "FF6A13", 0.62)
AZUL = material("Azul", "3D5AFE", 0.55)
TINTA = material("Tinta", "23232A", 0.5)
ARGILA = material("Argila", "C9CCD3", 0.75)
ARAME = material("Arame", "16161A", 0.5)
PONTO = material("Ponto", "FF6A13", 0.3, emissao=0.6)

# (nome, tamanho, posição, material final)
PECAS = [("Tampo", (2.0, 1.0, 0.1), (0, 0, 0.75), LARANJA)]
PECAS += [("Perna", (0.09, 0.09, 0.7), (sx * 0.88, sy * 0.4, 0.35), TINTA) for sx in (-1, 1) for sy in (-1, 1)]
PECAS += [("Assento", (0.55, 0.55, 0.07), (0.25, -0.95, 0.47), AZUL),
          ("Encosto", (0.55, 0.07, 0.6), (0.25, -1.2, 0.8), AZUL)]
PECAS += [("PernaC", (0.06, 0.06, 0.45), (0.25 + sx * 0.22, -0.95 + sy * 0.22, 0.225), TINTA)
          for sx in (-1, 1) for sy in (-1, 1)]

moveis = bpy.data.collections.new("Moveis")
col.children.link(moveis)
objetos = []
for nome, tam, pos, mat in PECAS:
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    o = bpy.context.object
    o.name = nome
    o.scale = tam
    bpy.ops.object.transform_apply(scale=True)
    o["mat_final"] = mat.name
    col.objects.unlink(o)
    moveis.objects.link(o)
    objetos.append(o)

# VAO: sobe as pernas da mesa (erro que o Verificar aponta), em metros
VAO = float(os.environ.get("VAO", "0"))
for o in objetos:
    if o.name.startswith("Perna") and not o.name.startswith("PernaC"):
        o.location.z += VAO

# vértices do modelo base (antes do chanfro), para as etapas de pontos e arame
pontos = []
for o in objetos:
    for v in o.data.vertices:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.028, segments=16, ring_count=8, location=o.matrix_world @ v.co)
        p = bpy.context.object
        p.data.materials.append(PONTO)
        bpy.ops.object.shade_smooth()
        pontos.append(p)

# chão que só recebe sombra
bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
chao = bpy.context.object
chao.is_shadow_catcher = True

# luz de estúdio suave
mundo = bpy.data.worlds.new("Mundo")
mundo.use_nodes = True
mundo.node_tree.nodes["Background"].inputs["Color"].default_value = (0.9, 0.91, 0.93, 1)
mundo.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.32
cena.world = mundo
alvo = bpy.data.objects.new("Alvo", None)
alvo.location = (0, -0.25, 0.42)
col.objects.link(alvo)
for nome, energia, pos, tam in (("Chave", 320, (3.5, -3.5, 5.5), 4), ("Recorte", 260, (-4, 3, 3.5), 3)):
    d = bpy.data.lights.new(nome, "AREA")
    d.energy, d.size = energia, tam
    o = bpy.data.objects.new(nome, d)
    o.location = pos
    col.objects.link(o)
    o.constraints.new("TRACK_TO").target = alvo

cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
cam.data.lens = 58
cam.location = (4.4, -4.9, 2.4)
col.objects.link(cam)
cam.constraints.new("TRACK_TO").target = alvo
cena.camera = cam

cena.render.engine = "CYCLES"
cena.cycles.samples = 96
cena.cycles.use_denoising = True
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "CUDA"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    cena.cycles.device = "GPU"
except Exception:
    cena.cycles.device = "CPU"
cena.render.film_transparent = True
cena.render.resolution_x = cena.render.resolution_y = LADO
cena.render.image_settings.file_format = "PNG"
cena.render.image_settings.color_mode = "RGBA"
cena.view_settings.view_transform = "Standard"


def preparar(etapa):
    for o in objetos:
        o.modifiers.clear()
        o.data.materials.clear()
        o.hide_render = etapa == "pontos"
        if etapa == "arame":
            w = o.modifiers.new("Arame", "WIREFRAME")
            w.thickness = 0.018
            o.data.materials.append(ARAME)
        elif etapa in ("argila", "final", "selecao"):
            b = o.modifiers.new("Chanfro", "BEVEL")
            b.width, b.segments = 0.018, 4
            o.data.materials.append(ARGILA if etapa == "argila" else bpy.data.materials[o["mat_final"]])
    for p in pontos:
        p.hide_render = etapa not in ("pontos", "arame")
    cena.render.use_freestyle = etapa == "selecao"
    if etapa == "selecao":
        lineset = cena.view_layers[0].freestyle_settings.linesets
        ls = lineset[0] if len(lineset) else lineset.new("Contorno")
        ls.select_silhouette, ls.select_border, ls.select_crease = True, True, False
        ls.select_by_collection = True
        ls.collection = moveis
        if ls.linestyle is None:
            ls.linestyle = bpy.data.linestyles.new("Contorno")
        ls.linestyle.color = srgb("FF6A13")
        ls.linestyle.thickness = 4.0


ETAPAS = os.environ.get("ETAPAS", "pontos,arame,argila,final,selecao").split(",")
for etapa in ETAPAS:
    preparar(etapa)
    nome = os.environ.get("SAIDA", etapa)
    cena.render.filepath = os.path.join(PASTA, nome + ".png")
    bpy.ops.render.render(write_still=True)
print("PRONTO")
