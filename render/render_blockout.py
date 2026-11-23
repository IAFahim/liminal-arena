"""Render the arena blockout: a labelled plan, a tactics plan, an aerial, or an eye-level shot.

    python render/render_blockout.py models/arena_blockout.glb images/arena_plan.png plan
    python render/render_blockout.py models/arena_blockout.glb images/arena_tactics.png tactics
    python render/render_blockout.py models/arena_blockout.glb images/arena_aerial.jpg aerial
    python render/render_blockout.py models/arena_blockout.glb images/arena_mid.jpg eye mid
    ... eye lane | lookout | store | b | walk | deck

The two plans are line drawings and want .png; the shaded views are photographs and are
written as JPEG when the name ends in .jpg. Cycles on GPU where there is one. The cameras and
the callouts are `views`', so every picture pairs with the dressed one `render_liminal.py`
makes of the same view. A sightline on the tactics plan is cast against the GLB before it is
drawn: one that is blocked is left out and reported.
"""
import math
import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parent))
import views  # noqa: E402

SUN, B_SUN, EYE, K, V, C, WHITE = views.SUN, views.B_SUN, views.EYE, views.K, views.V, views.C, views.WHITE
text, tile, line, ink, shaded = views.text, views.tile, views.line, views.ink, views.shaded

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


def tactics(scene, marks):
    """The sightlines, the pins and the legend. A line is drawn only if the GLB leaves it open."""
    dg = bpy.context.evaluated_depsgraph_get()
    v_ink, c_ink = ink(V), ink(C)
    blocked = []

    def eye(p):
        return marks[p] if p in marks else mathutils.Vector(p)

    for a, b in SIGHTS:                       # what each vantage sees, cast before it is drawn
        start, aim = eye(a), marks[b] if b in marks else mathutils.Vector(b)
        start, aim = start + mathutils.Vector((0, 0, EYE)), aim + mathutils.Vector((0, 0, EYE))
        if scene.ray_cast(dg, start, (aim - start).normalized(), distance=(aim - start).length - 0.05)[0]:
            blocked.append((a, tuple(round(v, 1) for v in aim)))
            continue
        line((start.x, start.y, 8), (aim.x, aim.y, 8), v_ink)
        tile(aim.x, aim.y, 8.02, 0.8, 0.8, v_ink)
    for i in "123456":                        # this answers that
        a, b = marks["v" + i], marks["c" + i]
        line((a.x, a.y, 8.04), (b.x, b.y, 8.04), c_ink, 0.12, (0.7, 0.5))
    for name in marks:
        p = marks[name]
        tile(p.x, p.y, 8.1, 2.1, 1.6, v_ink if name[0] == "v" else c_ink)
        text(name.upper(), p.x, p.y, 8.2, 1.15, ink((1, 1, 1)))
    for body, x, y, s, rot in [               # the names the legend uses
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
    return blocked


def main():
    glb, out = sys.argv[1], sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "plan"
    view = sys.argv[4] if len(sys.argv) > 4 else "mid"
    flat = mode in ("plan", "tactics")
    blocked = []

    scene, meshes, marks = views.load(glb)
    views.hide(meshes, marks, *(("roof",) if flat else ()))   # the plates are pins, drawn afresh below

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

    size, sun_dir, lamps = views.camera(mode, view)
    if mode == "plan":
        views.plan_names()
    if mode == "tactics":
        blocked = tactics(scene, marks)
    if mode == "aerial":
        views.aerial_names()
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

    views.cycles(scene, 128)
    scene.view_settings.view_transform = 'Standard'
    scene.world = bpy.data.worlds.new("w")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes['Background']
    sky = (0.80, 0.84, 0.90) if flat else (0.62, 0.72, 0.86)   # pale blue over the 3D views
    bg.inputs[0].default_value = (*sky, 1.0)
    bg.inputs[1].default_value = 0.65 if flat else 0.35
    views.still(scene, out, size)
    print("rendered", out, "" if not blocked else f"- sightlines BLOCKED, not drawn: {blocked}")


if __name__ == "__main__":
    main()
