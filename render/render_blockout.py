"""Render the arena blockout: a labelled plan, a tactics plan, an aerial, or an eye-level shot.

    python render/render_blockout.py models/arena_blockout.glb images/arena_plan.png plan
    python render/render_blockout.py models/arena_blockout.glb images/arena_tactics.png tactics
    python render/render_blockout.py models/arena_blockout.glb images/arena_aerial.jpg aerial
    python render/render_blockout.py models/arena_blockout.glb images/arena_mid.jpg eye mid
    ... eye lane | lookout | store | b | walk | deck

The two plans are line drawings and want .png; the shaded views are photographs and are
written as JPEG when the name ends in .jpg. Cycles on GPU where there is one. The GLB is in
metres, so every number here is too. A sightline on the tactics plan is cast against the
GLB before it is drawn: one that is blocked is left out and reported.
"""
import bpy, math, sys
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

# from a pin (or a point on a floor) to what it sees: (x, y, floor)
DECK_SW, DECK_NE, WINDOW_E = (-4, -1.5, 2.5), (4, 1.5, 2.5), (-24.8, 17.6, 2)
SIGHTS = [
    ("v1", (-34.5, 9, 0.25)), ("v1", (-34.5, -3, 0)), ("v1", (-30.5, 6, 0)), ("v1", (-28, 12, 0)),
    (WINDOW_E, (-32.5, 14, 1.5)),
    ("v2", (-9.5, 7, 0)), ("v2", (9.5, -7, 0)), ("v2", (-3, 13, 0)), ("v2", (4, -13, 0)),
    ("v3", DECK_SW), ("v3", (2, -8, 0)), ("v3", (-3, -7, 1.25)),
    ("v4", DECK_NE), ("v4", (-2, 8, 0)), ("v4", (3, 7, 1.25)),
    ("v5", (22, -20, 0)), ("v5", (28, -19.5, 0)), ("v5", (32, -19, 0)), ("v5", (24, -24, 0)),
    ("v6", (22.5, -19.5, 0)), ("v6", (24, -24, 0)),
]

# each vantage: what it is called, what it sees, and what answers it
LEGEND = [
    ("LOOKOUT", "over the A ramp's east half and foot, and the lane",
     "its door faces the plateau; or vault its 1 m parapet"),
    ("DECK", "over both passage mouths and both mid courts",
     "the underpass and side strips run beneath its sight"),
    ("STORE LOFT", "over mid south and the deck",
     "one ramp up, inside the store; the deck sees its window"),
    ("WORKSHOP LOFT", "over mid north and the deck",
     "one ramp up, inside the workshop; the deck sees its window"),
    ("WALK", "over site B and B main",
     "the crate stair in B's corner jumps onto its south end"),
    ("GATEHOUSE", "a window over B main and the west of site B",
     "the plant crate hides from it; its doors face away from B"),
]

glb, out = sys.argv[1], sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else "plan"
view = sys.argv[4] if len(sys.argv) > 4 else "mid"
flat = mode in ("plan", "tactics")
blocked = []

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
scene = bpy.context.scene
meshes = {o.name: o for o in scene.objects if o.type == 'MESH'}
marks = {n: o.matrix_world.translation.copy() for n, o in meshes.items()
         if len(n) == 2 and n[0] in "vc" and n[1].isdigit()}
for n, o in meshes.items():                   # the plates are pins, drawn afresh below
    o.hide_render = n in marks or (flat and n.startswith("roof"))

FONT = None
for path in ("/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
             "C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
    try:
        FONT = bpy.data.fonts.load(path)
        break
    except Exception:
        pass


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


def text(body, x, y, z, size, mat, rot=0, left=False, back=None):
    """Type lying flat at (x, y, z). `back`, a colour, prints the same type a little fatter
    underneath, so that it reads where sightlines or shadows cross it."""
    layers = [(0, mat, 0)] if back is None else [(size * 0.07, ink(back), -0.02), (0, mat, 0)]
    for offset, paint, lift in layers:
        curve = bpy.data.curves.new(body, type='FONT')
        curve.body, curve.size, curve.font, curve.offset = body, size, FONT, offset
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


# a 2 m checker on the floor, for scale, read in world space (the GLB's nodes are scaled)
if flat:
    mat = shaded("floor_grid", (0.4, 0.4, 0.4))
    nt = mat.node_tree
    chk = nt.nodes.new('ShaderNodeTexChecker')
    chk.inputs['Color1'].default_value = (0.42, 0.42, 0.40, 1)
    chk.inputs['Color2'].default_value = (0.33, 0.33, 0.31, 1)
    chk.inputs['Scale'].default_value = 0.5
    nt.links.new(nt.nodes.new('ShaderNodeNewGeometry').outputs['Position'], chk.inputs['Vector'])
    nt.links.new(chk.outputs['Color'], next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Base Color'])
    meshes["ground"].data.materials[0] = mat

# the blockout sits on a table-top slab: a plain surface under it
bpy.ops.mesh.primitive_plane_add(size=900, location=(0, 0, -1.02))
bpy.context.object.data.materials.append(shaded("table", (0.93, 0.93, 0.91)))

cam_data = bpy.data.cameras.new("cam")
cam_data.clip_end = 2000
sun_dir = SUN
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
if mode == "plan":                         # straight down, north up: the map's own drawing
    cam_data.type, cam_data.ortho_scale = 'ORTHO', 84
    cam.location, size = (0, 0, 300), (1800, 1260)
elif mode == "tactics":                    # the same, with room under it for a legend
    cam_data.type, cam_data.ortho_scale = 'ORTHO', 82
    cam.location, size = (0, -8.1, 300), (1800, 1655)
elif mode == "aerial":                     # three-quarter, from behind the attackers
    cam_data.lens, sun_dir = 30, (-0.45, -0.5, 0.72)      # high enough to keep the spawns lit
    cam.location = mathutils.Vector((-33.3, -50.5, 80))
    cam.rotation_euler = (mathutils.Vector((-4.4, -7.2, 0)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    size = (1800, 1480)
else:                                     # eye level, as a player sees it
    standing, target, cam_data.lens, lamps, sun_dir = VIEWS[view]
    cam.location = mathutils.Vector(standing)
    cam.rotation_euler = (mathutils.Vector(target) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    size = (1800, 1012)
    dg = bpy.context.evaluated_depsgraph_get()
    if any(scene.ray_cast(dg, cam.location, mathutils.Vector(d), distance=0.2)[0]
           for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))):
        sys.exit(f"the '{view}' camera stands inside the model, at {standing}")
scene.camera = cam

if mode == "plan":
    for body, x, y, s, rot in [           # callouts lying over the plan, as a designer would pencil them
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
            ("ATTACK\nSPAWN", -31, -22.5, 2.2, 0), ("DEFEND\nSPAWN", 31, 22.5, 2.2, 0)]:
        text(body, x, y, 12, s, ink(K), rot)

if mode == "tactics":
    dg = bpy.context.evaluated_depsgraph_get()
    v_ink, c_ink = ink(V), ink(C)

    def eye(p):
        return marks[p] if p in marks else mathutils.Vector(p)

    for a, b in SIGHTS:                   # what each vantage sees, cast before it is drawn
        start, aim = eye(a), marks[b] if b in marks else mathutils.Vector(b)
        start, aim = start + mathutils.Vector((0, 0, EYE)), aim + mathutils.Vector((0, 0, EYE))
        if scene.ray_cast(dg, start, (aim - start).normalized(), distance=(aim - start).length - 0.05)[0]:
            blocked.append((a, tuple(round(v, 1) for v in aim)))
            continue
        line((start.x, start.y, 8), (aim.x, aim.y, 8), v_ink)
        tile(aim.x, aim.y, 8.02, 0.8, 0.8, v_ink)
    for i in "123456":                    # this answers that
        a, b = marks["v" + i], marks["c" + i]
        line((a.x, a.y, 8.04), (b.x, b.y, 8.04), c_ink, 0.12, (0.7, 0.5))
    for name in marks:
        p = marks[name]
        tile(p.x, p.y, 8.1, 2.1, 1.6, v_ink if name[0] == "v" else c_ink)
        text(name.upper(), p.x, p.y, 8.2, 1.15, ink((1, 1, 1)))
    for body, x, y, s, rot in [           # the names the legend uses
            ("SITE A", -35.5, 25.2, 2.0, 0), ("SITE B", 28.5, -19.6, 2.2, 0), ("A RAMP", -34.5, 12, 1.7, 0),
            ("B MAIN", 22.8, -17.4, 1.2, 0), ("UNDERPASS", 3.4, -5.2, 1.15, 0), ("STORE", -22, -12, 3.0, 0),
            ("WORKSHOP", 22, 12.5, 2.2, 0), ("MID", 0, 13, 2.6, 0), ("MID", 0, -13.5, 2.6, 0),
            ("WEST LANE", -34.5, -2, 1.7, 90), ("EAST LANE", 34.5, 2, 1.7, 90),
            ("ATTACK\nSPAWN", -31, -22.5, 2.2, 0), ("DEFEND\nSPAWN", 31, 22.5, 2.2, 0)]:
        text(body, x, y, 12, s, ink(K), rot, back=(1, 1, 1))
    text("TACTICS  -  six vantage points, each with a counter", -40, -31.8, 12, 1.7, ink(K), left=True)
    text("solid line: what it sees, ray-cast against the model   -   dashed: its counter",
         3, -31.8, 12, 1.1, ink((0.3, 0.3, 0.3)), left=True)
    for i, (title, sees, counter) in enumerate(LEGEND):
        x, y = -40 + (80 / 3) * (i % 3), -35.3 - 5.7 * (i // 3)
        text(f"V{i + 1}  {title}", x, y, 12, 1.5, v_ink, left=True)
        text(sees, x + 1.6, y - 1.7, 12, 1.12, ink((0.15, 0.15, 0.15)), left=True)
        text(f"C{i + 1}  {counter}", x, y - 3.2, 12, 1.12, c_ink, left=True)

if mode == "aerial":
    for body, x, y, z, s, rot, back in [   # names painted on the surfaces they belong to; the
            ("SITE A", -33.5, 20.2, 2.09, 2.6, 0, WHITE),      # pads' and the caption's sit where
            ("SITE B", 28.5, -17.7, 0.09, 2.4, 0, WHITE),      # no roof or wall hides them
            ("ATTACK SPAWN", -29.8, -20.6, 0.09, 1.8, 0, WHITE), ("DEFEND SPAWN", 31, 22.5, 0.09, 1.8, 0, WHITE),
            ("DECK", 0, 1, 2.54, 2.0, 0, None), ("WALK", 38.05, -21.5, 2.04, 1.0, 0, None),
            ("LOOKOUT", -26.5, 19, 5.54, 1.5, 0, None), ("STORE", -21, -7, 5.54, 3.4, 0, None),
            ("WORKSHOP", 21, 6, 5.54, 3.0, 0, None), ("BOILER", -23, 13, 5.54, 1.2, 0, None),
            ("OFFICE", -15.5, 13, 5.54, 1.9, 0, None), ("LOBBY", 15.5, -13, 5.54, 1.9, 0, None),
            ("GATEHOUSE", 23, -13, 5.54, 1.05, 0, None)]:
        text(body, x, y, z, s, ink(K), rot, back=back)

if mode == "eye":                          # a roofed room reads better with a lamp in it
    for x, y, z, width, power in lamps:
        lamp = bpy.data.lights.new("lamp", 'AREA')
        lamp.size, lamp.energy = width, power
        ob = bpy.data.objects.new("lamp", lamp)
        scene.collection.objects.link(ob)
        ob.location = (x, y, z)

# a high sun with crisp edges for the drawings, lower and softer for the 3D views
sun = bpy.data.lights.new("key", 'SUN')
sun.energy = 1.6 if flat else 2.6
sun.angle = 0.02 if flat else 0.07
key = bpy.data.objects.new("key", sun)
scene.collection.objects.link(key)
key.rotation_euler = (-mathutils.Vector((-0.2, -0.24, 1.0) if flat else sun_dir)).to_track_quat('-Z', 'Y').to_euler()

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
scene.cycles.samples = 128
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = size
scene.render.image_settings.file_format = 'JPEG' if out.lower().endswith((".jpg", ".jpeg")) else 'PNG'
scene.render.image_settings.quality = 92
scene.view_settings.view_transform = 'Standard'
scene.world = bpy.data.worlds.new("w")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes['Background']
sky = (0.80, 0.84, 0.90) if flat else (0.62, 0.72, 0.86)   # pale blue over the 3D views
bg.inputs[0].default_value = (*sky, 1.0)
bg.inputs[1].default_value = 0.65 if flat else 0.35
scene.render.filepath = out
bpy.ops.render.render(write_still=True)
print("rendered", out, "" if not blocked else f"- sightlines BLOCKED, not drawn: {blocked}")
