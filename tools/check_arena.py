"""Check an arena GLB: what can be walked to and how long it takes, how each high place is
reached, and who sees whom.

    python check_arena.py models/arena_blockout.glb
    python check_arena.py models/liminal/arena_liminal.glb
    python check_arena.py models/liminal/arena_liminal_packed.glb --marks models/liminal/arena_liminal.glb

Standable floors come from ray casts on a 0.5 m grid; a player steps up 0.4 m, jumps
1.05 m, drops any height, and runs at 5.5 m/s. Eyes are 1.6 m up.

A packed file ships no design plates and its static parts are one mesh. `--marks` names the file
it was packed from, for where the plates stand; the ways up are then not asked, as no part is
left to hide: they are asked of the source, and the packed file of it by same_surfaces.py.
"""
import sys
from collections import deque

import bpy
from mathutils import Vector

STEP, JUMP, EYE, RUN = 0.4, 1.05, 1.6, 5.5
args = sys.argv[1:]
glb = args[args.index("--") + 1] if "--" in args else args[0]
marked = args[args.index("--marks") + 1] if "--marks" in args else None      # the file a packed one came from
PLATES = ("v1", "v2", "v3", "v4", "v5", "v6", "c1", "c2", "c3", "c4", "c5", "c6")

bpy.ops.wm.read_factory_settings(use_empty=True)
meshes, marks = {}, {}
if marked:
    bpy.ops.import_scene.gltf(filepath=marked)
    marks = {ob.name: ob.matrix_world.translation.copy() for ob in bpy.context.scene.objects
             if ob.type == 'MESH' and ob.name in PLATES}
    if len(marks) != len(PLATES):
        raise SystemExit(f"check_arena: {marked} holds {len(marks)} of the {len(PLATES)} design plates")
    bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
scene = bpy.context.scene
for ob in list(scene.objects):
    if ob.type != 'MESH':
        continue
    if ob.name in PLATES:
        marks[ob.name] = ob.matrix_world.translation.copy()
        ob.hide_viewport = True             # the plates are labels, not floor
    else:
        meshes[ob.name] = ob
CELL = 0.5
door = None
# No probe runs at exactly 45 degrees: samples sit at .25 and .75 m, corners on whole metres, so a
# true diagonal lands on corners -- where it touches two faces at once, and which one a cast
# reports is a tie that a rebuilt mesh can break either way
PROBES = [Vector(d).normalized() for d in ((0, 0, 1), (1, .13, .07), (-1, .11, -.05), (.17, 1, .04), (.19, -1, .09),
                                           (1, .93, .03), (-1, .96, -.02), (1, -1.07, .06), (-1, -.91, .05))]


def survey(hidden=(), shut=None):
    """Standable floors with the named parts hidden and, if `shut` is a pair of corners,
    that box filled in (a door shut): (i, j) -> floor heights."""
    global door
    for name, ob in meshes.items():
        ob.hide_viewport = any(name.startswith(h) for h in hidden)
    if door is not None:
        meshes.pop("door", None)
        bpy.data.objects.remove(door, do_unlink=True)
    door = None
    if shut:
        (x0, y0, z0), (x1, y1, z1) = shut
        bpy.ops.mesh.primitive_cube_add(size=1, location=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
        door = bpy.context.object
        door.scale = (x1 - x0, y1 - y0, z1 - z0)
        meshes["door"] = door
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()

    def free(p):
        """No solid holds `p`: along every probe the first face met faces back toward it.
        (A wall stands on the ground box, and on their shared face the cast can land on
        the ground's top -- which would read as a floor inside the wall.)"""
        for d in PROBES:
            hit, _, normal, *_ = scene.ray_cast(dg, p, d, distance=40)
            if hit and normal.dot(d) > 0.1:
                return False
        return True

    cells, down, up = {}, Vector((0, 0, -1)), Vector((0, 0, 1))
    for i in range(-79, 80):
        for j in range(-55, 56):
            x, y = (i + .5) * CELL, (j + .5) * CELL
            z, floors, first = 12.0, [], True
            while z > -0.5:
                hit, at, normal, *_ = scene.ray_cast(dg, Vector((x, y, z)), down, distance=z + 1.0)
                if not hit:
                    break
                z = at.z
                above = Vector((x, y, z + 0.02))
                if normal.z > 0.5 and not scene.ray_cast(dg, above, up, distance=1.9)[0] \
                        and (first or free(above)):
                    floors.append(round(z, 3))
                first = False
                z -= 0.02
            if floors:
                cells[(i, j)] = floors
    return cells


def flood(cells, start, jumps=True, drops=True):
    """Seconds to every node (i, j, floor) from (x, y, floor)."""
    i, j = (int(start[0] // CELL), int(start[1] // CELL))
    nodes = {(a, b, z) for (a, b), floors in cells.items() for z in floors}
    here = min(nodes, key=lambda n: (abs(n[2] - start[2]), abs(n[0] - i) + abs(n[1] - j)))
    best, queue = {here: 0.0}, deque([here])

    def step(node):
        a, b, z = node
        for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for z2 in cells.get((a + da, b + db), ()):
                rise = z2 - z
                if rise <= STEP or (jumps and rise <= JUMP):
                    yield (a + da, b + db, z2), rise

    while queue:
        node = queue.popleft()
        for other, rise in step(node):
            if rise < -STEP and not drops:
                continue
            cost = best[node] + CELL / RUN + (0.8 if rise > STEP else 0.0) + (0.3 if rise < -STEP else 0.0)
            if cost < best.get(other, 1e9):
                best[other] = cost
                queue.append(other)
    return best


def at(cells, name, floor):
    """The node on `floor` nearest the plate `name` (a marker, or (x, y))."""
    x, y = (marks[name].x, marks[name].y) if isinstance(name, str) else name
    return min(((i, j, z) for (i, j), fs in cells.items() for z in fs if abs(z - floor) < 0.12),
               key=lambda n: abs((n[0] + .5) * CELL - x) + abs((n[1] + .5) * CELL - y))


ATTACK, DEFEND = (-31, -22.5, 0), (31, 22.75, 0)
cells = survey()
att, de = flood(cells, ATTACK), flood(cells, DEFEND)
att_walk, de_walk = flood(cells, ATTACK, jumps=False), flood(cells, DEFEND, jumps=False)
SPOT = {"v1 lookout": ("v1", 2), "v2 deck": ("v2", 2.5), "v3 store loft": ("v3", 2), "v4 workshop loft": ("v4", 2),
        "v5 walk": ("v5", 2), "v6 gatehouse": ("v6", 0), "c1 plateau": ("c1", 2), "c2 underpass": ("c2", 0),
        "c3 store ramp foot": ("c3", 0), "c4 workshop ramp": ("c4", 0), "c5 B crate stair": ("c5", 1),
        "c6 behind B crate": ("c6", 0), "site A pad": ((-33.5, 22), 2), "site B pad": ((28, -22), 0),
        "boiler": ((-23, 13), 0), "office": ((-15.5, 13), 0), "store": ((-24, -12), 0), "pump room": ((-23, 1.5), 0),
        "workshop": ((24, 12), 0), "plant room": ((23, -1.5), 0), "lobby": ((15.5, -13), 0), "mid north": ((0, 13), 0),
        "mid south": ((0, -13), 0), "B main": ((23, -20), 0), "A yard": ((-28, 12), 0)}

print("\nWALK  (seconds from each spawn); * = a jump or a drop is needed to get there")
print(f"{'':20} {'attack':>9} {'defend':>9}")
for label, (name, floor) in SPOT.items():
    n = at(cells, name, floor)

    def fmt(full, walk, n=n):
        return "    none" if n not in full else f"{walk.get(n, full[n]):8.1f}{'' if n in walk else '*'}"
    print(f"{label:20} {fmt(att, att_walk):>9} {fmt(de, de_walk):>9}")

if marked:
    print("\nWAYS UP  not asked of a packed file: its parts are one mesh and none is left to hide. Ask its source.")
else:
    print("\nWAYS UP  (the route named is hidden; what is left)")
    LOOKOUT_DOOR = ((-26, 21, 2), (-24, 22, 4.2))
    for label, name, floor, gone, shut, expect in [
            ("store loft", "v3", 2, ("store_loft_ramp",), None, "unreachable"),
            ("workshop loft", "v4", 2, ("workshop_loft_ramp",), None, "unreachable"),
            ("walk", "v5", 2, ("walk_stairs",), None, "jump only"),
            ("walk", "v5", 2, ("walk_stairs", "crate_b1", "crate_b2"), None, "unreachable"),
            ("deck", "v2", 2.5, ("deck_ramp",), None, "walk"),
            ("deck", "v2", 2.5, ("deck_stairs",), None, "walk"),
            ("deck", "v2", 2.5, ("deck_ramp", "deck_stairs"), None, "unreachable"),
            ("site A plateau", "c1", 2, ("a_ramp",), None, "walk"),
            ("site A plateau", "c1", 2, ("a_ramp", "a_stairs"), None, "unreachable"),
            ("lookout, its door shut", "v1", 2, (), LOOKOUT_DOOR, "jump only"),
            ("store floor, off the loft, its ramp gone", (-24, -6), 0, ("store_loft_ramp",), None, "drop")]:
        c = survey(gone, shut)
        n = at(c, name, floor)
        if expect == "drop":                 # off the loft on foot, but not back up onto it
            up_loft = flood(c, (-24, -6, 0), jumps=False, drops=False)
            got = "drop" if n in flood(c, (-13.5, -5, 2), jumps=False) and at(c, "v3", 2) not in up_loft else "no"
        else:
            got = ("walk" if n in flood(c, ATTACK, jumps=False) else
                   "jump only" if n in flood(c, ATTACK) else "unreachable")
        print(f"  {'ok  ' if got == expect else 'FAIL'} without {' + '.join(gone) or '(door shut)':34} {label:40} {got}")
    survey()


def eye(p):
    return p + Vector((0, 0, EYE))


def point(p):
    return eye(marks[p]) if isinstance(p, str) else Vector(p)


def blockage(a, b):
    origin, aim = point(a), point(b)
    dg = bpy.context.evaluated_depsgraph_get()
    gap = (aim - origin).length
    hit, at, normal, idx, ob, mw = scene.ray_cast(dg, origin, (aim - origin).normalized(), distance=max(gap - 0.05, 0))
    return ob.name if hit else None


DECK_SW, DECK_NE, WINDOW_E = (-4, -1.5, 2.5 + EYE), (4, 1.5, 2.5 + EYE), (-24.8, 17.6, 2 + EYE)
TESTS = [
    ("v1", "sees", "ramp foot", (-34.5, 9, 0.25 + EYE)), ("v1", "sees", "lane north", (-34.5, -3, EYE)),
    ("v1", "sees", "passage mouth", (-30.5, 6, EYE)), ("v1", "sees", "A yard", (-28, 12, EYE)),
    (WINDOW_E, "sees", "ramp top, east half", (-32.5, 14, 1.5 + EYE)),
    ("v1", "blind", "ramp top, west half", (-37, 15, 1.75 + EYE)),
    ("v1", "blind", "lane behind the gate wall", (-36.5, -12, EYE)), ("v1", "sees", "c1 at its door", "c1"),
    ("v1", "blind", "plateau centre", (-34, 24, 2 + EYE)), ("c1", "sees", "lookout door", (-25, 21.5, 2 + EYE)),
    ("v2", "sees", "west passage mouth", (-9.5, 7, EYE)), ("v2", "sees", "east passage mouth", (9.5, -7, EYE)),
    ("v2", "sees", "mid north", (-3, 13, EYE)), ("v2", "sees", "mid south", (4, -13, EYE)),
    ("v2", "sees", "workshop loft", "v4"), (DECK_SW, "sees", "store loft", "v3"),
    ("v2", "blind", "underpass", (0, 1, EYE)), ("v2", "blind", "west strip", (-7.5, 1, EYE)),
    ("v2", "blind", "east strip", (7.5, -1, EYE)),
    ("v3", "sees", "deck", DECK_SW), ("v3", "sees", "mid south floor", (2, -8, EYE)), ("v3", "sees", "deck ramp", (-3, -7, 1.25 + EYE)),
    ("v3", "blind", "mid north", (0, 13, EYE)), ("v3", "sees", "workshop loft, across mid", "v4"),
    ("v4", "sees", "deck", DECK_NE), ("v4", "sees", "mid north floor", (-2, 8, EYE)), ("v4", "sees", "deck stairs", (3, 7, 1.25 + EYE)),
    ("v4", "blind", "mid south", (0, -13, EYE)), ("v4", "sees", "store loft, across mid", "v3"),
    ("v5", "sees", "B main", (22, -20, EYE)), ("v5", "sees", "B centre", (28, -19.5, EYE)),
    ("v5", "sees", "B north-east", (32, -19, EYE)), ("v5", "sees", "B south-west", (24, -24, EYE)),
    ("v5", "sees", "B crate stair", "c5"), ("v5", "blind", "B north-east at its foot", (33, -17, EYE)),
    ("v5", "blind", "crouched behind B's plant crate", (25, -22.5, 1.0)),
    ("v6", "sees", "B main", (22.5, -19.5, EYE)), ("v6", "sees", "B south-west", (24, -24, EYE)), ("v6", "blind", "c6", "c6"),
]
survey()                                  # everything visible again
print("\nSIGHT  (eyes 1.6 m up; what blocks a blind line in brackets)")
bad = 0
for a, want, label, b in TESTS:
    hit = blockage(a, b)
    ok = (hit is None) == (want == "sees")
    bad += not ok
    who = a if isinstance(a, str) else "window east" if a == WINDOW_E else "deck corner"
    print(f"  {'ok  ' if ok else 'FAIL'} {who:11} {want:5} {label}" + (f"   [{hit}]" if hit else ""))
print(f"\n{len(TESTS) - bad}/{len(TESTS)} sightlines as intended")
