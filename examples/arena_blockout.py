"""A two-site arena as a plain blockout: three heights, rooms with windows, and a counter
for every vantage point.

An original layout that borrows only the genre's grammar (two sites, a mid, connectors,
spawns). Everything is a box except four ramps; every number is metres from the arena's
centre, x east and y north.

Run, it writes models/arena_blockout.glb. `arena_liminal` imports `build` and dresses the
same parts, untouched, in textures.
"""
from pathlib import Path

import sand3d as s3d

M = 1000                          # millimetres in a metre
W, D = 80, 56                     # the arena, wall to wall
H, ROOF = 7, 5.5                  # the outer walls, and the roofs, stand this tall
EAVE = ROOF - 0.5                 # the walls rise to the eave; a roof slab rests on them
DOOR, SILL, HEAD = 2.2, 1.0, 2.2  # a door is floor to 2.2; a window keeps a 1 m sill
LOW, TALL = 1, 2                  # crates: a low one can be jumped, a tall one climbed off

DOORWAYS = []                     # filled by a build: every opening cut from a floor, as cut


def slab(name, x0, y0, x1, y1, z0, z1):
    """One axis-aligned box from (x0, y0, z0) to (x1, y1, z1); corners either way round."""
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    z0, z1 = sorted((z0, z1))
    return s3d.at(s3d.box(name, (x1 - x0) * M, (y1 - y0) * M, (z1 - z0) * M),
                  x=(x0 + x1) / 2 * M, y=(y0 + y1) / 2 * M, z=z0 * M)


def wall(name, x0, y0, x1, y1, z0, z1, openings=()):
    """A wall whose long side is its run: the boxes it is cut into. An opening is
    (from, to, bottom, top) along the run -- a door, or a window that keeps its sill."""
    along_x = abs(x1 - x0) > abs(y1 - y0)
    lo, hi = sorted((x0, x1) if along_x else (y0, y1))
    spans, cursor = [], lo
    for a, b, bottom, top in sorted(openings):
        a, b = sorted((a, b))
        if bottom <= z0:                 # cut from the floor: a doorway, for `arena_liminal`
            DOORWAYS.append(dict(name=name, along_x=along_x, a=a, b=b, floor=z0, top=top,
                                 x0=x0, y0=y0, x1=x1, y1=y1))
        if a > cursor:
            spans.append((cursor, a, z0, z1))
        if bottom > z0:
            spans.append((a, b, z0, bottom))
        if top < z1:
            spans.append((a, b, top, z1))
        cursor = b
    spans.append((cursor, hi, z0, z1))
    return [slab(f"{name}_{i}", a, y0, b, y1, p, q) if along_x else slab(f"{name}_{i}", x0, a, x1, b, p, q)
            for i, (a, b, p, q) in enumerate(s for s in spans if s[1] > s[0])]


def ramp(name, x0, y0, x1, y1, rise, toward):
    """A slope over the footprint, climbing `rise` toward n, s, e or w."""
    (x0, x1), (y0, y1) = sorted((x0, x1)), sorted((y0, y1))
    reach, width = (y1 - y0, x1 - x0) if toward in "ns" else (x1 - x0, y1 - y0)
    ob = s3d.extrude(name, s3d.poly([(0, 0), (reach * M, 0), (reach * M, rise * M)]), width * M, both=True)
    return [s3d.at(ob, rx=90, rz={"e": 0, "n": 90, "w": 180, "s": -90}[toward],
                   x=(x0 if toward == "e" else x1 if toward == "w" else (x0 + x1) / 2) * M,
                   y=(y0 if toward == "n" else y1 if toward == "s" else (y0 + y1) / 2) * M)]


def stairs(name, x0, y0, x1, y1, rise, steps, low):
    """`steps` boxes climbing `rise` away from the `low` side (n, s, e or w)."""
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    t = ((x1 - x0) if low in "ew" else (y1 - y0)) / steps
    out = []
    for i in range(1, steps + 1):
        if low == "e":
            box = (x1 - t * i, y0, x1 - t * (i - 1), y1)
        elif low == "w":
            box = (x0 + t * (i - 1), y0, x0 + t * i, y1)
        elif low == "n":
            box = (x0, y1 - t * i, x1, y1 - t * (i - 1))
        else:
            box = (x0, y0 + t * (i - 1), x1, y0 + t * i)
        out.append(slab(f"{name}_{i}", *box, 0, rise * i / steps))
    return out


def wing(s, tag, extra=()):
    """The slim building of two rooms beside a passage: s = 1 beside the west one (Boiler,
    Office), s = -1 its twin beside the east (Lobby, Gatehouse). It stops 4 m short of the
    lane so that the lookout, beside the ramp, keeps the ramp in view. `extra` windows look
    out of the twin's far wall, over site B."""
    def part(name, x0, y0, x1, y1, bottom, top, openings=()):
        cuts = [(min(s * a, s * b), max(s * a, s * b), p, q) for a, b, p, q in openings]
        return wall(f"{tag}_{name}", s * x0, s * y0, s * x1, s * y1, bottom, top, cuts)
    return (part("passage", -26, 10, -10, 11, 0, EAVE, [(-24, -22, 0, DOOR), (-14, -12, SILL, HEAD)])
            + part("far", -26, 15, -10, 16, 0, EAVE, [(-17, -15, 0, DOOR), *extra])
            + part("lane_end", -26, 11, -25, 15, 0, EAVE)
            + part("mid_end", -11, 11, -10, 15, 0, EAVE, [(12, 14, 0, DOOR)])
            + part("between", -21, 11, -20, 15, 0, EAVE, [(12, 14, 0, DOOR)])
            + [slab(f"roof_{tag}", s * -26, s * 10, s * -10, s * 16, EAVE, ROOF)])


def hall(s, tag):
    """A team's own building: s = 1 the attackers' Store, s = -1 the defenders' Workshop.
    One big room, a gallery of windows on the passage, and a loft over the mid with a
    window of its own -- reached by the one ramp inside."""
    def part(name, x0, y0, x1, y1, bottom, top, openings=()):
        cuts = [(min(s * a, s * b), max(s * a, s * b), p, q) for a, b, p, q in openings]
        return wall(f"{tag}_{name}", s * x0, s * y0, s * x1, s * y1, bottom, top, cuts)
    return (part("spawn_side", -30, -16, -10, -15, 0, EAVE, [(-22, -20, 0, DOOR)])
            + part("passage", -30, 3, -10, 4, 0, EAVE,
                   [(-27, -25, SILL, HEAD), (-15, -13, SILL, HEAD), (-20, -18, 0, DOOR)])
            + part("lane_end", -30, -15, -29, 3, 0, EAVE, [(-13, -11, 0, DOOR)])
            + part("mid_end", -11, -15, -10, 3, 0, EAVE,
                   [(-13, -11, 0, DOOR), (-6.5, -2.5, 2 + SILL, 2 + HEAD)])
            + part("gallery", -29, -1, -11, 0, 0, EAVE, [(-21, -19, 0, DOOR)])
            + [slab(f"{tag}_loft", s * -16, s * -10, s * -11, s * -1, 0, 2)]
            + ramp(f"{tag}_loft_ramp", s * -24, s * -10, s * -16, s * -7.5, 2, "e" if s == 1 else "w")
            + [slab(f"roof_{tag}", s * -30, s * -16, s * -10, s * 4, EAVE, ROOF)])


# name, x, y, size, floor -- cover; a low crate beside a tall one is a way up. A crate on a
# site pad stands on the pad, so its floor is the pad's top
CRATES = [
    ("crate_a1", -34, 22, TALL, 2.05), ("crate_a2", -32, 22, LOW, 2.05), ("crate_a3", -25, 25, TALL, 2),
    ("crate_b1", 34, -26, TALL, 0), ("crate_b2", 33, -26, LOW, 0),    # a stair onto the walk
    ("crate_b3", 27, -23, TALL, 0.05), ("crate_b4", 29, -23, LOW, 0.05),
    ("crate_w1", -33, -2, TALL, 0), ("crate_w2", -32, 0, LOW, 0),
    ("crate_e1", 31, 0, TALL, 0), ("crate_e2", 31, -1, LOW, 0),
    ("crate_m1", 7, 13, TALL, 0), ("crate_m2", 6, 13, LOW, 0),        # mid north
    ("crate_m3", -9, -15, TALL, 0), ("crate_m4", -7, -14, LOW, 0),    # mid south
    ("crate_s1", -27, -6, TALL, 0), ("crate_s2", -25, -6, LOW, 0),    # in the store
    ("crate_s3", 25, 4, TALL, 0), ("crate_s4", 24, 5, LOW, 0),        # in the workshop
]

# name, x, y, floor, title -- where each side can hold a view from, and what answers it
VANTAGES = [
    ("v1", -27, 17.6, 2, "Lookout"),        # over the A ramp and the west lane
    ("v2", -2.5, 2.5, 2.5, "Deck"),         # over both passage mouths
    ("v3", -13.5, -5, 2, "Store loft"),     # over mid south and the deck
    ("v4", 13.5, 5, 2, "Workshop loft"),    # over mid north and the deck
    ("v5", 37.5, -21, 2, "Walk"),           # over site B and its main entrance
    ("v6", 23, -14.4, 0, "Gatehouse"),      # a window over B main
]
COUNTERS = [
    ("c1", -25, 23.5, 2, "Plateau"),        # the lookout's door opens there; its parapet can be vaulted
    ("c2", 0, -3.5, 0, "Underpass"),        # beneath the deck's sight
    ("c3", -26, -8.75, 0, "Store ramp foot"),   # the only way up to the store loft
    ("c4", 26, 8.75, 0, "Workshop ramp"),       # the only way up to the workshop loft
    ("c5", 33.5, -25.5, 1, "Crate stair"),      # a jump onto the walk's south end
    ("c6", 28, -24.5, 0, "Plant crate"),        # out of the gatehouse window
]


def build():
    """The arena, in the scene, in flat colours. Returns its parts, in the order the GLB
    holds them; `DOORWAYS` holds every doorway cut along the way."""
    DOORWAYS.clear()
    s3d.empty_scene()
    ground = slab("ground", -W / 2, -D / 2, W / 2, D / 2, -1, 0)
    shell = [slab("wall_n", -40, 27, 40, 28, 0, H), slab("wall_s", -40, -28, 40, -27, 0, H),
             slab("wall_w", -40, -27, -39, 27, 0, H), slab("wall_e", 39, -27, 40, 27, 0, H)]

    # site A is a terrace: the lane climbs a ramp to it, the north strip a stair, and its
    # lookout looks over both
    high = [slab("plateau", -39, 16, -22, 27, 0, 2)] + ramp("a_ramp", -39, 8, -30, 16, 2, "n")
    flights = stairs("a_stairs", -22, 23, -14, 27, 2, 8, "e")
    # the lookout's south side is open above a 1 m parapet: from along it the ramp's east side
    # is in view, down to its foot and the lane beyond -- and the parapet is low enough to vault
    lookout = ([slab("lookout_s", -30, 16, -23, 17, 2, 3)]
               + wall("lookout_n", -30, 21, -23, 22, 2, EAVE, [(-26, -24, 2, 2 + DOOR)])
               + wall("lookout_w", -30, 17, -29, 21, 2, EAVE) + wall("lookout_e", -24, 17, -23, 21, 2, EAVE))

    # mid is a deck on four pillars, a ramp up from the south and a stair from the north; the
    # underpass runs through beneath it. Its parapets stop short of the corners: wider views
    # from them, and a corner is exposed to the strip below
    high += [slab("deck", -5, -2, 5, 4, 2, 2.5)]
    high += [slab(f"pillar_{tag}", x, y, x + 1, y + 1, 0, 2)
             for tag, x, y in (("sw", -5, -2), ("se", 4, -2), ("nw", -5, 3), ("ne", 4, 3))]
    rails = [slab("deck_parapet_w", -5, -1, -4.5, 3, 2.5, 3.5), slab("deck_parapet_e", 4.5, -1, 5, 3, 2.5, 3.5)]
    high += ramp("deck_ramp", -5, -12, -1, -2, 2.5, "n")
    flights += stairs("deck_stairs", 1, 4, 5, 10, 2.5, 12, "n")

    # site B is a courtyard: a walk along its east wall looks over it, off a stair from the lane
    high.append(slab("walk", 36, -27, 39, -16, 0, 2))
    rails.append(slab("walk_parapet", 36, -23, 36.5, -18, 2, 3))
    flights += stairs("walk_stairs", 36, -16, 39, -12, 2, 8, "n")

    rooms = wing(1, "nw") + wing(-1, "se", extra=[(-24.5, -21.5, SILL, HEAD)]) + hall(1, "store") + hall(-1, "workshop")
    high += [o for o in rooms if "_loft" in o.name]
    roofs = [o for o in rooms if o.name.startswith("roof")]
    roofs.append(slab("roof_lookout", -30, 16, -23, 22, EAVE, ROOF))
    walls = shell + lookout + rails + [o for o in rooms if "_loft" not in o.name and not o.name.startswith("roof")]
    walls += [slab("gate_w", -39, -8, -34, -7, 0, 3), slab("gate_e", 34, 7, 39, 8, 0, 3),
              slab("b_main_wall", 20, -27, 21, -23, 0, 3)]
    pads = [slab("site_a", -37, 18, -30, 26, 2, 2.05), slab("site_b", 24, -25, 32, -19, 0, 0.05),
            slab("spawn_attack", -37, -26, -25, -19, 0, 0.05), slab("spawn_defend", 25, 19, 37, 26, 0, 0.05)]
    crates = [slab(name, x, y, x + size, y + size, z, z + size) for name, x, y, size, z in CRATES]
    marks = [slab(name, x - 0.7, y - 0.7, x + 0.7, y + 0.7, z, z + 0.06) for name, x, y, z, _ in VANTAGES + COUNTERS]

    s3d.material(ground, "concrete", color="#9a9a94")
    s3d.material(walls, "concrete", color="#d6d6d0")
    s3d.material(roofs, "concrete", color="#9fa39a")
    s3d.material(high, "painted_metal", color="#8fb8a8")
    s3d.material(flights, "painted_metal", color="#b9cfc5")
    s3d.material(pads[:2], "painted_metal", color="#e3b23c")
    s3d.material(pads[2], "painted_metal", color="#d5493f")
    s3d.material(pads[3], "painted_metal", color="#3f7fd5")
    s3d.material(crates, "pine", color="#c49a5a")
    s3d.material(marks[:len(VANTAGES)], "painted_metal", color="#8e44ad")
    s3d.material(marks[len(VANTAGES):], "painted_metal", color="#1eaa6b")

    return [ground] + high + flights + walls + roofs + pads + crates + marks


if __name__ == "__main__":
    parts = build()
    s3d.export(s3d.group("arena", *parts),
               str(Path(__file__).resolve().parents[1] / "models" / "arena_blockout.glb"))
    print(f"OK - arena_blockout.glb exported ({len(parts)} parts, "
          f"{sum('_ramp' in o.name for o in parts)} of them ramps, {len(DOORWAYS)} doorways)")
