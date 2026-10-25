"""Objetos 3D flutuantes da página inicial do Modelia, um PNG transparente por objeto.

Cada objeto vem das tarefas que o Modelia ensina (cubo, mesa, cena com luz) ou da
linguagem do Blender (gizmo de mover, malha em aramado, balão do chat, cursor).

Rodar:  blender -b -P objetos.py
Saída:  objetos/<nome>.png
"""
import math
import os

import bpy

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "objetos")
LADO = 900


def srgb(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def limpar():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def material(nome, cor, rug=0.4, metal=0.0, emissao=0.0):
    m = bpy.data.materials.new(nome)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*srgb(cor), 1)
    p.inputs["Roughness"].default_value = rug
    p.inputs["Metallic"].default_value = metal
    if emissao:
        p.inputs["Emission Color"].default_value = (*srgb(cor), 1)
        p.inputs["Emission Strength"].default_value = emissao
    return m


def suave(o, mat=None, chanfro=0.0):
    if chanfro:
        b = o.modifiers.new("Bevel", "BEVEL")
        b.width = chanfro
        b.segments = 5
    if mat:
        o.data.materials.append(mat)
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.shade_smooth()
    return o


def montar_cena(dist=4.2, alvo=(0, 0, 0), lente=60):
    cena = bpy.context.scene
    alvo_obj = bpy.data.objects.new("Alvo", None)
    alvo_obj.location = alvo
    cena.collection.objects.link(alvo_obj)
    for nome, energia, cor, pos in (("Principal", 260, (1, 0.94, 0.88), (3, -3, 4)),
                                    ("Contraluz", 520, (0.45, 0.55, 1), (-3, 3, 2.5)),
                                    ("Preenche", 90, (1, 1, 1), (-3, -3, 1))):
        d = bpy.data.lights.new(nome, "AREA")
        d.energy, d.color, d.size = energia, cor, 3
        o = bpy.data.objects.new(nome, d)
        o.location = pos
        cena.collection.objects.link(o)
        o.constraints.new("TRACK_TO").target = alvo_obj
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = lente
    cam.location = (dist * 0.7, -dist * 0.7, dist * 0.55)
    cena.collection.objects.link(cam)
    cam.constraints.new("TRACK_TO").target = alvo_obj
    cena.camera = cam
    mundo = bpy.data.worlds.new("Mundo")
    mundo.use_nodes = True
    mundo.node_tree.nodes["Background"].inputs["Color"].default_value = (0.02, 0.02, 0.03, 1)
    cena.world = mundo
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


def render(nome):
    bpy.context.scene.render.filepath = os.path.join(PASTA, nome + ".png")
    bpy.ops.render.render(write_still=True)


LARANJA, AZUL, BRANCO, TINTA = "FF6A13", "3D5AFE", "F4F4F6", "1F1F26"


def cubo():
    limpar()
    bpy.ops.mesh.primitive_cube_add(size=1.6)
    o = suave(bpy.context.object, material("L", LARANJA, 0.35), 0.12)
    o.rotation_euler = (0.35, 0.2, 0.6)
    montar_cena()
    render("cubo")


def esfera_luz():
    limpar()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.85, segments=64, ring_count=32, location=(0, 0, -0.2))
    suave(bpy.context.object, material("A", AZUL, 0.25))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.16, segments=32, ring_count=16, location=(0.75, -0.2, 1.0))
    suave(bpy.context.object, material("Luz", "FFE2C2", 0.2, emissao=12))
    d = bpy.data.lights.new("Ponto", "POINT")
    d.energy, d.color = 120, (1, 0.8, 0.6)
    p = bpy.data.objects.new("Ponto", d)
    p.location = (0.75, -0.2, 1.0)
    bpy.context.scene.collection.objects.link(p)
    montar_cena(alvo=(0.2, 0, 0.2))
    render("esfera_luz")


def gizmo():
    limpar()
    eixos = (((1, 0, 0), (0, math.pi / 2, 0), LARANJA), ((0, 1, 0), (-math.pi / 2, 0, 0), AZUL),
             ((0, 0, 1), (0, 0, 0), BRANCO))
    for direcao, rot, cor in eixos:
        mat = material("E" + cor, cor, 0.3)
        centro = [d * 0.55 for d in direcao]
        bpy.ops.mesh.primitive_cylinder_add(radius=0.06, depth=1.1, location=centro, rotation=rot)
        suave(bpy.context.object, mat)
        ponta = [d * 1.25 for d in direcao]
        bpy.ops.mesh.primitive_cone_add(radius1=0.18, depth=0.4, location=ponta, rotation=rot)
        suave(bpy.context.object, mat)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.16, segments=32, ring_count=16)
    suave(bpy.context.object, material("C", TINTA, 0.4))
    montar_cena(dist=4.6, alvo=(0.35, 0.35, 0.45))
    render("gizmo")


def aramado():
    limpar()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0)
    o = bpy.context.object
    vertices = [o.matrix_world @ v.co for v in o.data.vertices]
    w = o.modifiers.new("Arame", "WIREFRAME")
    w.thickness = 0.035
    suave(o, material("W", BRANCO, 0.4))
    ponto = material("V", LARANJA, 0.3, emissao=1.5)
    for v in vertices:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.055, segments=16, ring_count=8, location=v)
        suave(bpy.context.object, ponto)
    montar_cena()
    render("aramado")


def balao():
    limpar()
    bpy.ops.mesh.primitive_cube_add(size=1)
    b = bpy.context.object
    b.scale = (1.7, 0.45, 1.1)
    bpy.ops.object.transform_apply(scale=True)
    mat = material("B", LARANJA, 0.35)
    suave(b, mat, 0.22)
    bpy.ops.mesh.primitive_cone_add(radius1=0.28, depth=0.55, location=(-0.45, 0, -0.72),
                                    rotation=(math.pi, 0.35, 0))
    suave(bpy.context.object, mat)
    bpy.ops.object.text_add(location=(0, -0.24, -0.02), rotation=(math.pi / 2, 0, 0))
    t = bpy.context.object
    t.data.body = "?"
    t.data.extrude = 0.06
    t.data.size = 0.9
    t.data.align_x = "CENTER"
    t.data.align_y = "CENTER"
    t.data.materials.append(material("T", TINTA, 0.4))
    montar_cena(dist=4.4)
    bpy.context.scene.camera.location = (1.4, -4.0, 1.0)
    render("balao")


def toro():
    limpar()
    bpy.ops.mesh.primitive_torus_add(major_radius=0.85, minor_radius=0.3, major_segments=64, minor_segments=32)
    o = suave(bpy.context.object, material("T", AZUL, 0.3))
    o.rotation_euler = (1.0, 0.3, 0.4)
    montar_cena()
    render("toro")


def cursor():
    limpar()
    import bmesh
    pontos = [(0, 0), (0, -1.6), (0.38, -1.22), (0.66, -1.85), (0.9, -1.74), (0.62, -1.12), (1.15, -1.1)]
    malha = bpy.data.meshes.new("Cursor")
    bm = bmesh.new()
    vs = [bm.verts.new((x, 0, y)) for x, y in pontos]
    face = bm.faces.new(vs)
    r = bmesh.ops.extrude_face_region(bm, geom=[face])
    for v in [e for e in r["geom"] if isinstance(e, bmesh.types.BMVert)]:
        v.co.y += 0.22
    bm.to_mesh(malha)
    o = bpy.data.objects.new("Cursor", malha)
    bpy.context.scene.collection.objects.link(o)
    suave(o, material("C", BRANCO, 0.35), 0.03)
    o.location = (-0.5, 0, 0.9)
    o.rotation_euler = (0.2, 0, -0.3)
    montar_cena(alvo=(0, 0, 0))
    render("cursor")


def mesa_arame():
    limpar()
    partes = [((2.0, 1.0, 0.1), (0, 0, 0.75))]
    partes += [((0.1, 0.1, 0.7), (sx * 0.9, sy * 0.4, 0.35)) for sx in (-1, 1) for sy in (-1, 1)]
    mat = material("W", LARANJA, 0.3, emissao=2)
    for tam, pos in partes:
        bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
        o = bpy.context.object
        o.scale = tam
        bpy.ops.object.transform_apply(scale=True)
        w = o.modifiers.new("Arame", "WIREFRAME")
        w.thickness = 0.025
        suave(o, mat)
    montar_cena(dist=5.2, alvo=(0, 0, 0.45))
    render("mesa_arame")


for f in (cubo, esfera_luz, gizmo, aramado, balao, toro, cursor, mesa_arame):
    f()
print("PRONTO")
