"""Do two GLBs show the same surfaces? Cast the same rays at both and compare what they hit.

    python tools/same_surfaces.py <source.glb> <packed.glb> [--atlas <image name>] [--step 1]

A tool that merges, cuts and re-maps faces can be wrong in ways its own counts cannot see: a
texture shifted by a block, a slope laid on the wrong plane, a door walled over. So the two
files are asked from the outside, as cameras would, and each ray's first stop in one is set
against its first stop in the other: where (to a fifth of a millimetre), what is painted there
(the colour of the texel under the hit: the packed file's one atlas, named by `--atlas`, is read
where its place on it is; a tiling texture, from the fraction of its place) and how many times
over its own colour it shines. Rays come from two kinds of place:

* a grid of points in the air over the level (those inside a solid are left out), each in the
  26 directions of `spread`: what a player walking the level would meet;
* right in front of every face of each file, straight at it, at its centre and halfway to each
  corner, from 5 cm and from 60 cm away: so that no face goes unasked for want of a ray.

The exit is 1 if any ray would see two different things. Two things the first file may show are
counted but not held against the second, both being its own faults: a face drawn at the depth
of the one in front of it (what z-fighting is to a ray) and a face seen from behind. The first
file's design plates (`Markers`) are skipped, as a packed file does not ship them. Its objects,
each one closed solid, also say where the matter is: no ray starts inside it.

Texels are not asked of Blender: each file's own bytes are decoded here, a PNG's first row being
its top one, as glTF reads it. Blender's importer turns that v (0 at the top) into its own (0 at
the bottom); `Model.describe` sets the two right. A packer that reads its textures one way and
writes them the other passes every test of its own; this is the test that sees it.
"""
import argparse
import itertools
import json
import logging
import math
import struct
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import bpy
import numpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

SPOT = 0.37          # metres off the whole numbers, so no ray runs along a wall or an edge
PLACE = 2e-4         # metres: how far apart two hits may be and still be one place
EDGE = 1e-3          # of a texel: a hit this close to its edge may fall either side of it
INSIDE = 1e-4        # metres: a hit this close to the edge of a face is on the edge, where faces meet
HEIGHTS = (0.3, 1.2, 2.6, 4.4, 8.5)
RANGE = (0.05, 0.6)  # metres: how far in front of a face the rays at it start
SKIP = ("Markers",)   # nodes of the first file that are design, not level


def spread(count):
    """`count` ways to look, evenly over the sphere, none along a line a wall, a floor or an edge
    lies on. The neighbours of a grid cell would not do: from a regular grid their diagonals all
    pass through the corners where walls meet, where nothing can be compared."""
    out, turn = [], math.pi * (3 - math.sqrt(5))
    for i in range(count):
        z, a = 1 - 2 * (i + 0.5) / count, i * turn + 0.37
        out.append(Vector((math.sqrt(1 - z * z) * math.cos(a), math.sqrt(1 - z * z) * math.sin(a), z)))
    return out


DIRECTIONS = spread(26)
WAYS = ((0.2137, 0.5713, 0.7941), (-0.8171, 0.3211, 0.4769), (0.4709, -0.7413, -0.4783))   # to count crossings by


def under(ob, names):
    """Is `ob`, or the object it hangs under, and so on up, named in `names`?"""
    while ob:
        if ob.name in names:
            return True
        ob = ob.parent
    return False


def centre(corners):
    return tuple(sum(c[k] for c in corners) / len(corners) for k in range(3))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def unit_cross(a, b, c):
    """The way the triangle `a b c` looks by its winding, as a unit vector; None if it has no
    area. Worked in doubles."""
    n = cross(tuple(b[k] - a[k] for k in range(3)), tuple(c[k] - a[k] for k in range(3)))
    length = math.sqrt(dot(n, n))
    return tuple(x / length for x in n) if length else None


def png(data):
    """A PNG's texels the way any decoder reads the file: its first row first, which in glTF is
    the top row. RGB, whatever the file's own layout (a palette, grey, transparency)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    chunks, at = defaultdict(bytes), 8
    while at < len(data):
        size, kind = struct.unpack_from(">I4s", data, at)
        chunks[kind] += data[at + 8:at + 8 + size]
        at += size + 12
    width, height, depth, sort, _, _, lace = struct.unpack_from(">IIBBBBB", chunks[b"IHDR"])
    if lace or depth < 8 and sort != 3:
        raise ValueError("an interlaced PNG" if lace else "a PNG of fewer than 8 bits a channel")
    lanes, step = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[sort], max(1, {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[sort] * depth // 8)
    stride = (width * lanes * depth + 7) // 8
    raw, rows, above, at = zlib.decompress(chunks[b"IDAT"]), [], [0] * stride, 0
    for _ in range(height):
        way, line, at = raw[at], list(raw[at + 1:at + 1 + stride]), at + 1 + stride
        if way > 4:
            raise ValueError(f"a PNG row filtered {way}")
        for i in range(stride):                      # each byte is undone against what was decoded before it
            if way == 0:
                break
            left, up = (line[i - step] if i >= step else 0), above[i]
            if way == 1:
                line[i] = (line[i] + left) & 255
            elif way == 2:
                line[i] = (line[i] + up) & 255
            elif way == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            else:
                corner = above[i - step] if i >= step else 0
                a, b, c = abs(up - corner), abs(left - corner), abs(left + up - 2 * corner)
                line[i] = (line[i] + (left if a <= b and a <= c else up if b <= c else corner)) & 255
        rows.append(line)
        above = line
    board = numpy.array(rows, dtype=numpy.uint8)
    if sort == 3:                                    # a palette: each texel is the number of an entry
        if depth < 8:
            bits = numpy.unpackbits(board, axis=1)[:, :width * depth].reshape(height, width, depth).astype(int)
            board = (bits * (2 ** numpy.arange(depth - 1, -1, -1))).sum(axis=2)
        else:
            board = board[:, :width]
        return numpy.frombuffer(chunks[b"PLTE"], dtype=numpy.uint8).reshape(-1, 3)[board]
    board = board.reshape(height, stride // lanes, lanes)[:, :width]
    return numpy.repeat(board, 3, axis=2) if lanes < 3 else board[:, :, :3]


class Pictures:
    """The images a GLB holds, each one's texels decoded from the file's own bytes, kept by the
    name the importer gives it."""

    def __init__(self, path):
        data = path.read_bytes()
        if struct.unpack_from("<4s", data)[0] != b"glTF":
            raise ValueError(f"{path.name} is not a GLB")
        self.doc, binary, at = {}, b"", 12
        while at < len(data):
            size, kind = struct.unpack_from("<II", data, at)
            if kind == 0x4E4F534A:
                self.doc = json.loads(data[at + 8:at + 8 + size])
            elif kind == 0x004E4942:
                binary += data[at + 8:at + 8 + size]
            at += size + 8
        self.files, self.boards = {}, {}
        for image in self.doc.get("images", []):
            if "bufferView" in image:
                view = self.doc["bufferViews"][image["bufferView"]]
                start = view.get("byteOffset", 0)
                self.files[image.get("name", "")] = binary[start:start + view["byteLength"]]

    def board(self, name):
        """The texels of the image the importer calls `name`; None if it is not a PNG."""
        if name not in self.boards:
            if name not in self.files:
                raise ValueError(f"no image named {name!r} in the file; it holds {sorted(self.files)}")
            data = self.files[name]
            self.boards[name] = png(data) if data[:8] == b"\x89PNG\r\n\x1a\n" else None
        return self.boards[name]


def paint_of(mat, pictures, atlas):
    """What faces of material `mat` are painted with: an image and its texels (the top row
    first), or a colour; and how many times over its own colour they shine."""
    bsdf = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None) \
        if mat.node_tree else None
    glow = round(float(bsdf.inputs["Emission Strength"].default_value), 4) if bsdf else 0.0
    base = bsdf.inputs["Base Color"] if bsdf else None
    if base is not None and base.is_linked and base.links[0].from_node.bl_idname == "ShaderNodeTexImage":
        image = base.links[0].from_node.image
        board = pictures.board(image.name)
        if board is None:                            # not a PNG: through Blender, whose first row is the file's last
            width, height = image.size
            pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
            image.pixels.foreach_get(pixels)
            board = (pixels.reshape(height, width, 4)[::-1, :, :3] * 255 + 0.5).astype(numpy.uint8)
        return ("tex", (board.shape[1], board.shape[0]), board, Path(image.name).stem == Path(atlas).stem, glow)
    return ("flat", tuple(round(c, 5) for c in base.default_value) if base is not None else None, glow)


def crossings(triangles, origin, direction):
    """How many of `triangles` (corners in space, an N x 3 x 3 array) the ray from `origin`
    along `direction` crosses: odd means the ray set out inside what they enclose."""
    a, e1, e2 = triangles[:, 0], triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    h = numpy.cross(direction, e2)
    det = numpy.einsum("ij,ij->i", e1, h)
    ok = numpy.abs(det) > 1e-14
    inv = numpy.where(ok, 1.0 / numpy.where(ok, det, 1.0), 0.0)
    s = origin - a
    u = numpy.einsum("ij,ij->i", s, h) * inv
    q = numpy.cross(s, e1)
    v = numpy.einsum("ij,j->i", q, direction) * inv
    far = numpy.einsum("ij,ij->i", e2, q) * inv
    near = (1e-9 < u) & (1e-9 < v) & (u + v < 1 - 1e-9)        # a corner or an edge is nobody's: three ways are asked
    return int((ok & near & (far > 1e-4)).sum())


class Model:
    """A GLB as something to cast rays at: every mesh, each face of it in place with the places
    of its corners on its texture, what each of its materials paints, a tree to find the first
    face with, and the same triangles on a grid of metre cells to take that stop exactly."""

    def __init__(self, path, atlas):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))
        pictures = Pictures(path)
        self.faces, self.keys, self.paints, self.triangles = {}, [], {}, 0
        self.box, vertices, polygons = [[math.inf] * 3, [-math.inf] * 3], [], []
        self.tris, self.cells, self.solids, self.heights, self.give = [], defaultdict(list), [], [], []
        for ob in bpy.data.objects:
            if ob.type != 'MESH' or under(ob, SKIP):
                continue
            me = ob.data
            me.calc_loop_triangles()
            self.triangles += len(me.loop_triangles)
            uv = [tuple(l.uv) for l in me.uv_layers.active.data] if me.uv_layers.active else [(0.0, 0.0)] * len(me.loops)
            world = [tuple(float(c) for c in ob.matrix_world @ v.co) for v in me.vertices]
            by_face = defaultdict(list)
            own = []
            for t in me.loop_triangles:
                key = (ob.name, t.polygon_index)
                corners = tuple(world[i] for i in t.vertices)
                by_face[t.polygon_index].append(corners + tuple(uv[i] for i in t.loops))
                number = len(self.tris)
                self.tris.append(corners + tuple(uv[i] for i in t.loops) + (key,))
                own.append(corners)
                a, b, c = corners
                e1, e2 = (tuple(q[k] - a[k] for k in range(3)) for q in (b, c))
                n = cross(e1, e2)
                twice = math.sqrt(dot(n, n))                                # twice the triangle's area
                heights = [twice / d if twice else math.inf for d in (math.dist(b, c), math.dist(a, c), math.dist(a, b))]
                self.heights.append(heights)                                # of each corner over the edge opposite it
                self.give.append([PLACE / h for h in heights])             # as a share of that height: PLACE in metres
                low = [min(p[k] for p in corners) - PLACE for k in range(3)]
                high = [max(p[k] for p in corners) + PLACE for k in range(3)]
                for cell in itertools.product(*(range(math.floor(low[k]), math.floor(high[k]) + 1) for k in range(3))):
                    self.cells[cell].append(number)
            if own:
                self.solids.append(numpy.array(own))
            paints = [self.paints.setdefault(m.name, paint_of(m, pictures, atlas)) if m else ("flat", None, 0.0)
                      for m in me.materials]
            base = len(vertices)
            vertices += world
            for i, poly in enumerate(me.polygons):
                corners = [world[v] for v in poly.vertices]
                self.faces[(ob.name, i)] = (by_face[i], centre(corners), paints[poly.material_index], corners)
                polygons.append(tuple(base + v for v in poly.vertices))
                self.keys.append((ob.name, i))
                for c in corners:
                    for k in range(3):
                        self.box[0][k], self.box[1][k] = min(self.box[0][k], c[k]), max(self.box[1][k], c[k])
        self.tree = BVHTree.FromPolygons(vertices, polygons)
        if self.solids:
            self.low = numpy.array([t.reshape(-1, 3).min(axis=0) for t in self.solids])
            self.high = numpy.array([t.reshape(-1, 3).max(axis=0) for t in self.solids])

    def inside(self, point):
        """Is the point in matter: inside some one of the file's objects, each a closed solid?
        The crossings are counted against each object's own triangles alone, so that two solids
        set into one another (a ramp's foot a hair under the ground) do not cancel out."""
        p = numpy.array(tuple(point))
        for i in numpy.nonzero(((p >= self.low) & (p <= self.high)).all(axis=1))[0]:
            if sum(crossings(self.solids[i], p, way) % 2 for way in WAYS) >= 2:
                return True
        return False

    def crossing(self, number, origin, direction):
        """Where the ray meets the triangle, or (None, None): how far along it, and the weights
        of the place in the triangle. Worked in doubles. A place less than PLACE outside it
        counts as on it, at its edge: in metres, not as a share of the triangle, which would
        let the same hair's breadth past a long face and not past a short one."""
        a, b, c = self.tris[number][:3]
        e1, e2 = (tuple(q[k] - a[k] for k in range(3)) for q in (b, c))
        h = cross(direction, e2)
        det = dot(e1, h)
        if abs(det) < 1e-12:
            return None, None
        g = self.give[number]
        s = tuple(origin[k] - a[k] for k in range(3))
        u = dot(s, h) / det
        if u < -g[1] or u > 1 + g[1]:
            return None, None
        q = cross(s, e1)
        v = dot(direction, q) / det
        if v < -g[2] or u + v > 1 + g[0]:
            return None, None
        far = dot(e2, q) / det
        return far, (1.0 - u - v, u, v)

    def inset(self, number, weights):
        """How far in from the nearest edge of the triangle a place in it lies, in metres."""
        return min(w * h for w, h in zip(weights, self.heights[number]))

    def describe(self, place, weights, number, direction):
        """One face met: where, what it is painted with, whether its front faces the ray, where
        in its texel the hit lies, which face, which way its front looks, and how far in from
        the face's edge the hit lies, in metres."""
        a, b, c, ua, ub, uc, key = self.tris[number]
        paint = self.faces[key][2]
        n = unit_cross(a, b, c)
        front = dot(n, direction) < 0.0
        inset = self.inset(number, weights)
        if paint[0] == "flat":
            return (place, paint, front, None, key, n, inset)
        (_, (width, height), board, is_atlas, glow) = paint
        u = weights[0] * ua[0] + weights[1] * ub[0] + weights[2] * uc[0]
        v = weights[0] * ua[1] + weights[1] * ub[1] + weights[2] * uc[1]
        if not is_atlas:
            u, v = u - math.floor(u), v - math.floor(v)
        x, y = min(int(u * width), width - 1), min(int(v * height), height - 1)
        rgb = tuple(int(channel) for channel in board[height - 1 - y, x, :3])   # v was turned upside down on the way in
        return (place, ("tex", rgb, glow), front, (u * width - x, v * height - y, x, y), key, n, inset)

    def first(self, origin, direction):
        """Every face the ray meets at its first stop, the nearest first. The tree finds the
        stop to a 32-bit float; the faces met there are then taken exactly, in doubles, so that
        one lying in the same plane as another is met with it -- and one the ray sets out on,
        which the tree may step over."""
        origin, direction = tuple(origin), tuple(direction)
        place, _, _, _ = self.tree.ray_cast(origin, direction)
        near = math.dist(place, origin) if place is not None else None
        at = tuple(math.floor(c) for c in (place if place is not None else origin))
        met = {}
        for cell in itertools.product(*(range(k - 1, k + 2) for k in at)):
            for number in self.cells.get(cell, ()):
                far, weights = self.crossing(number, origin, direction)
                if far is None or far < -PLACE or (near is not None and far > near + PLACE):
                    continue
                near = far if near is None else min(near, far)
                key = self.tris[number][6]
                if key not in met or (far, -self.inset(number, weights)) < (met[key][0], -self.inset(met[key][2], met[key][1])):
                    met[key] = (far, weights, number)
        return [self.describe(tuple(origin[k] + direction[k] * far for k in range(3)), weights, number, direction)
                for key, (far, weights, number) in sorted(met.items(), key=lambda item: (item[1][0], -self.inset(item[1][2], item[1][1])))
                if abs(far - near) <= PLACE]

    def face_rays(self):
        """Rays at every face, straight on: (where from, which way) for its centre and, on a
        face of four corners or more, a point halfway to each corner, from each distance in
        RANGE. A face of no area has none."""
        for key, (triangles, middle, paint, corners) in self.faces.items():
            n = unit_cross(*triangles[0][:3])
            if n is None:
                continue
            spots = [middle] + [[(middle[k] + c[k]) / 2 for k in range(3)] for c in corners] if len(corners) > 3 \
                else [middle]
            for spot in spots:
                for far in RANGE:
                    yield Vector([spot[k] + n[k] * far for k in range(3)]), Vector([-c for c in n])


def origins(model, step):
    """Points in the air over the model's box, `step` apart, that are in no solid."""
    (x0, y0, _), (x1, y1, _) = model.box
    out = []
    for i in range(int((x1 - x0 - SPOT) / step) + 1):
        for j in range(int((y1 - y0 - SPOT) / step) + 1):
            for z in HEIGHTS:
                at = Vector((x0 + SPOT + i * step, y0 + SPOT + j * step, z))
                if not model.inside(at):
                    out.append(at)
    return out


def on_edge(a, b):
    """Do both hits lie at the same edge of the texel grid, so near it that a rounding may put
    either on its other side?"""
    pa, pb = a[3], b[3]
    if pa is None or pb is None:
        return False
    return any(min(pa[i], 1 - pa[i]) < EDGE and min(pb[i], 1 - pb[i]) < EDGE and (pa[i] < 0.5) != (pb[i] < 0.5)
               for i in (0, 1))


def apart(a, b):
    """How far apart two faces met are, in doubles."""
    return math.dist(a[0], b[0])


def alike(a, b):
    """Do two faces met show the same thing: one place, one paint?"""
    return apart(a, b) <= PLACE and (a[1] == b[1] or (a[1][0] == b[1][0] == "tex" and on_edge(a, b)))


def differ(a, b):
    """Why the faces met at one stop in the first file (`a`) and in the second (`b`) are not the
    same to look at, or None."""
    for p in b:
        if not any(alike(q, p) for q in a):
            q = next((q for q in a if apart(q, p) <= PLACE), None)
            if q is None:
                return "only in the second"
            if q[1][0] != p[1][0]:
                return "another paint"
            return "another texel" if q[1][1] != p[1][1] else "another shine" if q[1][2] != p[1][2] else None
    for q in a:
        if q[2] and not any(alike(q, p) for p in b):
            return "only in the first"
    if any(not p[2] and p[6] > INSIDE and not any(alike(q, p) and not q[2] for q in a) for p in b):
        return "seen from behind in the second"
    for p, q in itertools.combinations(b, 2):           # along one edge two faces meet; in one plane is a fault
        if abs(dot(p[5], q[5])) > 0.999 and p[6] > INSIDE and q[6] > INSIDE:
            return "two faces at one place in the second"
    return None


def show(stop):
    return [(f[4][0], f[4][1], tuple(round(c, 3) for c in f[0]), f[1][:2], f[2],
             None if f[3] is None else tuple(round(c, 4) for c in f[3])) for f in stop]


class Report:
    """What a set of rays found: how many agree, how many differ and how, with a few shown."""

    def __init__(self):
        self.same, self.apart, self.noted, self.inside, self.edges, self.shown = 0, Counter(), Counter(), 0, 0, 0

    def look(self, first, second, origin, direction, skip_inside=False):
        """Set the first stop of one ray in both files against each other."""
        if skip_inside and first.inside(origin):
            self.inside += 1
            return
        a, b = first.first(origin, direction), second.first(origin, direction)
        if (a and all(f[6] <= INSIDE for f in a)) or (b and all(f[6] <= INSIDE for f in b)):
            self.edges += 1                  # it ran along an edge or into a corner, where nothing is to compare
            return
        kind = differ(a, b)
        if kind is None:
            self.same += 1
            if any(not q[2] and q[6] > INSIDE for q in a) and any(q[2] for q in a):
                self.noted["met a face drawn at the depth of the one in front, in the first file"] += 1
            elif a and b and not a[0][2] and b[0][2]:
                self.noted["met a face from behind, in the first file"] += 1
            return
        self.apart[kind] += 1
        if self.shown < 8:
            self.shown += 1
            print(f"  {kind}: from {tuple(round(c, 2) for c in origin)} along {tuple(round(c, 2) for c in direction)}: "
                  f"{show(a)} against {show(b)}")

    def line(self, title):
        return (f"{title}: {self.same + sum(self.apart.values()):,} rays, {self.same:,} agree"
                + (f", {sum(self.apart.values()):,} differ ("
                   + ", ".join(f"{n} {k}" for k, n in self.apart.most_common()) + ")" if self.apart else "")
                + (f"; {self.inside:,} start inside matter and are left out" if self.inside else "")
                + (f"; {self.edges:,} meet nothing but edges and are left out" if self.edges else "")
                + ("; " + "; ".join(f"{n:,} {k}" for k, n in self.noted.most_common()) if self.noted else ""))


def main():
    parser = argparse.ArgumentParser(description="Compare what rays hit in two GLBs.")
    parser.add_argument("first")
    parser.add_argument("second")
    parser.add_argument("--atlas", default="", help="the name of the second file's atlas image, if it has one")
    parser.add_argument("--step", type=float, default=2.0, help="metres between the points of the grid")
    args = parser.parse_args(sys.argv[1:])
    logging.disable(logging.INFO)      # the importer names every node it creates

    first, second = Model(Path(args.first).resolve(), ""), Model(Path(args.second).resolve(), args.atlas)
    for name, model in (("first file", first), ("second file", second)):
        kinds = Counter(p[0] for p in model.paints.values())
        print(f"{name}: {len(model.paints)} materials ({kinds['tex']} on images, {kinds['flat']} of a colour alone), "
              f"{len(model.faces):,} faces, {model.triangles:,} triangles")

    reports = []
    points = origins(first, args.step)
    grid = Report()
    for at in points:
        for d in DIRECTIONS:
            grid.look(first, second, at, d)
    reports.append(grid)
    print(grid.line(f"from {len(points):,} points in the air, {args.step:g} m apart"))

    for title, model in (("at the faces of the first file", first), ("at the faces of the second file", second)):
        faces = Report()
        for origin, direction in model.face_rays():
            faces.look(first, second, origin, direction, skip_inside=True)
        reports.append(faces)
        print(faces.line(title))

    if any(report.apart for report in reports):
        print("NOT THE SAME")
        sys.exit(1)
    print("OK - the two files show the same surfaces")


if __name__ == "__main__":
    main()
