"""The dressed arena packed for a real-time viewer. The source stays as it is.

`examples/arena_liminal.py` keeps every part its own object, at its own pivot, dressed in
Kenney's textures, each a separate image: the file to edit. This script builds that scene again
and writes what a player's machine loads:

- faces out of sight are left out: a wall's foot under the floor and its top under the roof, a
  stair's buried risers, a crate's underside. Where two faces would lie in one plane, one gives
  way, so none remain back to back: with single-sided materials, that is the end of z-fighting.
- what is left of the static arena is one mesh. Faces of one texture lying in one plane are
  merged and cut up again into as few quads as possible. Each door stays its own object about
  its hinge.
- merged this way, a corner of one face can lie on the edge of another (a T-junction): where
  the two work the line out differently by a hair, a crack of less than a pixel can open. The
  source has such places too. `--watertight` splits each such edge, so that there is none, at a
  triangle a corner: 2,461 triangles become 4,664 at 2048.
- two corners within a tenth of a millimetre of each other are one corner, worked out two ways:
  left apart they are a gap of a few microns along an edge to a GPU.
- every texture is in one atlas at its own resolution; no texel is resampled. A glTF material
  cannot tile a corner of an image, so a face is cut once per atlas block of its texture. The
  tool gives each texture the block that buys the most quads saved per texel: `--atlas` is the
  atlas's side, 2048 by default. 512 holds every texture too, but each then has one repeat of
  room, and the arena comes out with more triangles than it went in with (11,043).

    python tools/pack_arena.py
    python tools/pack_arena.py --atlas 512

Before it says done, it proves: nothing visible was lost or added (the area of each texture,
before and after), no two faces left lie in one plane, every face points out of the solid it
belongs to, every texel of every texture is in the atlas unchanged, every kept face samples
the texel it sampled before (asked once of the tool's own mapping and once of the atlas's file,
as a viewer reads it), and the GLB as bytes holds what was shipped: its images, its triangles,
no material double-sided, and with --watertight no T-junction, asked over every triangle of the
file where its node puts it.

These tests share this tool's arithmetic, and a tool that reads a texture one way and writes it
the other passes every one of them. `tools/same_surfaces.py` shares nothing: it asks the two
files from the outside, as cameras would. Run it on what comes out.
"""
import argparse
import bisect
import json
import math
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
import arena_liminal as liminal  # noqa: E402
from same_surfaces import png as decode  # noqa: E402  -- a PNG's texels from its bytes, as any viewer decodes them

GRID = 1e-5        # metres: coordinates are rounded to this. A mesh stores a vertex in 32 bits,
                    # so one corner of two touching parts can differ by a few microns; nothing
                    # in the arena is as small as ten
HIDDEN = 1e-4      # metres: this far in front of a face, inside another solid, is out of sight
SEAM = 2e-5         # metres: this far from an edge of a face, another face's corner lies on it
SAME = 1e-4         # metres: two corners this close are one corner, worked out two ways: a part's
                    # own and the one next to it, a few microns apart; the side of a slope built again
                    # from the frame of the face against it. Nothing in the arena is under a centimetre
FLOAT = 2 ** -24    # half a step of a 32-bit float: the farthest one number can read from itself
READ = 2e-5         # repeats of a texture: how far a shipped face may sit from where it was
GROUPS = ("Structure", "Cover", "Sites", "Doors")     # the arena's meshes; Markers are not shipped
ATLAS = 2048            # texels on the atlas's side, unless --atlas says another. 512 holds every texture
                        # but cuts the arena into five times its faces; 2048 is where that cost flattens out
SIZES = (512, 1024, 2048, 4096, 0)    # the table printed at the end; 0 is no atlas


def grid(x):
    return round(round(x / GRID) * GRID, 6)


def clean(v, snap=True):
    """A vector on the grid, a near-integer made the integer. A direction keeps its length:
    `snap` false for it."""
    return Vector(tuple(float(round(c)) if abs(c - round(c)) < 1e-5 else grid(c) if snap else c for c in v))


def signed_area(poly):
    total = 0.0
    for p, q in zip(poly, poly[1:] + poly[:1]):
        total += p[0] * q[1] - q[0] * p[1]
    return total / 2


def area3(poly):
    total = Vector((0, 0, 0))
    for p, q in zip(poly, poly[1:] + poly[:1]):
        total += p.cross(q)
    return total.length / 2


def clip(poly, n, lim):
    """A convex polygon in space kept where n . p <= lim."""
    out = []
    for p, q in zip(poly, poly[1:] + poly[:1]):
        dp, dq = n.dot(p) - lim, n.dot(q) - lim
        if dp <= 0:
            out.append(p)
        if (dp < 0 < dq) or (dq < 0 < dp):
            out.append(p.lerp(q, dp / (dp - dq)))
    return out


def split(poly, a, b):
    """A convex polygon in a plane by the line a -> b: (the part on its left, on its right)."""
    left, right = [], []
    ex, ey = b[0] - a[0], b[1] - a[1]
    for p, q in zip(poly, poly[1:] + poly[:1]):
        dp, dq = ex * (p[1] - a[1]) - ey * (p[0] - a[0]), ex * (q[1] - a[1]) - ey * (q[0] - a[0])
        dp, dq = (0 if abs(dp) < 1e-9 else dp), (0 if abs(dq) < 1e-9 else dq)      # on the line is on the line
        if dp >= 0:
            left.append(p)
        if dp <= 0:
            right.append(p)
        if (dp > 0 > dq) or (dq > 0 > dp):
            x = (p[0] + (q[0] - p[0]) * dp / (dp - dq), p[1] + (q[1] - p[1]) * dp / (dp - dq))
            left.append(x)
            right.append(x)
    return left, right


def subtract(poly, covers):
    """`poly` without the convex `covers`: convex pieces, none inside a cover."""
    pieces = [poly]
    for cover in covers:
        if signed_area(cover) < 0:
            cover = cover[::-1]
        rest = []
        for piece in pieces:
            inside = piece
            for a, b in zip(cover, cover[1:] + cover[:1]):
                if len(inside) < 3:
                    break
                inside, outside = split(inside, a, b)
                if len(outside) >= 3 and abs(signed_area(outside)) > 1e-12:
                    rest.append(outside)
        pieces = rest
    return pieces


def overlap(a, b):
    """The area two convex polygons of one plane share."""
    if signed_area(b) < 0:
        b = b[::-1]
    for p, q in zip(b, b[1:] + b[:1]):
        a = split(a, p, q)[0] if len(a) >= 3 else a
    return abs(signed_area(a)) if len(a) >= 3 else 0.0


def mapped(amap, q):
    """A corner of a face, in the frame its texture was read in, carried to its place on the
    texture: uv = offset + matrix . q."""
    (ox, oy), ((a, b), (c, d)) = amap
    return (ox + a * q[0] + b * q[1], oy + c * q[0] + d * q[1])


def fit(points, uvs):
    """The matrix of `uvs` as a linear map of `points`, through the three points that lie widest
    apart. Worked in exact doubles: a texture's place is stored in 32-bit floats, and so is every
    `mathutils` vector."""
    p = [(float(q[0]), float(q[1])) for q in points]
    e = [(float(q[0]), float(q[1])) for q in uvs]
    best, widest = None, 0.0
    for b in range(1, len(p)):
        for c in range(b + 1, len(p)):
            w = ((p[b][0] - p[0][0], p[c][0] - p[0][0]), (p[b][1] - p[0][1], p[c][1] - p[0][1]))
            det = w[0][0] * w[1][1] - w[0][1] * w[1][0]
            if abs(det) > widest:
                best, widest = (b, c, w, det), abs(det)
    b, c, w, det = best
    d = ((e[b][0] - e[0][0], e[c][0] - e[0][0]), (e[b][1] - e[0][1], e[c][1] - e[0][1]))
    return (((d[0][0] * w[1][1] - d[0][1] * w[1][0]) / det, (d[0][1] * w[0][0] - d[0][0] * w[0][1]) / det),
            ((d[1][0] * w[1][1] - d[1][1] * w[1][0]) / det, (d[1][1] * w[0][0] - d[1][0] * w[0][1]) / det))


def offset_of(points, uvs, matrix):
    """The offset that goes with `matrix`: what is left of each uv once the matrix is taken off,
    on average over the corners."""
    left = [0.0, 0.0]
    for q, uv in zip(points, uvs):
        at = mapped(((0.0, 0.0), matrix), q)
        left[0], left[1] = left[0] + uv[0] - at[0], left[1] + uv[1] - at[1]
    return (left[0] / len(points), left[1] / len(points))


def read(x, noise):
    """A texture's scale or offset as one face reads it: (the number to map by, the number to
    group by). A mesh keeps a texture's place in 32-bit floats, so the same metre of wall reads
    1 from one face and 1.0000002 from the next: within `noise` of a short decimal, it is that
    decimal. A slope's scale, 0.48507..., is no short decimal: rounded to four places it slid
    the slope's texture by a thirtieth of a texel, so it stays as read, and faces group by it
    to five places."""
    for digits in range(1, 6):
        short = round(x, digits)
        if abs(short - x) <= noise:
            return short, short
    return x, round(x, 5)


def axes(m):
    """Does a texture run along the frame its face was read in? (False, the matrix with what lies
    off the diagonal set to nothing), (True, the same for one that runs across them), or (None,
    the matrix as it is). What lies off the diagonal in the first two is the noise of 32-bit
    floats."""
    big = max(abs(c) for row in m for c in row)
    if abs(m[0][1]) < 1e-4 * big and abs(m[1][0]) < 1e-4 * big:
        return False, ((m[0][0], 0.0), (0.0, m[1][1]))
    if abs(m[0][0]) < 1e-4 * big and abs(m[1][1]) < 1e-4 * big:
        return True, ((0.0, m[0][1]), (m[1][0], 0.0))
    return None, m


def placed(points, uvs, span):
    """How the texture of a face sits on it, read from its corners at `points`, in metres, and
    their places `uvs`, in repeats: (does the texture run across the frame, the matrix, the
    offset, the key that faces of one placement share), the first None where the texture runs
    askew to the frame. `span` is how far the face reaches along each way of the frame: a short
    face far up its texture reads its scale the more loosely."""
    swap, m = axes(fit(points, uvs))
    if swap is None:
        m = fit(points, uvs)
        return None, m, offset_of(points, uvs, m), None
    reach = max(abs(c) for uv in uvs for c in uv)
    du, dv = (m[0][0], m[1][1]) if not swap else (m[0][1], m[1][0])      # repeats per metre, each way
    scales = [read(a, 2 * FLOAT * reach / s) for a, s in zip((du, dv), span if not swap else span[::-1])]
    m = ((scales[0][0], 0.0), (0.0, scales[1][0])) if not swap else ((0.0, scales[0][0]), (scales[1][0], 0.0))
    offset = offset_of(points, uvs, m)
    loose = 0.0 if all(value == by for value, by in scales) else \
        max(abs(c) for q in points for c in q) / min(span)      # a scale kept as read leans more, the farther the face sits from its frame's corner
    seats = [read(x - math.floor(x), 2 * FLOAT * reach * (1 + loose)) for x in offset]
    seats = [(value % 1, key % 1) for value, key in seats]       # a corner read a hair under a repeat sits on it
    for q, uv in zip(points, uvs):
        at = mapped((offset, m), q)
        if max(apart(a, b) for a, b in zip(at, uv)) > READ:
            raise ValueError("pack_arena: a face's texture is not an affine map of its corners")
    return swap, m, (seats[0][0], seats[1][0]), (swap, scales[0][1], scales[1][1], seats[0][1], seats[1][1])


class Face:
    def __init__(self, fid, ob, door, solid, pts, uv, style, n):
        self.id, self.ob, self.door, self.solid, self.pts, self.uv, self.style, self.n = (
            fid, ob, door, solid, pts, uv, style, n)
        self.d = grid(n.dot(pts[0]))
        self.area = area3(pts)
        self.lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        self.hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        self.st, self.group, self.cells, self.covers = None, None, [], []


class Solid:
    def __init__(self, name):
        self.name, self.planes = name, []

    def has(self, p):
        """Is `p` inside: on the right side of the plane of every face of it?"""
        return all(n.dot(p) <= d + GRID for n, d in self.planes)


class Side:
    """One side of one plane that faces an axis: its faces on a grid of (s, t), in metres."""
    def __init__(self, n, d):
        self.n, self.d, self.index = n, d, -1
        upright = abs(n.z) < 0.5
        self.e_s = Vector((-n.y, n.x, 0)) if upright else Vector((1, 0, 0))
        self.e_t = Vector((0, 0, 1)) if upright else Vector((0, 1, 0))
        self.key = (n.x, n.y, n.z, d)
        self.rects, self.groups, self.cov = [], {}, []
        self.by_solid = defaultdict(list)

    def at(self, p):
        return (grid(p.dot(self.e_s)), grid(p.dot(self.e_t)))

    def point(self, s, t):
        return self.e_s * s + self.e_t * t + self.n * self.d

    def centre(self, c):
        i, j = c % self.nx, c // self.nx
        return ((self.xs[i] + self.xs[i + 1]) / 2, (self.ts[j] + self.ts[j + 1]) / 2)

    def cell(self, s, t):
        return (bisect.bisect_right(self.ts, t) - 1) * self.nx + bisect.bisect_right(self.xs, s) - 1


class Group:
    """Faces of one finish, one door or the static arena, on one side, that share one texture
    placement: (the texture runs along t rather than s, metres-to-repeats along s and t, where
    the texture's corner sits) -- or None for flat colour."""
    def __init__(self, style, placement):
        self.style, self.placement = style, placement
        self.faces, self.rects, self.span = [], [], None

    def uv(self, s, t):
        swap, au, av, cu, cv = self.placement
        return (cu + au * t, cv + av * s) if swap else (cu + au * s, cv + av * t)

    def st(self, u, v):
        swap, au, av, cu, cv = self.placement
        return (grid((v - cv) / av), grid((u - cu) / au)) if swap else (grid((u - cu) / au), grid((v - cv) / av))


class Polygon:
    """A face kept in a frame of its own: a slope, a triangle cut from the side of a ramp, or
    a rectangle a slope lies against."""
    def __init__(self, f):
        self.f, self.n, self.d, self.solid, self.style, self.door = f, f.n, f.d, f.solid, f.style, f.door
        self.a = (f.pts[1] - f.pts[0]).normalized()
        self.b = f.n.cross(self.a)
        poly = [(grid(p.dot(self.a)), grid(p.dot(self.b))) for p in f.pts]
        self.own = dict(zip(poly, f.pts))         # each corner of the face by its place in this frame
        self.map = None
        if f.style[0] == "tex":
            wide = (max(p[0] for p in poly) - min(p[0] for p in poly), max(p[1] for p in poly) - min(p[1] for p in poly))
            _, m, offset, _ = placed(poly, f.uv, wide)
            self.map = (offset, m)                                 # uv = offset + matrix . (x, y)
        self.poly = poly if signed_area(poly) > 0 else poly[::-1]
        self.visible, self.lost = [], 0.0

    def point(self, q):
        """A point of the face's own frame, where it stands. A corner of the face itself is
        given back as it is: worked out again in 32 bits, it comes out a few microns from the
        same corner in the frame of the face next to it, and two corners that close, each on
        the edge of the other, are split for ever. Any other point is set on the grid."""
        q = tuple(q)
        if q in self.own:
            return self.own[q]
        return Vector(tuple(grid(c) for c in self.a * q[0] + self.b * q[1] + self.n * self.d))


class Piece:
    """A face as shipped: its corners where they stand, their place on the atlas, its material,
    and where it came from: a `group` of rectangles, or a `polygon`."""
    def __init__(self, pts, uv, material, style, n, door, solid, group=None, polygon=None):
        self.pts, self.uv, self.material, self.style, self.n = pts, uv, material, style, n
        self.door, self.solid, self.group, self.polygon = door, solid, group, polygon
        self.origin = (0.0, 0.0)                # where its grid of atlas blocks starts, in repeats
        self.area = area3(pts)                 # in the coordinates it was laid out in, once shipped


def style_of(mat):
    """What a face is painted with: (a texture's name, how many times over its own colour it
    shines), or (a material of flat colour, the same)."""
    bsdf = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    glow = round(float(bsdf.inputs["Emission Strength"].default_value), 6)
    base = bsdf.inputs["Base Color"]
    if base.is_linked and base.links[0].from_node.bl_idname == "ShaderNodeTexImage":
        return ("tex", base.links[0].from_node.image.name, glow)
    return ("flat", mat, glow)


def load():
    """The dressed scene as faces and solids. Every mesh under the arena's groups is one closed
    convex solid: its faces where it stands, in metres, snapped to the grid."""
    bpy.context.view_layer.update()            # a placement set in this process is not `matrix_world` until this
    faces, solids, doors = [], [], {}
    meshes = [o for o in bpy.data.objects if o.type == 'MESH' and o.parent and o.parent.name in GROUPS]
    for ob in sorted(meshes, key=lambda o: o.name):
        me, m = ob.data, ob.matrix_world
        pts = [clean(m @ v.co) for v in me.vertices]
        layer = me.uv_layers.active.data
        door = ob.name if ob.parent.name == "Doors" else None
        solid, first = Solid(ob.name), len(faces)
        for p in me.polygons:
            n = clean((m.to_3x3() @ p.normal).normalized(), snap=False)
            f = Face(len(faces), ob.name, door, len(solids), [pts[i] for i in p.vertices],
                     [(layer[li].uv[0], layer[li].uv[1]) for li in p.loop_indices],
                     style_of(me.materials[p.material_index]), n)
            f.mat = me.materials[p.material_index]
            if f.area < 1e-9:
                raise ValueError(f"pack_arena: {ob.name} has a face of no area")
            if max(abs(n.dot(q) - f.d) for q in f.pts) > 2 * GRID:      # a slope's corners are snapped one by one
                raise ValueError(f"pack_arena: {ob.name} has a face that is not flat")
            solid.planes.append((n, f.d))
            faces.append(f)
        solid.planes = list({(tuple(n), d): (n, d) for n, d in solid.planes}.values())
        solid.lo = [min(f.lo[i] for f in faces[first:]) for i in range(3)]
        solid.hi = [max(f.hi[i] for f in faces[first:]) for i in range(3)]
        solid.height = solid.hi[2] - solid.lo[2]
        solids.append(solid)
        if door:
            doors[door] = m.copy()
    return faces, solids, doors


def covers(f, solids):
    """The parts of face `f`, as convex polygons, that have the matter of another solid in
    front of them."""
    out = []
    for j, b in enumerate(solids):
        if j == f.solid or any(f.lo[i] > b.hi[i] + 1e-6 or f.hi[i] < b.lo[i] - 1e-6 for i in range(3)):
            continue
        poly = f.pts
        for n, d in b.planes:
            poly = clip(poly, n, d - HIDDEN * n.dot(f.n))
            if len(poly) < 3:
                break
        if len(poly) >= 3 and area3(poly) > 1e-9:
            out.append((j, poly))
    return out


def layout(faces):
    """Every face onto the side of its plane, as a rectangle on the side's grid where it is one
    and its texture runs along its edges, and otherwise as a polygon in a frame of its own."""
    sides, polygons, planes, images = {}, [], {}, {}
    for f in faces:
        key = (f.n.x, f.n.y, f.n.z, f.d)
        if sum(abs(c) > 1e-6 for c in f.n) == 1:           # its plane faces an axis: a slope's does not
            side = sides.setdefault(key, Side(f.n, f.d))
            pts = [(grid(p.dot(side.e_s)), grid(p.dot(side.e_t))) for p in f.pts]
            if len(pts) == 4 and len({p[0] for p in pts}) == 2 and len({p[1] for p in pts}) == 2:
                f.st = (min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts))
            placement = None
            if f.st is not None and f.style[0] == "tex":
                # faces join a group by how their texture sits, as each reads it: a scale that is
                # a short decimal exactly, and at 1.0000002 what it was meant to be. So at
                # 1.0000002 a metre of wall is one block wide, where reading it as it stood left
                # a sliver of a face behind every block cut
                swap, m, offset, reading = placed(pts, f.uv, (f.st[1] - f.st[0], f.st[3] - f.st[2]))
                if swap is None:
                    f.st = None                        # its texture runs askew: never merged
                else:
                    image = f.style[1]
                    if image not in images:
                        im = bpy.data.images[image]
                        images[image] = (Path(bpy.path.abspath(im.filepath)), im.size[0], im.size[1])
                    au, av = (m[0][0], m[1][1]) if not swap else (m[0][1], m[1][0])
                    placement = (reading, (swap, au, av, offset[0], offset[1]))
        if f.st is None:
            g = Polygon(f)
            polygons.append(g)
            planes.setdefault(key, []).append(g)
            continue
        name = f.style[1] if f.style[0] == "tex" else f.style[1].name
        group = side.groups.setdefault((placement[0] if placement else None, name, f.style[2], f.door),
                                       Group(f.style, placement[1] if placement else None))
        reach = max(f.st[1] - f.st[0], f.st[3] - f.st[2])
        if placement and reach > (group.span or 0.0):   # the longest face of a group reads its placement best
            group.span, group.placement = reach, placement[1]
        f.group = group
        f.group.side, f.group.door = side, f.door
        f.group.faces.append(f)
        side.rects.append(f)
        side.by_solid[f.solid].append(f)
    return sides, polygons, planes, images


def opposite_faces(key, j, p, sides, planes):
    """The faces of solid `j` on the other side of plane `key`, at the point `p` of it."""
    k = (-key[0], -key[1], -key[2], -key[3])
    side = sides.get(k)
    if side is None:
        return []
    q = side.at(p)
    out = [f for f in side.by_solid.get(j, []) if f.st[0] <= q[0] <= f.st[1] and f.st[2] <= q[1] <= f.st[3]]
    for g in planes.get(k, []):
        if g.solid == j and all((b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]) >= -1e-9
                                for a, b in zip(g.poly, g.poly[1:] + g.poly[:1])):
            out.append(g.f)
    return out


def hide(faces, solids, sides, polygons, planes):
    """Out of the file what nothing can see, and a winner for every pair of faces that would
    lie in one plane. Returns (the faces that go whole, the grid cells that go, a count).

    A face with matter in front of all of it goes. A big face that is only party hidden keeps
    its hidden part when the face in front of it is one that went: the two never meet, and a
    ceiling stays whole above its walls and a floor whole under them, where cutting both would
    leave a hole for every wall. What is left of two faces still face to face is settled, the
    smaller giving way."""
    moved = []
    for side in sides.values():
        for f in side.rects:
            for j, poly in covers(f, solids):
                q = [side.at(p) for p in poly]
                box = (min(p[0] for p in q), max(p[0] for p in q), min(p[1] for p in q), max(p[1] for p in q))
                if (len({p[0] for p in q}) > 2 or len({p[1] for p in q}) > 2
                        or abs(abs(signed_area(q)) - (box[1] - box[0]) * (box[3] - box[2])) > 1e-9):
                    moved.append(f)                # a slope lies against it: it is cut as a polygon
                    f.covers = []
                    break
                f.covers.append((j, *box))
    for f in moved:
        key = (f.n.x, f.n.y, f.n.z, f.d)
        f.group.faces.remove(f)
        sides[key].rects.remove(f)
        sides[key].by_solid[f.solid].remove(f)
        f.st = None
        g = Polygon(f)
        polygons.append(g)
        planes.setdefault(key, []).append(g)

    duplicates = []
    for si, side in enumerate(sides.values()):
        side.index = si
        xs, ts = set(), set()
        for f in side.rects:
            xs.update(f.st[:2])
            ts.update(f.st[2:])
            for _, s0, s1, t0, t1 in f.covers:
                xs.update((s0, s1))
                ts.update((t0, t1))
        side.xs, side.ts = sorted(xs), sorted(ts)
        side.nx = nx = len(side.xs) - 1
        side.owner = [-1] * (nx * (len(side.ts) - 1))
        side.cov = [[] for _ in side.owner]
        for f in side.rects:
            i0, i1 = (bisect.bisect_left(side.xs, x) for x in f.st[:2])
            j0, j1 = (bisect.bisect_left(side.ts, x) for x in f.st[2:])
            f.cells = [j * nx + i for j in range(j0, j1) for i in range(i0, i1)]
            for c in f.cells:
                if side.owner[c] >= 0 and faces[side.owner[c]].solid != f.solid:
                    duplicates.append((faces[side.owner[c]].ob, f.ob))
                side.owner[c] = f.id
            for j, s0, s1, t0, t1 in f.covers:
                i0, i1 = (bisect.bisect_left(side.xs, x) for x in (s0, s1))
                j0, j1 = (bisect.bisect_left(side.ts, x) for x in (t0, t1))
                for jj in range(j0, j1):
                    for ii in range(i0, i1):
                        if j not in side.cov[jj * nx + ii]:
                            side.cov[jj * nx + ii].append(j)

    removed = {f.id for side in sides.values() for f in side.rects if all(side.cov[c] for c in f.cells)}

    for g in polygons:
        against = []
        for j, poly in covers(g.f, solids):
            others = opposite_faces((g.n.x, g.n.y, g.n.z, g.d), j, sum(poly, Vector()) / len(poly), sides, planes)
            if not others or any(o.st is None or o.id not in removed for o in others):
                q = [(p.dot(g.a), p.dot(g.b)) for p in poly]
                against.append(q if signed_area(q) > 0 else q[::-1])
        before = abs(signed_area(g.poly))
        g.visible = subtract(g.poly, against) if against else [g.poly]
        g.lost = before - sum(abs(signed_area(p)) for p in g.visible)

    cut, fought = set(), 0
    for side in sides.values():
        for f in side.rects:
            if f.id in removed:
                continue
            for c in f.cells:
                if not side.cov[c] or (side.index, c) in cut:
                    continue
                p = side.point(*side.centre(c))
                for j in sorted(side.cov[c]):
                    others = opposite_faces(side.key, j, p, sides, planes)
                    if not others:                 # the matter of `j` is on this side of the plane
                        cut.add((side.index, c))
                        break
                    alive = [o for o in others if o.st is not None and o.id not in removed]
                    if not alive:
                        continue                   # a face that went, or one that gives way
                    other = sides[(alive[0].n.x, alive[0].n.y, alive[0].n.z, alive[0].d)]
                    hc = other.cell(*other.at(p))
                    if (other.index, hc) in cut:
                        continue
                    fought += 1
                    h = alive[0]
                    if ((round(f.area, 6), solids[f.solid].height, f.ob)
                            < (round(h.area, 6), solids[h.solid].height, h.ob)):
                        cut.add((side.index, c))
                        break
                    cut.add((other.index, hc))
    return removed, cut, fought, duplicates


def strips(mask, nx, nt, flip):
    """Rows of full runs, a run that goes on into the row below joined to it; columns, flipped."""
    out, going = [], {}
    for j in range(nt):
        now = {}
        i = 0
        while i < nx:
            if mask[j * nx + i]:
                start = i
                while i < nx and mask[j * nx + i]:
                    i += 1
                now[(start, i)] = going.get((start, i), j)
            else:
                i += 1
        out += [(run[0], run[1], top, j) for run, top in going.items() if run not in now]
        going = now
    out += [(run[0], run[1], top, nt) for run, top in going.items()]
    return [(c, d, a, b) for a, b, c, d in out] if flip else out


def greedy(mask, nx, nt):
    """The largest rectangle of what is left, again until none is: a ceiling keeps a band
    between two rows of lit tiles whole, a stair's flank its flight."""
    free, out = mask[:], []
    while True:
        left = [i for i, v in enumerate(free) if v]
        if not left:
            return out
        i0, i1 = min(i % nx for i in left), max(i % nx for i in left) + 1
        j0, j1 = min(i // nx for i in left), max(i // nx for i in left) + 1
        heights, best = [0] * (i1 - i0), None
        for j in range(j0, j1):
            stack = []
            for i in range(i0, i1 + 1):
                h = 0
                if i < i1 and free[j * nx + i]:
                    heights[i - i0] += 1
                    h = heights[i - i0]
                elif i < i1:
                    heights[i - i0] = 0
                start = i
                while stack and stack[-1][1] >= h:
                    s, sh = stack.pop()
                    if sh and (best is None or (sh * (i - s), min(sh, i - s), -j, -i) > best[0]):
                        best = ((sh * (i - s), min(sh, i - s), -j, -i), (s, i, j - sh + 1, j + 1))
                    start = s
                if h and (not stack or stack[-1][1] < h):
                    stack.append((start, h))
        s0, s1, t0, t1 = best[1]
        for j in range(t0, t1):
            for i in range(s0, s1):
                free[j * nx + i] = False
        out.append(best[1])


def partition(mask, nx, nt):
    """A grid's marked cells as few rectangles as one of three ways of counting finds."""
    best = strips(mask, nx, nt, False)
    for rects in (strips([mask[j * nx + i] for i in range(nx) for j in range(nt)], nt, nx, True), greedy(mask, nx, nt)):
        if len(rects) < len(best):
            best = rects
    return best


def finish(style):
    """A finish's name: its texture's, or its flat material's."""
    return style[1] if style[0] == "tex" else style[1].name


def merge(faces, sides, polygons, removed, cut):
    """The cells that stay, per group, laid out as few rectangles as the grid allows. Returns
    the groups, with their rectangles, and, per finish, the area that is still in sight."""
    kept, seen = [], defaultdict(float)
    for side in sides.values():
        cells_of = defaultdict(list)
        for c, fid in enumerate(side.owner):
            if fid >= 0 and fid not in removed and (side.index, c) not in cut:
                cells_of[faces[fid].group].append(c)
        for group, cells in cells_of.items():
            i0, i1 = min(c % side.nx for c in cells), max(c % side.nx for c in cells) + 1
            j0, j1 = min(c // side.nx for c in cells), max(c // side.nx for c in cells) + 1
            w = i1 - i0
            mask = [False] * (w * (j1 - j0))
            for c in cells:
                mask[(c // side.nx - j0) * w + c % side.nx - i0] = True
            for a, b, c, d in partition(mask, w, j1 - j0):
                group.rects.append((side.xs[i0 + a], side.ts[j0 + c], side.xs[i0 + b], side.ts[j0 + d]))
                seen[finish(group.style), group.style[2]] += (side.xs[i0 + b] - side.xs[i0 + a]) * (side.ts[j0 + d] - side.ts[j0 + c])
            kept.append(group)
    for g in polygons:
        seen[finish(g.style), g.style[2]] += sum(abs(signed_area(p)) for p in g.visible)
    return kept, seen


HAIR = 3e-5        # repeats of a texture: this close to an edge, a cut is not made and an overshoot
                    # is not one. A face's own reading of its texture is a few hundred-thousandths
                    # off (its place is kept in 32-bit floats, thirty repeats out), a corner just
                    # cut reads a hair off its line, and a scale is no round number: without this
                    # a door one repeat wide was cut a sliver two millionths wide


def cuts(lo, hi, step, apron):
    """`lo` to `hi`, in repeats of a texture, cut where a block of `step` repeats ends: each
    strip with the whole repeat its block starts at. A block is its texture from a whole
    repeat, and round it lies the texture's own continuation, an apron of `apron` repeats: the
    block is set as far back as that and a hair allow, so that it reaches as far on as it can,
    and a face no longer than a block is never cut."""
    out = []
    while True:
        start = math.floor(lo + apron + HAIR)
        reach = start + step + apron
        end = hi if hi <= reach + HAIR else min(hi, reach)
        out.append((lo, end, start))
        if end >= hi:
            return out
        lo = end


def pieces(rect, block):
    """A rectangle, in repeats of its texture, cut where one atlas block of it ends: each piece
    with the corner of the block it lies in, a whole repeat of the texture. So the piece's place
    in its block is its place in the texture, texel for texel."""
    (u0, u1, v0, v1), (a, b, au, av) = rect, block
    us, vs = cuts(u0, u1, a, au), cuts(v0, v1, b, av)
    return [((x0, x1, y0, y1), (start, rise)) for y0, y1, rise in vs for x0, x1, start in us]


def gutter(side):
    """Texels of a texture's own continuation round its block: enough for the mip levels a
    viewer gets to before the arena is small on screen (4: to level 3, 8: to level 4)."""
    return 4 if side <= 128 else 8 if side <= 256 else 16 if side <= 512 else 32


def extent(image, block, images):
    """A block's (width, height, margin on each side along x, margin along y) in texels. A
    block is `a` by `b` repeats of its texture; the margin round it is the apron, where a face
    may start off a whole repeat, and outside that the gutter for mips; both are the texture's
    own continuation."""
    _, iw, ih = images[image]
    a, b, au, av = block
    px, py = au * iw, av * ih                            # the apron in texels: a whole number, or no texel is where it was
    if abs(px - round(px)) > 1e-9 or abs(py - round(py)) > 1e-9:
        raise ValueError(f"pack_arena: an apron of {au}, {av} repeats of {image} is not a whole number of texels")
    px, py = round(px), round(py)
    g = gutter(max(a * iw, b * ih))
    return a * iw + 2 * (px + g), b * ih + 2 * (py + g), px + g, py + g


def skyline(items, size):
    """Places for blocks, each top-left, in an atlas of `size`: along the lowest skyline."""
    sky, pos = [[0, size, 0]], {}
    for name, w, h in items:
        best = None
        for i, (x, _, _) in enumerate(sky):
            if x + w > size:
                continue
            level, j, reach = sky[i][2], i, x
            while reach < x + w:
                level = max(level, sky[j][2])
                reach, j = sky[j][0] + sky[j][1], j + 1
            if level + h <= size and (best is None or level < best[0]):
                best = (level, x)
        if best is None:
            return None, size
        level, x = best
        pos[name] = (x, level)
        new = []
        for sx, sw, sy in sky:
            if sx + sw <= x or sx >= x + w:
                new.append([sx, sw, sy])
            else:
                if sx < x:
                    new.append([sx, x - sx, sy])
                if sx + sw > x + w:
                    new.append([x + w, sx + sw - x - w, sy])
        new.append([x, w, level + h])
        new.sort()
        sky = []
        for seg in new:
            if sky and sky[-1][2] == seg[2] and sky[-1][0] + sky[-1][1] == seg[0]:
                sky[-1][1] += seg[1]
            else:
                sky.append(seg)
    return pos, max(seg[2] for seg in sky)


def pack(blocks, images, size, orders):
    """The blocks in an atlas of `size`, the best of a few ways of ordering them: (where each
    sits, how tall the stack is), or None if they do not fit."""
    items = [(k, *extent(k, b, images)[:2]) for k, b in blocks.items()]
    best = None
    for order in orders:
        pos, top = skyline(order(items), size)
        if pos is not None and (best is None or top < best[1]):
            best = (pos, top)
    return best


TALL = lambda items: sorted(items, key=lambda t: (-t[2], -t[1], t[0]))            # noqa: E731
BIG = lambda items: sorted(items, key=lambda t: (-(t[1] * t[2]), -max(t[1], t[2]), t[0]))   # noqa: E731
WIDE = lambda items: sorted(items, key=lambda t: (-t[1], -t[2], t[0]))            # noqa: E731


def allocate(groups, polygons, images, size):
    """The atlas block of each texture, in repeats: the one that saves the most quads per
    texel, for as long as everything fits in `size` by `size` (0: no limit). Returns the
    blocks and, if they fit, where each sits in the atlas."""
    rects = defaultdict(list)
    for group in groups:
        if group.style[0] == "tex":
            for s0, t0, s1, t1 in group.rects:
                (u0, v0), (u1, v1) = group.uv(s0, t0), group.uv(s1, t1)
                rects[group.style[1]].append((min(u0, u1), max(u0, u1), min(v0, v1), max(v0, v1)))
    for g in polygons:
        if g.style[0] == "tex":
            uv = [mapped(g.map, q) for q in g.poly]
            rects[g.style[1]].append((min(p[0] for p in uv), max(p[0] for p in uv), min(p[1] for p in uv), max(p[1] for p in uv)))
    cache = {}

    def count(image, block):
        if (image, block) not in cache:
            cache[(image, block)] = sum(len(pieces(r, block)) for r in rects[image])
        return cache[(image, block)]

    blocks = {k: (1, 1, 0.0, 0.0) for k in images}
    fits = lambda b: pack(b, images, size, (TALL,)) if size else (None, 0)    # noqa: E731
    if size and fits(blocks)[0] is None:
        return None, None, 0
    while True:
        best = None
        for image in images:
            a, b, au, av = blocks[image]
            cands = [(a + 1, b, au, av), (a, b + 1, au, av), (2 * a, b, au, av), (a, 2 * b, au, av)]
            if size:                                  # an apron of half a repeat, each way it is not one yet
                cands += [c for c in ((a, b, 0.5, av), (a, b, au, 0.5)) if c != blocks[image]]
            for cand in cands:
                if max(cand[0], cand[1]) > 64 or (size and max(extent(image, cand, images)[:2]) > size):
                    continue
                trial = dict(blocks)
                trial[image] = cand
                if size and fits(trial) is None:
                    continue
                gain = count(image, blocks[image]) - count(image, cand)
                cost = (extent(image, cand, images)[0] * extent(image, cand, images)[1]
                        - extent(image, blocks[image], images)[0] * extent(image, blocks[image], images)[1])
                if gain > 0 and (best is None or gain / cost > best[0]):
                    best = (gain / cost, image, cand)
        if best is None:
            break
        blocks[best[1]] = best[2]
    quads = sum(len(pieces(r, blocks[k])) for k, rs in rects.items() for r in rs)
    placed = pack(blocks, images, size, (TALL, BIG, WIDE)) if size else (None, 0)
    return blocks, (placed[0] if placed else None), quads


def texels(image, images):
    """A texture as it is in its file: RGB bytes, the first row the top one."""
    path, iw, ih = images[image]
    px = bpy.data.images.load(str(path), check_existing=True).pixels[:]
    out = bytearray(iw * ih * 3)
    for y in range(ih):
        src = (ih - 1 - y) * iw * 4                    # Blender reads an image from its bottom row
        for x in range(iw):
            for c in range(3):
                out[(y * iw + x) * 3 + c] = round(px[src + 4 * x + c] * 255)
    return out, iw, ih


def compose(blocks, placed, images, size):
    """The atlas as RGB bytes, the first row the top one: every texture's block where `placed`
    put it, each texel the one it is in its own file, the rest of the sheet their average."""
    sheet, total, count = [], [0, 0, 0], 0
    for image, (a, b, au, av) in blocks.items():
        raw, iw, ih = texels(image, images)
        w, h, mx, my = extent(image, (a, b, au, av), images)
        rows = []
        for y in range(h):
            one = raw[((y - my) % ih) * iw * 3:((y - my) % ih + 1) * iw * 3]
            rows.append(one[(iw - mx) * 3:] + one * a + one[:mx * 3])
            for c in range(3):
                total[c] += sum(one[c::3]) * a
            count += iw * a
        sheet.append((*placed[image], rows))
    data = bytearray(bytes(round(t / max(count, 1)) for t in total) * (size * size))
    for x0, y0, rows in sheet:
        for y, row in enumerate(rows):
            at = ((y0 + y) * size + x0) * 3
            data[at:at + len(row)] = row
    return data


def write_png(path, size, data):
    """`data`, RGB bytes from the top row down, as a PNG: the same bytes go into the GLB."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)                                   # filter type 0: none
        raw += data[y * size * 3:(y + 1) * size * 3]

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def frac(x):
    return x - math.floor(x)


def apart(a, b):
    """Two places on a texture, as fractions of a repeat: how far apart they are, either way round."""
    return abs((a - b + 0.5) % 1 - 0.5)


def atlas_uv(image, u, v, block, origin, placed, images, size):
    """A point of a face, in repeats of its texture, as a place on the atlas: in the block of
    its texture that starts at the whole repeat `origin`, which a face may overrun by its apron
    and no more than a hair."""
    a, b, au, av = block
    _, iw, ih = images[image]
    _, _, mx, my = extent(image, block, images)        # the block's first texel is apron and gutter, both ways
    x0, y0 = placed[image]
    ru, rv = u - origin[0], v - origin[1]
    return ((x0 + mx + ru * iw) / size, 1 - (y0 + my + (b - rv) * ih) / size)


def turn(pts, n):
    """Twice the area of a polygon, signed: positive when its corners run round the way `n`
    points. Summed from its first corner, in exact doubles: a Blender vector keeps 32 bits, and
    at thirty metres from the origin that is thicker than a strip of a wall."""
    p0 = tuple(pts[0])
    total = 0.0
    for p, q in zip(pts, pts[1:] + pts[:1]):
        a = tuple(x - y for x, y in zip(p, p0))
        b = tuple(x - y for x, y in zip(q, p0))
        total += (a[1] * b[2] - a[2] * b[1]) * n[0] + (a[2] * b[0] - a[0] * b[2]) * n[1] + (a[0] * b[1] - a[1] * b[0]) * n[2]
    return total


def wound(pts, uv, n):
    """The same face, wound to face `n`."""
    return (pts, uv) if turn(pts, n) > 0 else (pts[::-1], uv[::-1])


def way_back(offset, matrix):
    """A polygon's frame from a place on its texture: the map's inverse, written out where the
    texture runs along the frame's axes, so that a corner just cut on a block line is exactly
    on the line when it comes back."""
    (a, b), (c, d) = matrix
    if b == 0 and c == 0:
        return lambda uv: ((uv[0] - offset[0]) / a, (uv[1] - offset[1]) / d)
    if a == 0 and d == 0:
        return lambda uv: ((uv[1] - offset[1]) / c, (uv[0] - offset[0]) / b)
    det = a * d - b * c
    return lambda uv: ((d * (uv[0] - offset[0]) - b * (uv[1] - offset[1])) / det,
                       (a * (uv[1] - offset[1]) - c * (uv[0] - offset[0])) / det)


def cut_polygons(polys, to_uv, block, from_uv):
    """Polygons, in repeats of their texture, cut where one atlas block of it ends. The cut is
    made in the texture's own coordinates, in plain floats (a Blender vector keeps 32 bits: a
    cut half a millionth from an edge came back on it, and the piece was its parent again),
    and each piece's block starts at a whole repeat, as far back as its apron allows. Returns
    each piece three times: there, in the polygon's frame, and the repeat its block starts at."""
    out, todo = [], []
    a, b, au, av = block
    for q in polys:
        uv = [(grid(p[0]), grid(p[1])) for p in map(to_uv, q)]
        todo.append(uv if signed_area(uv) > 0 else uv[::-1])
    while todo:
        uv = todo.pop()
        us, vs = [p[0] for p in uv], [p[1] for p in uv]
        origin = (math.floor(min(us) + au + HAIR), math.floor(min(vs) + av + HAIR))
        if max(us) > origin[0] + a + au + HAIR:            # over the edge of its block along u
            ends = ((origin[0] + a + au, min(vs)), (origin[0] + a + au, max(vs)))
        elif max(vs) > origin[1] + b + av + HAIR:
            ends = ((min(us), origin[1] + b + av), (max(us), origin[1] + b + av))
        else:
            out.append((uv, [from_uv(p) for p in uv], origin))
            continue
        for piece in split(uv, *ends):
            if (len(piece) >= 3 and abs(signed_area(piece)) > 1e-12
                    and min(p[0] for p in piece) < max(p[0] for p in piece) - HAIR
                    and min(p[1] for p in piece) < max(p[1] for p in piece) - HAIR):
                todo.append([tuple(p) for p in piece] if signed_area(piece) > 0 else [tuple(p) for p in piece[::-1]])
    return out


def ship(groups, polygons, blocks, placed, images, size, material):
    """Every face as it ships, by door (None: the static arena). `material(style)` is the
    material a face of that style is painted with: with an atlas (`size`), the one that shines
    as many times over its own colour; without one, the texture's own. A piece's area is taken in the
    coordinates it was laid out in, which are exact, not from its corners in space, which a
    Blender vector holds to 32 bits."""
    out = defaultdict(list)

    def emit(pts, uv, n, paint, style, door, solid, where, area, origin=(0.0, 0.0)):
        piece = Piece(*wound(pts, uv, n), paint, style, n, door, solid, **where)
        piece.area, piece.origin = area, origin
        out[door].append(piece)

    for group in groups:
        side, door = group.side, group.door
        if group.placement is None:
            for s0, t0, s1, t1 in group.rects:
                pts = [side.point(s, t) for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1))]
                emit(pts, [(0, 0)] * 4, side.n, material(group.style), group.style, door, group.faces[0].solid,
                     {"group": group}, (s1 - s0) * (t1 - t0))
            continue
        image, block = group.style[1], blocks[group.style[1]]
        for s0, t0, s1, t1 in group.rects:
            (u0, v0), (u1, v1) = group.uv(s0, t0), group.uv(s1, t1)
            rect = (min(u0, u1), max(u0, u1), min(v0, v1), max(v0, v1))
            for p, origin in pieces(rect, block):
                pts, uv = [], []
                for u, v in ((p[0], p[2]), (p[1], p[2]), (p[1], p[3]), (p[0], p[3])):
                    pts.append(side.point(*group.st(u, v)))
                    uv.append(atlas_uv(image, u, v, block, origin, placed, images, size) if size else (u, v))
                (x0, y0), (x1, y1) = group.st(p[0], p[2]), group.st(p[1], p[3])
                emit(pts, uv, side.n, material(group.style), group.style, door, group.faces[0].solid,
                     {"group": group}, abs((x1 - x0) * (y1 - y0)), origin)
    for g in polygons:
        paint = material(g.style)
        if g.map is None:
            for q in g.visible:
                emit([g.point(p) for p in q], [(0, 0)] * len(q), g.n,
                     paint, g.style, g.door, g.solid, {"polygon": g}, abs(signed_area(q)))
            continue
        image, block = g.style[1], blocks[g.style[1]]
        offset, matrix = g.map
        for uv, q, origin in cut_polygons(g.visible, lambda p: mapped(g.map, p), block, way_back(offset, matrix)):
            emit([g.point(p) for p in q],
                 [atlas_uv(image, p[0], p[1], block, origin, placed, images, size) if size else (p[0], p[1])
                  for p in uv], g.n, paint, g.style, g.door, g.solid, {"polygon": g}, abs(signed_area(q)), origin)
    return out


def on_edges(a, b, places, lines):
    """The places, of all the corners of a mesh, that lie on the way from `a` to `b`, in that
    order, each as (the place, how far along the edge it is, 0 to 1). A place lies on an edge
    when it is within `SEAM` of the edge's line and more than `SEAM` from either end: one rule
    for every edge, for a corner can come out a grid step off the line it belongs to. `lines`
    is `places` by the line each lies on when that line runs along an axis, the line named in
    grid steps: such an edge asks only the places within `SEAM` of its own line. A sloping edge
    asks `places`, which is every corner once, sorted, over its own box."""
    ta, tb = tuple(a), tuple(b)
    d = [y - x for x, y in zip(ta, tb)]
    square, length = sum(c * c for c in d), math.dist(ta, tb)
    if not square:
        return []
    axis = next((k for k in range(3) if d[(k + 1) % 3] == 0 and d[(k + 2) % 3] == 0), None)
    low, high = [min(x, y) - SEAM for x, y in zip(ta, tb)], [max(x, y) + SEAM for x, y in zip(ta, tb)]
    if axis is None:
        far = float("inf")
        over = places[bisect.bisect_left(places, (low[0], -far, -far)):bisect.bisect_right(places, (high[0], far, far))]
    else:
        u, v, step = (axis + 1) % 3, (axis + 2) % 3, round(SEAM / GRID)
        over = [q for i in range(-step, step + 1) for j in range(-step, step + 1)
                for q in lines.get((axis, round(ta[u] / GRID) + i, round(ta[v] / GRID) + j), ())]
    on = []
    for q in over:
        if not (low[0] <= q[0] <= high[0] and low[1] <= q[1] <= high[1] and low[2] <= q[2] <= high[2]):
            continue                              # only what lies over the edge's box can lie on it
        w = [c - x for c, x in zip(q, ta)]
        t = sum(x * y for x, y in zip(w, d)) / square
        if SEAM / length < t < 1 - SEAM / length and math.dist(w, [t * c for c in d]) < SEAM:
            on.append((q, t))
    return sorted(on, key=lambda q: q[1])


def corners(pieces):
    """Every corner of these pieces once, sorted, and the same by the line each lies on."""
    return corners_of(sorted({tuple(c) for piece in pieces for c in piece.pts}))


def one_corner(pieces):
    """`pieces` with every two corners within `SAME` of each other made one corner, which stands
    for all of them: the one that most faces hold. Left apart, they are a gap of a few microns
    along an edge to a GPU, and to the mending two corners that each lie on the other's edge."""
    taken, buckets, held = {}, defaultdict(list), defaultdict(int)
    for piece in pieces:
        for c in piece.pts:
            held[tuple(c)] += 1

    def near(q):
        at = [math.floor(c / SAME) for c in q]
        return (p for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
                for p in buckets.get((at[0] + i, at[1] + j, at[2] + k), ()) if math.dist(p, q) <= SAME)

    for q in sorted(held, key=lambda q: (-held[q], q)):
        taken[q] = next(near(q), q)
        if taken[q] == q:
            buckets[tuple(math.floor(c / SAME) for c in q)].append(q)
    for piece in pieces:
        pts, uv = [], []
        for p, u in zip(piece.pts, piece.uv):
            q = taken[tuple(p)]
            if q not in pts:                   # a corner of a sliver thinner than that, back on one before it: gone
                pts.append(q)
                uv.append(u)
        piece.pts, piece.uv = pts, uv


def mend(pieces, mended=True):
    """Make these pieces into triangles, and return them: (how many
    edges were split, how many corners were put on them, the corners of the mesh once, each
    triangle as (its piece, three corners, each the number of its place and its place on the
    texture)). Two faces that share an edge are watertight only if both hold the same corners
    on it. A face drawn from (a, b) with its neighbour drawn from (a, v) and (v, b) each works
    the line out for itself, and where they differ by a hair a crack of less than a pixel
    opens along it: a T-junction. So first every edge of every piece takes the corners of the
    other pieces that lie on it; then the pieces are cut into triangles; then every edge of
    the triangles, the cuts made by the triangulation included, is asked the same, and each
    triangle holding such an edge is cut again, until none is left. Without `mended` the
    corners are left where they lie: fewer triangles, and a crack of a pixel can open. Every
    mesh's pieces are given together: a door's corner lies on the wall's edge as any other does.
    First, corners within `SAME` of each other are made one (`one_corner`); a piece left with
    fewer than three, or without area, is a sliver and ships nothing."""
    one_corner(pieces)
    places, lines = corners(pieces)
    split, added = 0, 0
    for piece in pieces:
        plan = []
        for i, (a, b) in enumerate(zip(piece.pts, piece.pts[1:] + piece.pts[:1])):
            ua, ub = piece.uv[i], piece.uv[(i + 1) % len(piece.pts)]
            ta, tb = tuple(a), tuple(b)
            plan.append((a, ua))
            on = on_edges(a, b, places, lines) if mended else []
            for q, t in on:                        # the neighbour's own corner, so that both hold one place: one that
                at = tuple(x + t * (y - x) for x, y in zip(ta, tb))     # is a step off the line is put on it, as it
                plan.append((q if math.dist(q, at) < 1e-12 else at,    # is within a hair's distance, and the piece stays flat-sided
                             tuple(x + t * (y - x) for x, y in zip(ua, ub))))
            split += bool(on)
            added += len(on)
        piece.plan = plan if len(piece.pts) > 2 and turn(piece.pts, piece.n) > 1e-12 else []

    by = sorted({tuple(p) for piece in pieces for p, _ in piece.plan})     # every corner once, in order
    index = {q: i for i, q in enumerate(by)}
    _, lines = corners_of(by)
    tris = [(k, [(index[tuple(p)], uv) for p, uv in ((piece.plan[i], piece.plan[j], piece.plan[c]))])
            for k, piece in enumerate(pieces) if piece.plan for i, j, c in ears([p for p, _ in piece.plan], piece.n)]
    asked, cut = set(), set()                        # edges asked already with no corner on them; edges cut
    while True:
        edges = defaultdict(list)
        for n, (_, t) in enumerate(tris):
            for x, y in ((0, 1), (1, 2), (2, 0)):
                edges[tuple(sorted((t[x][0], t[y][0])))].append((n, x, y))
        for (a, b), holding in edges.items():
            if (a, b) in asked:
                continue
            on = on_edges(by[a], by[b], by, lines) if mended else []
            if on:
                break
            asked.add((a, b))
        else:
            return split, added, by, tris
        assert (a, b) not in cut, (f"pack_arena: the corner {on[0][0]} lies on the edge from {by[a]} to {by[b]}, "
                                   "and an end of that on the edge from it: two corners a few microns apart are cut for ever")
        cut.add((a, b))
        chain = [(index[q], t) for q, t in on]
        fresh = {}
        for n, x, y in holding:                       # each triangle holding the edge, cut along it
            owner, t = tris[n]
            (na, ua), (nb, ub) = t[x], t[y]
            way = [(na, 0.0)] + chain + [(nb, 1.0)] if na == a else [(na, 0.0)] + [(v, 1.0 - s) for v, s in chain[::-1]] + [(nb, 1.0)]
            run = [(v, tuple(c + s * (d - c) for c, d in zip(ua, ub))) for v, s in way]       # this corner to that one
            third = t[3 - x - y]
            fresh[n] = [(owner, [run[i], run[i + 1], third]) for i in range(len(run) - 1)]
        tris = [new for n, tri in enumerate(tris) for new in fresh.get(n, [tri])]


def corners_of(by):
    """These places by the line each lies on, when that line runs along an axis; the line is
    named in grid steps, so that a place a step off it is found with it."""
    lines = defaultdict(list)
    for q in by:
        for axis in range(3):
            lines[(axis, round(q[(axis + 1) % 3] / GRID), round(q[(axis + 2) % 3] / GRID))].append(q)
    return by, lines


def ears(pts, n):
    """How a flat face of `pts`, wound to face `n`, is cut into triangles, every one of them
    with area and every corner in one: the first corner that makes an ear is cut off, again and
    again, so long as what would be left still has area. A corner on the way between two
    others never makes a sliver of its own, and is kept as a corner of the triangles about it."""
    left, out = list(range(len(pts))), []
    while len(left) > 3:
        for i, b in enumerate(left):
            a, c = left[i - 1], left[(i + 1) % len(left)]
            rest = left[:i] + left[i + 1:]
            if turn([pts[a], pts[b], pts[c]], n) > 1e-12 and turn([pts[k] for k in rest], n) > 1e-12:
                out.append((a, b, c))
                left = rest
                break
        else:
            raise AssertionError(f"pack_arena: a face cannot be cut into triangles: {[tuple(round(c, 5) for c in pts[i]) for i in left]}")
    out.append(tuple(left))
    return out


def mesh_of(name, frame, laid):
    """A Blender mesh from what `mend` made of one mesh's pieces (`laid`: the pieces that ship,
    the corners once, the triangles), where they stand, written in `frame`: its own pivot, so
    that a door still turns about its hinge."""
    pieces, by, tris = laid
    me, verts, slots = bpy.data.meshes.new(name), [frame.inverted() @ Vector(q) for q in by], []
    for piece in pieces:
        if piece.material not in slots:
            slots.append(piece.material)
    me.from_pydata(verts, [], [[c[0] for c in t] for _, t in tris])
    for mat in slots:
        me.materials.append(mat)
    layer = me.uv_layers.new(name="UVMap")
    for p, (k, t) in zip(me.polygons, tris):
        p.material_index = slots.index(pieces[k].material)
        for li, (_, uv) in zip(p.loop_indices, t):
            layer.data[li].uv = uv
    return me



def atlas_material(png, glow):
    """The one textured material, or its shining twin: the atlas, pixels crisp close up."""
    m = bpy.data.materials.new(f"atlas_glow" if glow else "atlas")
    nodes = m.node_tree.nodes
    bsdf = nodes["Principled BSDF"]
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(png), check_existing=True)
    tex.interpolation = 'Closest'
    m.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.85
    if glow:
        m.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = glow
    m.use_backface_culling = True                    # single-sided: glTF's doubleSided false
    return m


def prove(faces, solids, shipped, seen, blocks, placed, images, size):
    """Four tests, each one able to stop the export. Returns how many places it sampled."""
    now = defaultdict(float)
    for pieces in shipped.values():
        for p in pieces:
            now[finish(p.style), p.style[2]] += p.area
    for key, was in seen.items():
        if abs(now[key] - was) > 1e-6 * max(was, 1.0):
            raise AssertionError(f"pack_arena: {key[0]} covers {now[key]:.6f} m2 where the source shows {was:.6f} m2")

    for pieces in shipped.values():
        for p in pieces:
            if turn(p.pts, p.n) <= 1e-12:
                raise AssertionError(f"pack_arena: a face of {finish(p.style)} faces into its own solid (wound the wrong way)")
            middle = sum(p.pts, Vector()) / len(p.pts) - p.n * HIDDEN      # a hair inside: in some solid. A merged
            if not any(s.has(middle) for s in solids):                    # rectangle lies over several parts
                raise AssertionError(f"pack_arena: a face of {finish(p.style)} does not lie on a solid")

    planes = defaultdict(list)
    for pieces in shipped.values():
        for p in pieces:
            planes[(p.n.x, p.n.y, p.n.z, round(p.n.dot(p.pts[0]), 6))].append(p)
    for key, pieces in planes.items():
        n = Vector(key[:3])
        e1 = n.orthogonal().normalized()
        e2 = n.cross(e1)
        flat = []
        for p in pieces:
            q = [(v.dot(e1), v.dot(e2)) for v in p.pts]
            flat.append((q, (min(x for x, _ in q), max(x for x, _ in q), min(y for _, y in q), max(y for _, y in q)), p))
        flat.sort(key=lambda t: t[1][0])
        for i, (qa, box_a, pa) in enumerate(flat):
            for qb, box_b, pb in flat[i + 1:]:
                if box_b[0] >= box_a[1] - 1e-9:
                    break
                if box_b[1] <= box_a[0] + 1e-9 or box_b[3] <= box_a[2] + 1e-9 or box_b[2] >= box_a[3] - 1e-9:
                    continue
                if overlap(qa, qb) > 1e-9:
                    raise AssertionError(f"pack_arena: two faces lie in one plane, {finish(pa.style)} over {finish(pb.style)}")

    looked = 0
    for pieces in shipped.values():
        for p in pieces:
            if p.style[0] != "tex":
                continue
            image, (a, b, au, av) = p.style[1], blocks[p.style[1]]
            _, iw, ih = images[image]
            x0, y0 = placed[image] if size else (0, 0)
            mx, my = extent(image, (a, b, au, av), images)[2:] if size else (0, 0)
            if p.group is not None:                   # a rectangle: a cell's middle of it
                side, group = p.group.side, p.group
                s, t = side.at(sum(p.pts, Vector()) / len(p.pts))
                was = group.uv(s, t)
                frame, mid = [side.at(v) for v in p.pts], (s, t)
            else:                                     # a polygon: its middle
                g_ = p.polygon
                frame = [((v - g_.n * g_.d).dot(g_.a), (v - g_.n * g_.d).dot(g_.b)) for v in p.pts]
                mid = (sum(x for x, _ in frame) / len(frame), sum(y for _, y in frame) / len(frame))
                source = fit(g_.poly, g_.f.uv)          # the face it was cut from, as the source painted it
                was = mapped((offset_of(g_.poly, g_.f.uv, source), source), mid)
            matrix = fit(frame, p.uv)                 # what the piece ships, read from its own corners
            now_uv = mapped((offset_of(frame, p.uv, matrix), matrix), mid)
            got = now_uv
            if size:
                inside = ((now_uv[0] * size - x0 - mx) / iw, b - ((1 - now_uv[1]) * size - y0 - my) / ih)   # in the block
                if not (-au - HAIR <= inside[0] <= a + au + HAIR and -av - HAIR <= inside[1] <= b + av + HAIR):
                    raise AssertionError(f"pack_arena: a face of {image} is mapped outside its block in the atlas")
                if any(abs(o - round(o)) > 1e-9 for o in p.origin):
                    raise AssertionError(f"pack_arena: a face of {image} has its blocks starting off a whole repeat, "
                                         f"and its texture would slide over it")
                got = (inside[0] + p.origin[0], inside[1] + p.origin[1])       # counted from the grid's own corner
            if apart(got[0], was[0]) > READ or apart(got[1], was[1]) > READ:
                raise AssertionError(f"pack_arena: a face of {image} shows another part of the texture than before "
                                     f"({got} against {was})")
            looked += 1
    return looked


CLEAR = 1e-4        # of a texel: a place this close to the edge of one may fall either side of it
OFF = (0.293, 0.617)  # of the way from a face's middle to a corner: the middle and the halfway points lie on texel edges


def reads(board, u, v, tiling):
    """What a viewer reads at a place on an image whose rows are as in its file (the first the top
    one): the colour of the texel, and where in it the place lies, each way from its lower edge.
    `u v` is the place as the source painted it or as a piece ships it, which is Blender's: v
    counted up from the bottom row; the exporter turns it into glTF's, counted down from the top.
    A tiling image is read at the fraction of the place."""
    height, width = board.shape[:2]
    if tiling:
        u, v = u % 1.0, v % 1.0
    x, y = min(int(u * width), width - 1), min(int((1 - v) * height), height - 1)
    return tuple(int(c) for c in board[y, x]), (u * width - x, (1 - v) * height - y)


def viewed(shipped, images, png):
    """Every textured face as a viewer reads it from the files: the texel under its middle (or
    under a point halfway to a corner, if the middle lies on the edge of a texel) in the atlas
    PNG's own bytes, against the texel under the same place in the texture file's own bytes,
    where the source painted the face. Nothing of the atlas's layout is asked, nor the arithmetic
    that mapped the face: a test that shares them with the mapping could not see it go wrong.
    Returns how many faces were read."""
    atlas, files = decode(png.read_bytes()), {image: decode(Path(path).read_bytes()) for image, (path, _, _) in images.items()}
    read = 0
    for pieces in shipped.values():
        for p in pieces:
            if p.style[0] != "tex":
                continue
            if p.group is not None:
                side, group = p.group.side, p.group
                frame, painted = [side.at(v) for v in p.pts], (lambda q: group.uv(*q))
            else:
                g_ = p.polygon
                frame = [((v - g_.n * g_.d).dot(g_.a), (v - g_.n * g_.d).dot(g_.b)) for v in p.pts]
                source = fit(g_.poly, g_.f.uv)
                amap = (offset_of(g_.poly, g_.f.uv, source), source)
                painted = lambda q: mapped(amap, q)  # noqa: E731
            corners = len(frame)
            spots = [[1 / corners] * corners] + [[(1 - t) / corners + (t if i == k else 0.0) for k in range(corners)]
                                                  for t in OFF for i in range(corners)]     # off the lines of the texel grid
            for weights in spots:
                q = (sum(w * c[0] for w, c in zip(weights, frame)), sum(w * c[1] for w, c in zip(weights, frame)))
                now = (sum(w * c[0] for w, c in zip(weights, p.uv)), sum(w * c[1] for w, c in zip(weights, p.uv)))
                (was, a), (ships, b) = reads(files[p.style[1]], *painted(q), tiling=True), reads(atlas, *now, tiling=False)
                if min(min(c, 1 - c) for c in a + b) < CLEAR:      # on the edge of a texel, in either image
                    continue
                read += 1
                if was != ships:
                    raise AssertionError(f"pack_arena: a face of {p.style[1]} would show {ships} where the source shows {was} "
                                         f"(read from the files as a viewer reads them)")
                break
    return read


def check_atlas(png, blocks, placed, images, size):
    """The atlas read back from its file: every texel of every block, gutters too, against the
    texture it came from. Returns how many were compared."""
    px = bpy.data.images.load(str(png), check_existing=False).pixels[:]
    compared = 0
    for image, block in blocks.items():
        raw, iw, ih = texels(image, images)
        w, h, mx, my = extent(image, block, images)
        x0, y0 = placed[image]
        for y in range(h):
            for x in range(w):
                at = ((size - 1 - (y0 + y)) * size + x0 + x) * 4
                src = (((y - my) % ih) * iw + (x - mx) % iw) * 3
                if bytes(round(px[at + c] * 255) for c in range(3)) != raw[src:src + 3]:
                    raise AssertionError(f"pack_arena: texel {x},{y} of {image}'s block changed in the atlas")
                compared += 1
    return compared


def tjunctions(doc, body):
    """Edges of the GLB, over every triangle of it where its node puts it (so a door's corner is
    asked of the wall's edge as much as of its own mesh's: a GPU draws them together), that one
    triangle holds whole while another holds part of them: a corner of the latter lies on the
    edge, and that is what cracks open along. Read from the file's own numbers, 32 bits each,
    and asked of the file and not of the pieces, so that the mending is not its own witness.
    A corner is set on the grid of a hundredth of a millimetre, which takes up the rounding of
    a corner carried through its node's frame."""
    code = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}     # glTF's component types

    def held(i):
        """An accessor's numbers: each corner of a POSITION, or the indices one by one."""
        a, v = doc["accessors"][i], doc["bufferViews"][doc["accessors"][i]["bufferView"]]
        start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        if a["type"] == "VEC3":
            return [struct.unpack_from("<3f", body, start + k * v.get("byteStride", 12)) for k in range(a["count"])]
        return struct.unpack_from("<" + code[a["componentType"]] * a["count"], body, start)

    def frame(n):
        """A node's own place in its parent's: glTF's sixteen numbers, down each column."""
        (x, y, z, w), (a, b, c), (i, j, k) = (n.get("rotation", (0, 0, 0, 1)), n.get("scale", (1, 1, 1)),
                                              n.get("translation", (0, 0, 0)))
        return list(n["matrix"]) if "matrix" in n else [
            a * (1 - 2 * (y * y + z * z)), b * 2 * (x * y - z * w), c * 2 * (x * z + y * w), 0,
            a * 2 * (x * y + z * w), b * (1 - 2 * (x * x + z * z)), c * 2 * (y * z - x * w), 0,
            a * 2 * (x * z - y * w), b * 2 * (y * z + x * w), c * (1 - 2 * (x * x + y * y)), 0,
            i, j, k, 1]

    def after(outer, inner):
        """`outer` then `inner`: where the one puts what the other has put."""
        return [sum(outer[4 * k + r] * inner[4 * c + k] for k in range(4)) for c in range(4) for r in range(4)]

    triangles, places = [], set()

    def walk(i, m):
        n = doc["nodes"][i]
        m = after(m, frame(n))
        if "mesh" in n:
            for pr in doc["meshes"][n["mesh"]]["primitives"]:
                here = [tuple(round(sum(m[4 * k + r] * q[k] for k in range(3)) + m[12 + r], 5) for r in range(3))
                        for q in held(pr["attributes"]["POSITION"])]
                at = [here[j] for j in held(pr["indices"])] if "indices" in pr else here
                triangles.extend(at[k:k + 3] for k in range(0, len(at), 3))
                places.update(here)
        for c in n.get("children", []):
            walk(c, m)

    for i in doc["scenes"][doc.get("scene", 0)]["nodes"]:
        walk(i, (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))
    by = sorted(places)                              # every corner of the file once, in order
    number = {q: i for i, q in enumerate(by)}
    index = [number[q] for t in triangles for q in t]
    edges = set()
    for k in range(0, len(index), 3):
        t = index[k:k + 3]
        for x, y in ((0, 1), (1, 2), (2, 0)):
            edges.add((min(t[x], t[y]), max(t[x], t[y])))
    _, lines = corners_of(by)
    found = 0
    for a, b in edges:                      # a face's whole edge, where another face holds part of it
        on = [q for q, _ in on_edges(by[a], by[b], by, lines)
              if (min(number[q], a), max(number[q], a)) in edges or (min(number[q], b), max(number[q], b)) in edges]
        if on:
            found += 1
            if found <= 5:
                print(f"   from {by[a]} to {by[b]}, a corner of another face at {on}")

    return found


def check_glb(path, png, shipped, images, size, mended):
    """The GLB as bytes: its image(s) are the PNG(s) beside the source, no material is
    double-sided, the lamps are not required, the triangles it holds are the triangles
    shipped, and (with `mended`) no corner of any triangle lies on an edge another holds
    whole, asked over the whole file. `shipped` is the triangles' count."""
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    doc, body = json.loads(data[20:20 + length]), data[28 + length:]
    held = [body[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
            for v in (doc["bufferViews"][i["bufferView"]] for i in doc.get("images", []))]
    wanted = [png.read_bytes()] if size else [Path(image[0]).read_bytes() for image in images.values()]
    if sorted(held) != sorted(wanted):
        raise AssertionError(f"pack_arena: the GLB holds {len(held)} images that are not the {len(wanted)} the source has, "
                             "byte for byte")
    if any(m.get("doubleSided") for m in doc.get("materials", [])):
        raise AssertionError("pack_arena: a material in the GLB is double-sided")
    if "KHR_lights_punctual" in doc.get("extensionsRequired", []):
        raise AssertionError("pack_arena: the GLB requires lamps")
    triangles = sum(doc["accessors"][pr["indices"]]["count"] // 3 for n in doc["nodes"] if "mesh" in n
                    for pr in doc["meshes"][n["mesh"]]["primitives"])
    draws = sum(len(doc["meshes"][n["mesh"]]["primitives"]) for n in doc["nodes"] if "mesh" in n)
    if triangles != shipped:
        raise AssertionError(f"pack_arena: the GLB holds {triangles} triangles, the packer shipped {shipped}")
    seams = tjunctions(doc, body)
    if seams and mended:
        raise AssertionError(f"pack_arena: {seams} edges of the GLB have a corner of another face on them: cracks can open there")
    return triangles, draws, sum(1 for n in doc["nodes"] if "mesh" in n), len(doc.get("materials", [])), len(data), seams


def main():
    parser = argparse.ArgumentParser(description="Pack the dressed arena for a real-time viewer.")
    parser.add_argument("--atlas", type=int, default=ATLAS,
                        help="the atlas's side in texels (%(default)s); 0 keeps the arena's textures as they are")
    parser.add_argument("--watertight", action="store_true",
                        help="split every edge where a corner of another face lies on it, so that no crack of a pixel "
                             "can open there (a T-junction): watertight, at a triangle a corner")
    parser.add_argument("--out", default=str(ROOT / "models" / "liminal" / "arena_liminal_packed.glb"),
                        help="the GLB to write; an atlas is written beside it, as a PNG")
    args = parser.parse_args(sys.argv[1:])
    out, size, mended = Path(args.out).resolve(), args.atlas, args.watertight
    if 0 < size < 128:
        raise SystemExit("pack_arena: --atlas is an atlas's side in texels, 512 or more; or 0 for no atlas")
    png = out.with_suffix(".png")

    liminal.build()
    faces, solids, doors = load()
    sides, polygons, planes, images = layout(faces)
    removed, cut, fought, duplicates = hide(faces, solids, sides, polygons, planes)
    if duplicates:
        raise SystemExit(f"pack_arena: two faces of different parts lie on one side of one plane: {duplicates[:5]}")
    groups, seen = merge(faces, sides, polygons, removed, cut)
    before = sum(len(f.pts) - 2 for f in faces)
    names = {f.ob for f in faces}
    draws_before = sum(len({f.style for f in faces if f.ob == name}) for name in names)
    tiles = {(f.style[1], f.style[2]): f.mat for f in faces if f.style[0] == "tex"}

    def count(side):
        if not side:
            blocks, placed = {k: (math.inf, math.inf, 0.0, 0.0) for k in images}, None
        else:
            blocks, placed, _ = allocate(groups, polygons, images, side)
            if blocks is None:
                return None
            placed = placed or {k: (0, 0) for k in blocks}
        pieces = [piece for part in ship(groups, polygons, blocks, placed, images, side, lambda style: None).values() for piece in part]
        return len(mend(pieces, False)[3]), len(mend(pieces, True)[3])     # the triangles with the T-junctions left, and watertight

    whole = sum(faces[i].area for i in removed)
    part, enclosed = sum(g.lost for g in polygons), 0.0
    for side in sides.values():
        for c, fid in enumerate(side.owner):
            if fid < 0 or fid in removed:
                continue
            a = (side.xs[c % side.nx + 1] - side.xs[c % side.nx]) * (side.ts[c // side.nx + 1] - side.ts[c // side.nx])
            if (side.index, c) in cut:
                part += a
            elif side.cov[c]:
                enclosed += a
    print(f"the dressed arena: {len(faces)} faces, {before:,} triangles, {draws_before} draws, {len(names)} mesh nodes")
    print(f"out of sight: {len(removed)} faces whole ({whole:.0f} m2); face to face, the smaller gave way over {part:.1f} m2; "
          f"kept though enclosed, to spare a cut in a bigger face: {enclosed:.0f} m2")
    print("triangles by the atlas's side, no texel resampled:", "   ".join(
        f"{s or 'textures apart'} {n:,} ({(n - before) / before:+.0%}; {left:,} watertight)"
        for s in SIZES for n, left in (count(s),) if n is not None))

    out.parent.mkdir(parents=True, exist_ok=True)
    if size:
        blocks, placed, _ = allocate(groups, polygons, images, size)
        if blocks is None:
            raise SystemExit(f"pack_arena: the {len(images)} textures do not fit, one repeat each, in {size} by {size}")
        write_png(png, size, compose(blocks, placed, images, size))
        atlas = {glow: atlas_material(png, glow) for glow in sorted({glow for _, glow in tiles})}
        paint = lambda style: atlas[style[2]] if style[0] == "tex" else style[1]       # noqa: E731
    else:
        blocks, placed = {k: (math.inf, math.inf, 0.0, 0.0) for k in images}, None
        png.unlink(missing_ok=True)                          # no atlas of an earlier run left beside the file
        paint = lambda style: tiles[(style[1], style[2])] if style[0] == "tex" else style[1]   # noqa: E731
    for m in bpy.data.materials:
        m.use_backface_culling = True
    shipped = ship(groups, polygons, blocks, placed, images, size, paint)
    looked = prove(faces, solids, shipped, seen, blocks, placed, images, size)
    compared = check_atlas(png, blocks, placed, images, size) if size else 0
    read = viewed(shipped, images, png) if size else 0

    every = [piece for pieces in shipped.values() for piece in pieces]
    split, added, corner_of, cut = mend(every, mended)       # all at once: a door's corner lies on the wall's edge as any other does
    laid = {}
    for door, pieces in shipped.items():                     # each mesh keeps its own corners, each by its number
        by, index, mine, seen = [], {}, [], []
        for k, t in cut:
            if every[k].door != door:
                continue
            if not seen or seen[-1] is not every[k]:
                seen.append(every[k])
            corners = []
            for v, uv in t:
                if corner_of[v] not in index:
                    index[corner_of[v]] = len(by)
                    by.append(corner_of[v])
                corners.append((index[corner_of[v]], uv))
            mine.append((len(seen) - 1, corners))
        assert all(a is b for a, b in zip(seen, pieces)), "pack_arena: a piece's triangles are not together"
        laid[door] = (seen, by, mine)
    shipped_tris = sum(len(laid[d][2]) for d in laid)
    for ob in [o for o in bpy.data.objects if o.type == 'MESH' and o.name not in doors]:
        bpy.data.objects.remove(ob, do_unlink=True)         # the parts, their faces one mesh now; and the design plates
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)
    static = bpy.data.objects.new("geometry", mesh_of("geometry", Matrix.Identity(4), laid[None]))
    bpy.context.scene.collection.objects.link(static)
    static.parent = bpy.data.objects["Structure"]
    for name, frame in doors.items():                       # a door keeps its name, its hinge for a pivot, its own mesh
        ob = bpy.data.objects[name]
        old = ob.data
        ob.data = mesh_of(name, frame, laid[name])
        if old.users == 0:
            bpy.data.meshes.remove(old)
        ob.data.name = name
    for name in ("Cover", "Markers"):                       # their parts are merged now, or not shipped
        ob = bpy.data.objects.get(name)
        if ob and not ob.children:
            bpy.data.objects.remove(ob, do_unlink=True)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLB', export_image_format='AUTO', export_texcoords=True,
                              export_normals=True, export_materials='EXPORT', export_yup=True, export_extras=True,
                              export_lights=True, export_copyright=liminal.CREDIT)
    liminal.polish(out)
    triangles, draws, nodes, materials, byte_count, seams = check_glb(out, png, shipped_tris, images, size, mended)
    if size:
        print(f"atlas {size} x {size}, {png.stat().st_size:,} bytes as PNG. Blocks, in repeats of their texture:")
        for image in sorted(blocks, key=lambda k: -extent(k, blocks[k], images)[0] * extent(k, blocks[k], images)[1]):
            w, h = extent(image, blocks[image], images)[:2]
            a, b, au, av = blocks[image]
            ways = ("u" if au else "") + (" and " if au and av else "") + ("v" if av else "")
            apron = f", an apron of half a repeat along {ways}" if ways else ""
            print(f"   {image.removesuffix('.png'):26s} {a} x {b} repeats   {w} x {h} texels "
                  f"at {placed[image][0]},{placed[image][1]}{apron}")
    print(f"OK - {out.name}: {triangles:,} triangles against {before:,} ({(triangles - before) / before:+.1%}); "
          f"{draws} draws against {draws_before}; {nodes} mesh nodes against {len(names)}; {materials} materials; "
          f"{byte_count:,} bytes" + (f" with a {size} by {size} atlas" if size else f", the arena's own {len(tiles)} textures"))
    print(f"watertight: {split:,} edges split where a corner of another face lay on them, {added:,} corners added; "
          "the file itself, every triangle where its node puts it, has no T-junction left" if mended else
          f"{seams} edges have the corner of another face on them (T-junctions, asked over every triangle of the file "
          "where its node puts it; the source has such places too): --watertight splits each, at a triangle a corner")
    print("proved: the area of each finish is what it was; no two faces lie in one plane; every face points out of its "
          f"solid; {looked:,} faces sample the texel they sampled before"
          + (f"; {compared:,} atlas texels equal their files; {read:,} faces, read from the atlas file as a viewer reads "
             f"it, show the texel their texture's file holds" if size
             else "; the images in the file are the source's own"))


if __name__ == "__main__":
    main()
