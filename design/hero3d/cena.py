"""Objeto 3D da página inicial do Modelia: mesa + cadeira + primitivas, girando 360°.

Rodar:  blender -b -P cena.py
Saída:  quadros/q_0001.png ... (fundo transparente) e modelia_hero.glb
"""
import math
import os

import bpy

PASTA = os.path.dirname(os.path.abspath(__file__))
QUADROS = 48
LADO = 720

bpy.ops.wm.read_factory_settings(use_empty=True)
cena = bpy.context.scene


def material(nome, cor, rugosidade=0.35, emissao=0.0):
    m = bpy.data.materials.new(nome)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*cor, 1)
    p.inputs["Roughness"].default_value = rugosidade
    if emissao:
        p.inputs["Emission Color"].default_value = (*cor, 1)
        p.inputs["Emission Strength"].default_value = emissao
    return m


def srgb(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


LARANJA = material("Laranja", srgb("FF6A13"), 0.5)
AZUL = material("Azul", srgb("3D5AFE"), 0.45)
TINTA = material("Tinta", srgb("1F1F26"), 0.5)
BRANCO = material("Branco", srgb("F4F4F6"), 0.4)

raiz = bpy.data.objects.new("Raiz", None)
cena.collection.objects.link(raiz)


def caixa(nome, tam, pos, mat, chanfro=0.02):
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    o = bpy.context.object
    o.name = nome
    o.scale = tam
    bpy.ops.object.transform_apply(scale=True)
    b = o.modifiers.new("Bevel", "BEVEL")
    b.width = chanfro
    b.segments = 4
    o.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    o.parent = raiz
    return o


# mesa: tampo laranja, pernas escuras (a mesma tarefa do modo Demonstrar)
caixa("Tampo", (1.2, 0.8, 0.06), (0, 0, 0.75), LARANJA, 0.025)
for sx in (-1, 1):
    for sy in (-1, 1):
        caixa("Perna", (0.07, 0.07, 0.72), (sx * 0.5, sy * 0.3, 0.36), TINTA)

# cadeira azul
caixa("Assento", (0.45, 0.45, 0.05), (0.15, -0.75, 0.45), AZUL)
caixa("Encosto", (0.45, 0.05, 0.5), (0.15, -0.95, 0.72), AZUL)
for sx in (-1, 1):
    for sy in (-1, 1):
        caixa("PernaCadeira", (0.045, 0.045, 0.43), (0.15 + sx * 0.18, -0.75 + sy * 0.18, 0.215), TINTA)

# primitivas flutuando, como quem está aprendendo a modelar
bpy.ops.mesh.primitive_torus_add(major_radius=0.18, minor_radius=0.06, location=(-0.6, 0.45, 1.35))
t = bpy.context.object
t.data.materials.append(AZUL)
bpy.ops.object.shade_smooth()
t.rotation_euler = (math.radians(60), 0, math.radians(20))
t.parent = raiz
caixa("Cubo", (0.22, 0.22, 0.22), (0.5, 0.4, 1.3), BRANCO, 0.03).rotation_euler = (0.5, 0.6, 0.2)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.13, location=(0.05, 0.25, 1.55), segments=48, ring_count=24)
s = bpy.context.object
s.data.materials.append(LARANJA)
bpy.ops.object.shade_smooth()
s.parent = raiz

# luzes: principal quente, contraluz azul
def luz(nome, tipo, energia, cor, pos, alvo=(0, -0.1, 0.75)):
    d = bpy.data.lights.new(nome, tipo)
    d.energy = energia
    d.color = cor
    if tipo == "AREA":
        d.size = 3
    o = bpy.data.objects.new(nome, d)
    o.location = pos
    cena.collection.objects.link(o)
    r = o.constraints.new("TRACK_TO")
    alvo_obj = bpy.data.objects.get("Alvo") or bpy.data.objects.new("Alvo", None)
    if alvo_obj.name not in cena.collection.objects:
        cena.collection.objects.link(alvo_obj)
    alvo_obj.location = alvo
    r.target = alvo_obj


luz("Principal", "AREA", 220, (1, 0.93, 0.85), (3, -3, 4))
luz("Contraluz", "AREA", 500, (0.45, 0.55, 1), (-3, 3, 3))
luz("Preenchimento", "AREA", 80, (1, 1, 1), (-3, -3, 1.5))

cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
cam.data.lens = 62
cam.location = (3.6, -3.6, 2.6)
cena.collection.objects.link(cam)
r = cam.constraints.new("TRACK_TO")
r.target = bpy.data.objects["Alvo"]
cena.camera = cam

mundo = bpy.data.worlds.new("Mundo")
mundo.use_nodes = True
mundo.node_tree.nodes["Background"].inputs["Color"].default_value = (0.02, 0.02, 0.03, 1)
mundo.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
cena.world = mundo

# giro de 360° sem emenda: o último quadro é o anterior ao primeiro
cena.frame_start, cena.frame_end = 1, QUADROS
raiz.rotation_euler = (0, 0, 0)
raiz.keyframe_insert("rotation_euler", index=2, frame=1)
raiz.rotation_euler = (0, 0, math.radians(360))
raiz.keyframe_insert("rotation_euler", index=2, frame=QUADROS + 1)
for fc in (raiz.animation_data.action.fcurves if hasattr(raiz.animation_data.action, "fcurves") else []):
    for k in fc.keyframe_points:
        k.interpolation = "LINEAR"

for motor in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    try:
        cena.render.engine = motor
        break
    except TypeError:
        continue
cena.render.film_transparent = True
cena.render.resolution_x = cena.render.resolution_y = LADO
cena.render.image_settings.file_format = "PNG"
cena.render.image_settings.color_mode = "RGBA"
cena.view_settings.view_transform = "Standard"
cena.render.filepath = os.path.join(PASTA, "quadros", "q_")
bpy.ops.render.render(animation=True)

bpy.ops.export_scene.gltf(filepath=os.path.join(PASTA, "modelia_hero.glb"), export_animations=True)
print("PRONTO")
