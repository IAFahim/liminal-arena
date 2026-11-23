"""Why check_arena.py does not count a spot as floor: every probe's first hit from just above it.

    python -I tools/probe_hit.py models/liminal/arena_liminal.glb -10.25 -12.25 0

A floor stands where nothing is within 1.9 m above it and no probe's first hit is a face seen
from behind (the point is inside something). "BLOCKS" marks such a hit, with what made it.
"""
import sys

import bpy
from mathutils import Vector

glb, x, y, z = sys.argv[1], *map(float, sys.argv[2:5])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
scene = bpy.context.scene
for ob in scene.objects:                       # as the checker does: the plates are labels, not floor
    if ob.type == 'MESH' and ob.name in ("v1", "v2", "v3", "v4", "v5", "v6", "c1", "c2", "c3", "c4", "c5", "c6"):
        ob.hide_viewport = True
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
PROBES = [Vector(d).normalized() for d in ((0, 0, 1), (1, .13, .07), (-1, .11, -.05), (.17, 1, .04), (.19, -1, .09),
                                           (1, .93, .03), (-1, .96, -.02), (1, -1.07, .06), (-1, -.91, .05))]
p = Vector((x, y, z + 0.02))
print(f"from {tuple(round(c, 2) for c in p)} in {glb}")
hit, at, _, _, ob, _ = scene.ray_cast(dg, p, Vector((0, 0, 1)), distance=1.9)
print(f"  something within 1.9 m above: {ob.name if hit else 'no'}" + (f", at {tuple(round(c, 2) for c in at)}" if hit else ""))
for d in PROBES:
    hit, at, normal, _, ob, _ = scene.ray_cast(dg, p, d, distance=40)
    print(f"  {tuple(round(c, 2) for c in d)!s:17} " + (f"{'BLOCKS' if normal.dot(d) > 0.1 else 'ok    '} {ob.name:22} "
          f"at {tuple(round(c, 2) for c in at)}  normal {tuple(round(c, 2) for c in normal)}" if hit else "nothing"))
