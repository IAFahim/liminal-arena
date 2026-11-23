"""Where the cameras stand and what is written on the pictures. `render_blockout.py` and
`render_liminal.py` both import it, so a grey view and a dressed one are the same photograph.

Every number is in metres, the GLB's units, in the scripts' axes: x east, y north, z up.
"""
import math
import sys

import bpy
import mathutils

EYE = 1.6                                     # a standing player's eyes
V, C, K = (0.52, 0.22, 0.66), (0.05, 0.55, 0.36), (0.05, 0.05, 0.05)   # inks: vantage, counter, plain
WHITE = (1, 1, 1)

SUN, B_SUN = (-0.55, -0.65, 0.55), (-0.6, -0.3, 0.74)   # the 3D views' sun; site B's, from the west, so
                                                         # that its courtyard is lit but its faces differ
# where a player stands for each shot: eye, target, lens, lamps (x, y, z, size, watts), sun
VIEWS = {
    "mid":     ((-1.5, -16, EYE), (0, 3, 2.6), 20, (), SUN),
    "lane":    ((-35.8, 1, EYE), (-31.5, 18, 3.4), 20, (), SUN),
    "lookout": ((-26.5, 16.7, 2 + EYE), (-35.6, 8.9, 1.7), 30, (), SUN),    # leaning over its parapet, down the A ramp
    "store":   ((-27.5, -13.5, EYE), (-14, -3, 2.2), 18, ((-22, -11, 4.7, 8, 700), (-14, -5.5, 4.7, 6, 400)), SUN),
    "deck":    ((-3.5, 0, 2.5 + EYE + 0.1), (3, 11, 2.2), 20, (), SUN),
    "b":       ((21.5, -22, EYE), (29.2, -12.8, 1.0), 18, (), B_SUN),      # in B main: the gatehouse window, the walk
    "walk":    ((37.5, -19.5, 2 + EYE), (23, -23, 1.2), 22, (), B_SUN),
}

PLAN, TACTICS = ((0, 0), 84, (1800, 1260)), ((0, -8.1), 82, (1800, 1655))   # the plan's centre, its ortho scale, the picture
AERIAL = ((-33.3, -50.5, 80), (-4.4, -7.2, 0), 30, (1800, 1480))            # where it stands, what it looks at, its lens, the picture
EYE_SHOT = (1800, 1012)

# callouts lying over the plan, as a designer would pencil them: text, x, y, size, turn
PLAN_LABELS = [
    ("SITE A", -33.5, 20, 2.0, 0), ("A STAIRS", -18, 25, 1.2, 0), ("RAMP", -34.5, 12, 1.8, 0),
    ("YARD", -28.2, 13.8, 1.2, 0), ("LOOKOUT", -26.5, 19.3, 1.05, 0),
    ("BOILER", -23, 13, 0.95, 0), ("OFFICE", -15.5, 13, 1.7, 0), ("STORE", -23, -12, 3.0, 0),
    ("PUMP ROOM", -22, 1.5, 1.6, 0), ("LOFT", -13.5, -7.5, 1.8, 90),
    ("WORKSHOP", 21, 12.5, 2.2, 0), ("PLANT ROOM", 22, -1.5, 1.6, 0), ("LOFT", 13.5, 7.5, 1.8, 90),
    ("GATE\nHOUSE", 23, -13, 1.1, 0), ("LOBBY", 15.5, -13, 1.6, 0),
    ("DECK", 0.5, 1, 2.2, 0), ("UNDERPASS", 3.2, -5, 1.15, 0), ("STAIRS", 3, 7, 1.1, 90),
    ("RAMP", -3, -7.5, 1.5, 90), ("MID", 0, 13, 3.0, 0), ("MID", 0, -13.5, 3.0, 0),
    ("WEST PASSAGE", -20, 7, 1.5, 0), ("EAST PASSAGE", 20, -7, 1.5, 0),
    ("SITE B", 28.5, -19.6, 2.2, 0), ("WALK", 37.5, -22, 1.5, 90), ("STAIRS", 37.5, -14, 0.9, 90),
    ("B MAIN", 22.8, -17.4, 1.2, 0),
    ("WEST LANE", -34.5, -2, 1.7, 90), ("EAST LANE", 34.5, 2, 1.7, 90),
    ("NORTH STRIP", 0, 21.5, 2.3, 0), ("SOUTH STRIP", 0, -21.5, 2.3, 0),
    ("ATTACK\nSPAWN", -31, -22.5, 2.2, 0), ("DEFEND\nSPAWN", 31, 22.5, 2.2, 0),
]

# names painted on the surfaces they belong to: text, x, y, z, size, turn, backing. The pads' and
# the caption's sit where no roof or wall hides them.
AERIAL_LABELS = [
    ("SITE A", -33.5, 20.2, 2.09, 2.6, 0, WHITE),
    ("SITE B", 28.5, -17.7, 0.09, 2.4, 0, WHITE),
    ("ATTACK SPAWN", -29.8, -20.6, 0.09, 1.8, 0, WHITE), ("DEFEND SPAWN", 31, 22.5, 0.09, 1.8, 0, WHITE),
    ("DECK", 0, 1, 2.54, 2.0, 0, None), ("WALK", 38.05, -21.5, 2.04, 1.0, 0, None),
    ("LOOKOUT", -26.5, 19, 5.54, 1.5, 0, None), ("STORE", -21, -7, 5.54, 3.4, 0, None),
    ("WORKSHOP", 21, 6, 5.54, 3.0, 0, None), ("BOILER", -23, 13, 5.54, 1.2, 0, None),
    ("OFFICE", -15.5, 13, 5.54, 1.9, 0, None), ("LOBBY", 15.5, -13, 5.54, 1.9, 0, None),
    ("GATEHOUSE", 23, -13, 5.54, 1.05, 0, None),
]

_FACE = None


def load(glb):
    """The GLB in an empty scene: (the scene, its meshes by name, where the marker plates stand)."""
    global _FACE
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _FACE = None                               # the empty file takes bpy.data with it, a loaded font too
    bpy.ops.import_scene.gltf(filepath=glb)
    scene = bpy.context.scene
    meshes = {o.name: o for o in scene.objects if o.type == 'MESH'}
    marks = {n: o.matrix_world.translation.copy() for n, o in meshes.items()
             if len(n) == 2 and n[0] in "vc" and n[1].isdigit()}
    return scene, meshes, marks


def hide(meshes, marks, *prefixes):
    """The plates are labels, not level. What else goes: every mesh whose name starts with one of
    `prefixes` -- a plan drops the roofs, to show the rooms."""
    for n, o in meshes.items():
        o.hide_render = n in marks or any(n.startswith(p) for p in prefixes)


def font():
    """A bold face for the labels, from wherever this machine keeps one; Blender's own if none."""
    global _FACE
    if _FACE is None:
        for path in ("/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                     "C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
            try:
                _FACE = bpy.data.fonts.load(path)
                break
            except Exception:
                pass
    return _FACE


def ink(rgb):
    """An unlit, flat colour, for everything drawn on top of the model."""
    mat = bpy.data.materials.new("ink")
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    glow = mat.node_tree.nodes.new('ShaderNodeEmission')
    glow.inputs['Color'].default_value = (*rgb, 1)
    mat.node_tree.links.new(glow.outputs[0], mat.node_tree.nodes.new('ShaderNodeOutputMaterial').inputs[0])
    return mat


def shaded(name, rgb, rough=0.9):
    """A plain lit surface."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Base Color'].default_value = (*rgb, 1)
    bsdf.inputs['Roughness'].default_value = rough
    return mat


def overlay(ob, mat):
    ob.visible_shadow = ob.visible_diffuse = ob.visible_glossy = False   # no ghosting
    ob.data.materials.append(mat)
    return ob


def text(body, x, y, z, size, mat, rot=0, left=False, back=None, halo=0.0):
    """Type lying flat at (x, y, z). `back`, a colour, prints the same type a little fatter
    underneath -- by a fourteenth of its size, or by `halo` metres where that is more -- so that
    it reads where sightlines, shadows or textures cross it."""
    scene = bpy.context.scene
    layers = [(0, mat, 0)] if back is None else [(max(size * 0.07, halo), ink(back), -0.02), (0, mat, 0)]
    for offset, paint, lift in layers:
        curve = bpy.data.curves.new(body, type='FONT')
        curve.body, curve.size, curve.font, curve.offset = body, size, font(), offset
        curve.align_x, curve.align_y = 'LEFT' if left else 'CENTER', 'CENTER'
        ob = bpy.data.objects.new(body, curve)
        scene.collection.objects.link(ob)
        ob.location, ob.rotation_euler = (x, y, z + lift), (0, 0, math.radians(rot))
        overlay(ob, paint)


def tile(x, y, z, w, d, mat, rot=0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z))
    ob = bpy.context.object
    ob.scale, ob.rotation_euler = (w, d, 0.04), (0, 0, rot)
    return overlay(ob, mat)


def line(a, b, mat, width=0.14, dash=None):
    """A flat rule from a to b, whole, or in dashes of (dash, gap) metres."""
    a, b = mathutils.Vector(a), mathutils.Vector(b)
    heading, angle, step = (b - a).length, math.atan2(b.y - a.y, b.x - a.x), 0.0
    while step < heading:
        end = heading if dash is None else min(step + dash[0], heading)
        mid = a.lerp(b, (step + end) / 2 / heading)
        tile(mid.x, mid.y, mid.z, end - step, width, mat, angle)
        step = end if dash is None else end + dash[1]


def camera(mode, view="mid"):
    """The scene's camera for `mode` -- `plan`, `tactics`, `aerial`, or `eye`, standing at `VIEWS[view]`.
    Returns (the picture's size, the sun the grey views give it, its lamps)."""
    scene = bpy.context.scene
    data = bpy.data.cameras.new("cam")
    data.clip_end = 2000
    cam = bpy.data.objects.new("cam", data)
    scene.collection.objects.link(cam)
    if mode == "plan":                         # straight down, north up: the map's own drawing
        data.type, data.ortho_scale = 'ORTHO', PLAN[1]
        cam.location, size, sun, lamps = (*PLAN[0], 300), PLAN[2], SUN, ()
    elif mode == "tactics":                    # the same, with room under it for a legend
        data.type, data.ortho_scale = 'ORTHO', TACTICS[1]
        cam.location, size, sun, lamps = (*TACTICS[0], 300), TACTICS[2], SUN, ()
    elif mode == "aerial":                     # three-quarter, from behind the attackers
        data.lens, cam.location = AERIAL[2], mathutils.Vector(AERIAL[0])
        cam.rotation_euler = (mathutils.Vector(AERIAL[1]) - cam.location).to_track_quat('-Z', 'Y').to_euler()
        size, sun, lamps = AERIAL[3], (-0.45, -0.5, 0.72), ()
    else:                                      # eye level, as a player sees it
        standing, target, data.lens, lamps, sun = VIEWS[view]
        cam.location = mathutils.Vector(standing)
        cam.rotation_euler = (mathutils.Vector(target) - cam.location).to_track_quat('-Z', 'Y').to_euler()
        size = EYE_SHOT
        dg = bpy.context.evaluated_depsgraph_get()
        if any(scene.ray_cast(dg, cam.location, mathutils.Vector(d), distance=0.2)[0]
               for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))):
            raise SystemExit(f"the '{view}' camera stands inside the model, at {standing}")
    scene.camera = cam
    return size, sun, lamps


def plan_names(back=None, halo=0.0):
    for body, x, y, s, rot in PLAN_LABELS:
        text(body, x, y, 12, s, ink(K), rot, back=back, halo=halo)


def aerial_names(backed=False):
    """The aerial's names, each on the surface it belongs to. `backed` prints white under all of
    them, for surfaces too busy to take bare type."""
    for body, x, y, z, s, rot, back in AERIAL_LABELS:
        text(body, x, y, z, s, ink(K), rot, back=WHITE if backed else back)


def cycles(scene, samples):
    """Cycles, on the GPU where there is one."""
    scene.render.engine = 'CYCLES'
    try:
        scene.cycles.device = 'GPU'
        prefs = bpy.context.preferences.addons['cycles'].preferences
        for kind in ('OPTIX', 'CUDA'):       # the type must be chosen before devices count
            try:
                prefs.compute_device_type = kind
                break
            except TypeError:
                continue
        prefs.get_devices()
        for dt in prefs.devices:
            dt.use = dt.type in ('OPTIX', 'CUDA')
        print("device:", prefs.compute_device_type, [d.name for d in prefs.devices if d.use])
    except Exception as e:
        scene.cycles.device = 'CPU'
        print("GPU setup failed, CPU:", e)
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True


def still(scene, out, size, percent=100):
    """One picture, `size` pixels wide and tall less `percent`, a JPEG when the name says so."""
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.resolution_percentage = percent
    scene.render.image_settings.file_format = 'JPEG' if out.lower().endswith((".jpg", ".jpeg")) else 'PNG'
    scene.render.image_settings.quality = 92
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
