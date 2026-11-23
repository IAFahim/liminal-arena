"""The blockout dressed in Kenney's Retro Textures (Liminal), and written out for engines.

`arena_blockout` builds the grey boxes; this script lays pixel textures over them -- 64
pixels to the metre, so a wallpaper tile is a metre wide and two tall, its wainscot on the
floor -- hangs a door open in every doorway, sets a lit ceiling tile every four metres
under every roof, and writes the result out four ways: a GLB with its textures inside it,
an FBX the same, an OBJ with its textures beside it, and a .blend.

    python examples/arena_liminal.py

Not one floor moves: the layout is the blockout's, and tools/check_arena.py runs on the
GLB this writes as it does on the grey one. The yard is the tiled floor of a drained pool,
a lane line down each lane, and site B's courtyard the one corner still under water. The
attackers' half is yellow wallpaper over red carpet, the defenders' blue over green, and
each pad is outlined in light.
"""
import json
import math
import shutil
import struct
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena_blockout as blockout  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KENNEY = ROOT / "textures" / "kenney_retro-textures-liminal" / "PNG"
OUT = ROOT / "models" / "liminal"
CREDIT = "Arena modelled with sand3d. Textures: Retro Textures Liminal by Kenney (www.kenney.nl), CC0"
BAND = 2                        # a wallpaper panel, and a course of concrete, is this tall
LEAF, THICK = 1.1, 0.06         # a door leaf: the door textures' 64 x 128 in metres, and its thickness
LIT_EVERY, LAMP_EVERY = 4, 8    # ceiling: a lit tile every 4 m, a lamp under every second of them
LAMP_W, SHINE = 5.0, 3.0        # a lamp's power; how many times its own colour a lit tile shines
UP = Vector((0, 0, 1))

Y_PANEL, Y_PLAIN = "wall_yellow_pattern_panel", "wall_yellow_small"       # the attackers' walls
B_PANEL, B_PLAIN = "wall_blue_stripes_panel", "wall_blue_stripes_small"   # the defenders'
Y_FLAT = "wall_yellow_panel"                 # the attackers' wallpaper, unpatterned: site A's terrace
CONCRETE, PAVING, ROOFING = "wall_concrete", "pool_tiles", "wall_concrete_dirt"   # the yard is pool deck
EDGE = "#3b2a1d"                             # a door leaf's edges: its own wood, darkened

# (x0, y0, x1, y1, texture, tile width, the axis its width runs along) -- the ground's floor;
# the first zone to hold a point wins
ZONES = [
    (-29, -15, -11, 3, "carpet_red", 2, "x"),            # the store and its pump room
    (-25, 11, -11, 15, "carpet_red", 2, "x"),            # the boiler and the office
    (11, -3, 29, 15, "carpet_green", 2, "x"),            # the workshop and its plant room
    (11, -15, 25, -11, "carpet_green", 2, "x"),          # the lobby and the gatehouse
    (21, -27, 39, -16, "pool_water_tiles", 1, "x"),      # site B's courtyard: the corner still flooded
    (-35, -27, -34, 8, "pool_tiles_line", 1, "y"),       # a lane line down the west lane, and up the east
    (34, -16, 35, 27, "pool_tiles_line", 1, "y"),
]
# pad -> (its floor, the tile's width, the colour its outline shines)
PADS = {"site_a": ("carpet_red", 2, "#ffb547"), "site_b": ("pool_tiles", 1, "#ffb547"),
        "spawn_attack": ("carpet_red_detail", 2, "#ff4a3d"), "spawn_defend": ("wall_blue_small", 2, "#4a8cff")}
# doorway -> the side its door opens to: every one swings into a room, never across a path
SWING = {"nw_passage": "+y", "nw_far": "-y", "nw_mid_end": "-x", "nw_between": "-x",
         "store_spawn_side": "+y", "store_passage": "-y", "store_lane_end": "+x", "store_mid_end": "-x",
         "store_gallery": "-y", "lookout_n": "-y",
         "se_passage": "-y", "se_far": "+y", "se_mid_end": "+x", "se_between": "+x",
         "workshop_spawn_side": "-y", "workshop_passage": "+y", "workshop_lane_end": "-x",
         "workshop_mid_end": "+x", "workshop_gallery": "+y"}
HINGE = {"lookout_n": "b"}     # on the jamb that hides nothing: from the lookout's floor, the plateau stays in view
LEAF_OF = {"store_spawn_side": "door_emergency_exit", "workshop_spawn_side": "door_emergency_exit",
           "lookout_n": "door_wood_boarded"}

MATS, RATIO = {}, {}           # the materials made so far; a texture's height over its width


def mat(key, glow=0.0):
    """The material of one Kenney texture, made once; `glow` is how many times over its own
    colour it shines. A '#rrggbb' in place of a name is a flat colour."""
    if (key, glow) in MATS:
        return MATS[(key, glow)]
    m = bpy.data.materials.new(f"kenney_{key}" if not key.startswith("#") else f"flat_{key[1:]}_{glow:g}")
    if m.node_tree is None:                       # Blender 4; from 5 on a material is always nodes
        m.use_nodes = True
    nodes = m.node_tree.nodes
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.85
    if key.startswith("#"):
        srgb = [int(key[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        bsdf.inputs["Base Color"].default_value = (*(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                                                     for c in srgb), 1.0)
        bsdf.inputs["Emission Color"].default_value = bsdf.inputs["Base Color"].default_value
    else:
        image = bpy.data.images.load(str(KENNEY / f"{key}.png"), check_existing=True)
        RATIO[key] = image.size[1] / image.size[0]
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = 'Closest'             # pixels stay pixels, in Blender and in the GLB
        m.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if glow:
            m.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = glow
    MATS[(key, glow)] = m
    return m


def role(name):
    """What a part is, from its name: (kind, side), where a side matters -- +1 the attackers'
    half, -1 the defenders'. Anything unnamed is a mistake, not a default."""
    if "_stairs" in name:
        return "stairs"
    if name == "deck_ramp":
        return "ramp"
    if name.endswith("_loft_ramp"):
        return "loft_ramp", 1 if name.startswith("store") else -1
    if "parapet" in name:
        return "parapet"
    if name == "ground":
        return "ground"
    if name in ("wall_n", "wall_s", "wall_w", "wall_e"):
        return "shell"
    if name.startswith("roof_"):
        return "roof"
    if name.startswith("crate_"):
        return "crate"
    if name[0] in "vc" and name[1:].isdigit():
        return "mark", name[0]
    if name in PADS:
        return "pad", name
    if name in ("plateau", "deck", "walk"):
        return {"plateau": "terrace"}.get(name, name)
    if name == "a_ramp":
        return "terrace_ramp"
    if name.startswith("pillar_"):
        return "pillar"
    if name.endswith("_loft"):
        return "loft", 1 if name.startswith("store") else -1
    if name.startswith(("gate_", "b_main")):
        return "gate"
    if name.startswith(("lookout_", "nw_", "store_")):
        return "wall", 1
    if name.startswith(("se_", "workshop_")):
        return "wall", -1
    raise KeyError(f"arena_liminal: no part is named {name!r}")


def finish(r, n, low, base):
    """What a face is painted as -- (texture, the metres a tile is wide, its shine) -- from its
    part `r`, its normal `n`, the height `low` its course starts at, and `base`, its part's floor."""
    kind, side = r if isinstance(r, tuple) else (r, None)
    up = n.z > 0.5
    if kind in ("wall", "loft"):
        if kind == "loft" and up:
            return ("carpet_red" if side > 0 else "carpet_green"), 2, 0
        if abs(n.z) > 0.5 or low != base:          # a sill, a top, a course above the first
            return (Y_PLAIN if side > 0 else B_PLAIN), 1, 0
        return (Y_PANEL if side > 0 else B_PANEL), 1, 0
    if kind == "loft_ramp":
        if up:
            return ("carpet_red" if side > 0 else "carpet_green"), 2, 0
        return (Y_PLAIN if side > 0 else B_PLAIN), 1, 0
    if kind in ("terrace", "terrace_ramp"):
        return ("carpet_red_detail", 2, 0) if up else (Y_FLAT, 1, 0)
    if kind in ("deck", "walk", "parapet", "ramp"):
        return (PAVING, 2, 0) if up else (CONCRETE, 1, 0)
    if kind == "roof":
        return (ROOFING, 2, 0) if up else (CONCRETE, 1, 0)
    if kind == "pillar":
        return "metal_structure", 1, 0
    if kind == "stairs":
        return PAVING, 1, 0
    if kind in ("shell", "gate", "ground"):
        return (PAVING, 2, 0) if up else (CONCRETE, 1, 0)
    if kind == "crate":
        return "metal_structure_bolts", 1, 0
    if kind == "mark":
        return ("#8e44ad" if side == "v" else "#1eaa6b"), 1, SHINE / 2
    if kind == "pad":
        return PADS[side][2], 1, SHINE
    raise KeyError(kind)


def place(p, n, width, course, key):
    """A point's place on its texture: an upright face unrolls along its run, left to right as
    its front is seen, and rises from its course's floor; a flat one lies on the plan."""
    height = width * RATIO.get(key, 1.0)
    if abs(n.z) < 0.7:
        return (p.x * -n.y + p.y * n.x) / width, (p.z - course * BAND) / height
    return p.x / width, p.y / height


def world_faces(ob):
    """`ob`'s faces where it stands, in metres: each a ring of points and its normal."""
    m, me = ob.matrix_world, ob.data
    return [([m @ me.vertices[i].co / 1000 for i in p.vertices], (m.to_3x3() @ p.normal).normalized())
            for p in me.polygons]


def cut(ring, z, below):
    """`ring` sliced at height `z`, the part below (or above) kept; a convex ring stays one."""
    out = []
    for p, q in zip(ring, ring[1:] + ring[:1]):
        inp, inq = (p.z <= z, q.z <= z) if below else (p.z >= z, q.z >= z)
        if inp:
            out.append(p)
        if inp != inq:
            out.append(p.lerp(q, (z - p.z) / (q.z - p.z)))
    return out


def area(ring):
    total = Vector((0, 0, 0))
    for p, q in zip(ring, ring[1:] + ring[:1]):
        total += p.cross(q)
    return total.length / 2


def courses(ring):
    """`ring` cut into `BAND`-tall courses, as (ring, the height it starts at)."""
    low, high = min(p.z for p in ring), max(p.z for p in ring)
    out = []
    for k in range(math.floor(low / BAND), math.floor(high / BAND) + 1):
        piece = cut(cut(ring, k * BAND, False), (k + 1) * BAND, True)
        if len(piece) >= 3 and area(piece) > 1e-7:
            out.append((piece, k * BAND))
    return out


def quad(a, b, z):
    """A flat cell from corner `a` to corner `b` at height `z`, wound to face up."""
    return [Vector((a[0], a[1], z)), Vector((b[0], a[1], z)), Vector((b[0], b[1], z)), Vector((a[0], b[1], z))]


def floor(ring):
    """The ground's top: cut along every zone's edge, each patch carpeted or tiled as the room
    it lies in, the rest pool deck."""
    z = ring[0].z
    xs = sorted({p.x for p in ring} | {v for zone in ZONES for v in (zone[0], zone[2])})
    ys = sorted({p.y for p in ring} | {v for zone in ZONES for v in (zone[1], zone[3])})
    faces = []
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            x, y = (x0 + x1) / 2, (y0 + y1) / 2
            key, width, along = next(((s[4], s[5], s[6]) for s in ZONES if s[0] < x < s[2] and s[1] < y < s[3]),
                                     (PAVING, 2, "x"))
            cell = quad((x0, y0), (x1, y1), z)
            uv = [place(p, UP, width, 0, key) for p in cell]
            if along == "y":                  # a lane line runs north: the texture's width lies that way
                uv = [u[::-1] for u in uv]
            faces.append((mat(key), cell, uv))
    return faces


def outline(ring, pad):
    """A pad's top: its floor inside, a band round the edge that shines -- a site seen from
    across the arena at dusk."""
    key, width, colour = PADS[pad]
    z, wide = ring[0].z, 0.15
    x0, x1 = min(p.x for p in ring), max(p.x for p in ring)
    y0, y1 = min(p.y for p in ring), max(p.y for p in ring)
    i = wide
    glow = mat(colour, SHINE)
    bands = [quad((x0, y0), (x1, y0 + i), z), quad((x0, y1 - i), (x1, y1), z),
             quad((x0, y0 + i), (x0 + i, y1 - i), z), quad((x1 - i, y0 + i), (x1, y1 - i), z)]
    inside = quad((x0 + i, y0 + i), (x1 - i, y1 - i), z)
    return ([(glow, band, [(0, 0)] * 4) for band in bands]
            + [(mat(key), inside, [place(p, UP, width, 0, key) for p in inside])])


def ceiling(ring, lamps):
    """The underside of a roof or a deck as a suspended ceiling: a tile to the metre, one in
    `LIT_EVERY` lit -- and, half as often, a lamp hung under it -- a vent here and there."""
    z = ring[0].z
    x0, x1 = min(p.x for p in ring), max(p.x for p in ring)
    y0, y1 = min(p.y for p in ring), max(p.y for p in ring)
    faces = []
    for i in range(round(x1 - x0)):
        for j in range(round(y1 - y0)):
            lit = i % LIT_EVERY == LIT_EVERY // 2 and j % LIT_EVERY == LIT_EVERY // 2
            vent = i % 7 == 5 and j % 6 == 4 and not lit
            key = "ceiling_medium_light_on" if lit else "ceiling_medium_vent" if vent else "ceiling_medium"
            cell = [Vector((x0 + i, y0 + j, z)), Vector((x0 + i, y0 + j + 1, z)),      # wound to face down
                    Vector((x0 + i + 1, y0 + j + 1, z)), Vector((x0 + i + 1, y0 + j, z))]
            faces.append((mat(key, SHINE if lit else 0), cell, [(0, 0), (0, 1), (1, 1), (1, 0)]))
            if lit and i % LAMP_EVERY == LIT_EVERY // 2 and j % LAMP_EVERY == LIT_EVERY // 2:
                lamps.append((x0 + i + 0.5, y0 + j + 0.5, z))
    return faces


def dress(ob, r, lamps):
    """`ob`'s new faces: (material, ring, each point's place on its texture), where it stands."""
    faces, seen = [], world_faces(ob)
    base = min(p.z for ring, _ in seen for p in ring)
    for ring, n in seen:
        if r == "ground" and n.z > 0.5:
            faces += floor(ring)
        elif r[0] == "pad" and n.z > 0.5:
            faces += outline(ring, r[1])
        elif r in ("roof", "deck") and n.z < -0.5:
            faces += ceiling(ring, lamps)
        else:
            for piece, course in [(ring, 0)] if r[0] == "mark" else courses(ring):
                key, width, glow = finish(r, n, course, base)
                faces.append((mat(key, glow), piece, [place(p, n, width, course, key) for p in piece]))
    return faces


def mesh(ob, faces, frame):
    """`ob`'s mesh from `faces` -- (material, ring, uvs) in metres, where they were measured --
    written in `frame`, the placement the part keeps: its own pivot, as the blockout's, so
    that in an editor a crate turns about itself and a door about its hinge."""
    me, verts, slots, polys, uvs = bpy.data.meshes.new(ob.name), [], [], [], []
    back = frame.inverted()
    for material, ring, uv in faces:
        if material not in slots:
            slots.append(material)
        polys.append(tuple(range(len(verts), len(verts) + len(ring))))
        verts += [back @ p for p in ring]
        uvs.append(uv)
    me.from_pydata(verts, [], polys)
    for material in slots:
        me.materials.append(material)
    layer = me.uv_layers.new(name="UVMap")
    for p, uv, (material, _, _) in zip(me.polygons, uvs, faces):
        p.material_index = slots.index(material)
        for li, co in zip(p.loop_indices, uv):
            layer.data[li].uv = co
    ob.data, ob.matrix_basis = me, frame


def box(x0, y0, x1, y1, z0, z1):
    """The six faces of a box as (ring, normal), each wound to face out."""
    v = lambda x, y, z: Vector((x, y, z))
    return [((v(x0, y0, z1), v(x1, y0, z1), v(x1, y1, z1), v(x0, y1, z1)), UP),
            ((v(x0, y0, z0), v(x0, y1, z0), v(x1, y1, z0), v(x1, y0, z0)), Vector((0, 0, -1))),
            ((v(x0, y0, z0), v(x1, y0, z0), v(x1, y0, z1), v(x0, y0, z1)), Vector((0, -1, 0))),
            ((v(x0, y1, z0), v(x0, y1, z1), v(x1, y1, z1), v(x1, y1, z0)), Vector((0, 1, 0))),
            ((v(x0, y0, z0), v(x0, y0, z1), v(x0, y1, z1), v(x0, y1, z0)), Vector((-1, 0, 0))),
            ((v(x1, y0, z0), v(x1, y1, z0), v(x1, y1, z1), v(x1, y0, z1)), Vector((1, 0, 0)))]


def leaf(d):
    """A door standing open at a right angle in doorway `d`, hung on one jamb: its two faces
    carry the door upright from hinge to handle -- one of them the mirror of the other, as a
    real door's are. Also returns its hinge line, to be its pivot."""
    sign = 1 if SWING[d["name"]][0] == "+" else -1
    jamb = d["b"] - THICK if HINGE.get(d["name"]) == "b" else d["a"]
    z0, z1 = d["floor"], d["floor"] + 2 * LEAF
    if d["along_x"]:                      # the wall runs on x: the leaf stands out along y
        face = d["y1"] if sign > 0 else d["y0"]
        (x0, x1), (y0, y1) = (jamb, jamb + THICK), sorted((face, face + sign * LEAF))
        along, root = Vector((0, sign, 0)), Vector((jamb, face, z0))
    else:
        face = d["x1"] if sign > 0 else d["x0"]
        (x0, x1), (y0, y1) = sorted((face, face + sign * LEAF)), (jamb, jamb + THICK)
        along, root = Vector((sign, 0, 0)), Vector((face, jamb, z0))
    default = "door_wood_light" if d["name"].startswith(("se_", "workshop_")) else "door_wood"
    texture, edge = mat(LEAF_OF.get(d["name"], default)), mat(EDGE)
    faces = []
    for ring, n in box(x0, y0, x1, y1, z0, z1):
        if abs(n.z) < 0.5 and abs(n.dot(along)) < 0.5:      # the door itself, front or back
            faces.append((texture, ring, [((p - root).dot(along) / LEAF, (p.z - z0) / (2 * LEAF)) for p in ring]))
        else:
            faces.append((edge, ring, [(0, 0)] * len(ring)))
    return faces, root


def empty(name, parent=None):
    ob = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(ob)
    ob.parent = parent
    return ob


def polish(glb):
    """Two touches on the GLB the exporter wrote, read as the bytes it is. Its samplers: pixels
    crisp close up (the nearest `magFilter` the Closest interpolation wrote), smooth far away
    (a trilinear `minFilter`) -- how pixel-art scenes are played, so that distance does not
    shimmer. And its lamps, which the exporter declares the file cannot do without: a loader
    that has no KHR_lights_punctual should still show the arena, lampless."""
    data = glb.read_bytes()                        # 12-byte header, then each chunk's length and type
    magic, version, _, length, kind = struct.unpack_from("<4sIII4s", data, 0)
    doc = json.loads(data[20:20 + length])
    for sampler in doc.get("samplers", []):
        sampler["minFilter"] = 9729                # LINEAR_MIPMAP_LINEAR
    doc["extensionsRequired"] = [e for e in doc.get("extensionsRequired", []) if e != "KHR_lights_punctual"]
    body = json.dumps(doc, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)
    rest = data[20 + length:]
    glb.write_bytes(struct.pack("<4sIII4s", magic, version, 20 + len(body) + len(rest), len(body), kind)
                    + body + rest)


def main():
    parts = blockout.build()
    bpy.context.view_layer.update()
    root = empty("Arena")
    groups = {name: empty(name, root) for name in ("Structure", "Cover", "Doors", "Sites", "Markers", "Lights")}
    lamps = []
    for ob in parts:
        r = role(ob.name)
        frame = ob.matrix_world.copy()             # the scene is in millimetres, the files in metres
        frame.translation /= 1000
        if max(abs(s - 1) for s in frame.to_scale()) > 1e-6:
            raise ValueError(f"arena_liminal: {ob.name} is scaled, and this script assumes none is")
        mesh(ob, dress(ob, r, lamps), frame)
        ob.parent = groups["Markers" if r[0] == "mark" else "Cover" if r == "crate"
                            else "Sites" if r[0] == "pad" else "Structure"]
        for prop in list(ob.keys()):                # sand3d's own bookkeeping stays out of the files
            if prop.startswith("s3d"):
                del ob[prop]
        if r[0] == "mark":
            ob["kind"] = "vantage" if r[1] == "v" else "counter"
    for v, c in zip(blockout.VANTAGES, blockout.COUNTERS):
        bpy.data.objects[v[0]]["title"], bpy.data.objects[v[0]]["answered by"] = v[4], c[0]
        bpy.data.objects[c[0]]["title"], bpy.data.objects[c[0]]["answers"] = c[4], v[0]
    for d in blockout.DOORWAYS:
        name = f"door_{d['name']}"
        ob = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        bpy.context.scene.collection.objects.link(ob)
        ob.parent = groups["Doors"]
        faces, hinge = leaf(d)
        mesh(ob, faces, Matrix.Translation(hinge))
    for me in list(bpy.data.meshes):               # the parts' grey meshes, and the doors' blanks
        if me.users == 0:
            bpy.data.meshes.remove(me)
    for i, (x, y, z) in enumerate(lamps):
        data = bpy.data.lights.new(f"lamp_{i}", 'POINT')
        data.energy, data.color, data.shadow_soft_size = LAMP_W, (1.0, 0.93, 0.82), 0.45
        ob = bpy.data.objects.new(f"lamp_{i}", data)
        bpy.context.scene.collection.objects.link(ob)
        ob.parent = groups["Lights"]
        ob.location = (x, y, z - 0.35)
    for name, at, turn in (("Spawn_Attack", (-31, -22.5, 0.05), 135), ("Spawn_Defend", (31, 22.75, 0.05), -45)):
        ob = empty(name, groups["Sites"])           # each faces the middle of the arena
        ob.location, ob.rotation_euler.z = at, math.radians(turn)
    for m in list(bpy.data.materials):
        if "s3d_material" in m:
            bpy.data.materials.remove(m)
    OUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(OUT / "arena_liminal.glb"), export_format='GLB', export_image_format='AUTO',
                              export_texcoords=True, export_normals=True, export_materials='EXPORT', export_yup=True,
                              export_extras=True, export_lights=True, export_copyright=CREDIT)
    polish(OUT / "arena_liminal.glb")
    bpy.ops.export_scene.fbx(filepath=str(OUT / "arena_liminal.fbx"), object_types={'EMPTY', 'MESH', 'LIGHT'},
                             path_mode='COPY', embed_textures=True, bake_anim=False)
    shutil.rmtree(OUT / "obj", ignore_errors=True)   # the OBJ's textures land beside it, so it gets a room of
    (OUT / "obj").mkdir()                            # its own, emptied first: no texture outlives its use
    bpy.ops.wm.obj_export(filepath=str(OUT / "obj" / "arena_liminal.obj"), forward_axis='NEGATIVE_Z', up_axis='Y',
                          export_materials=True, path_mode='COPY', apply_modifiers=True)
    for image in bpy.data.images:
        if not image.packed_file:
            image.pack()
    bpy.context.preferences.filepaths.save_version = 0     # and no .blend1 left beside it
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "arena_liminal.blend"), compress=True)
    (OUT / "arena_liminal.json").write_text(json.dumps({
        "name": "Liminal Arena", "units": "metres",
        "axes": "x east, y north, z up, as in examples/; the files themselves are Y-up",
        "spawn_attack": [-31, -22.5, 0.05], "spawn_defend": [31, 22.75, 0.05],
        "sites": {"A": [-37, 18, -30, 26], "B": [24, -25, 32, -19]},
        "water": [list(z[:4]) for z in ZONES if z[4].startswith("pool_water")],   # shallow: for a splash, a slow step
        "vantages": [{"id": v[0], "title": v[4], "at": list(v[1:4]), "counter": c[0]}
                     for v, c in zip(blockout.VANTAGES, blockout.COUNTERS)],
        "textures": "Retro Textures Liminal 1.0 by Kenney (www.kenney.nl), CC0 -- textures/ in this repo",
    }, indent=1) + "\n")
    print(f"OK - arena_liminal written to {OUT} ({len(parts)} parts, {len(blockout.DOORWAYS)} doors, {len(lamps)} lamps, "
          f"{len(MATS)} materials)")


if __name__ == "__main__":
    main()
