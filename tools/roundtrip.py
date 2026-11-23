"""Do the FBX, the OBJ and the .blend come back the size they went out, with their textures?

    python -I tools/roundtrip.py models/liminal/arena_liminal.fbx models/liminal/obj/arena_liminal.obj \
        models/liminal/arena_liminal.blend

Each is imported into an empty scene and its meshes measured in the world: the arena is
x -40..40, y -28..28, z -1..7 once the file is back in Blender's axes.
"""
import sys

import bpy
from mathutils import Vector

fbx, obj, blend = sys.argv[1:4]


def report(label):
    bpy.context.view_layer.update()
    lo, hi = [1e9] * 3, [-1e9] * 3
    meshes = lamps = empties = 0
    textured = set()
    for ob in bpy.context.scene.objects:
        lamps += ob.type == 'LIGHT'
        empties += ob.type == 'EMPTY'
        if ob.type != 'MESH':
            continue
        meshes += 1
        for corner in ob.bound_box:
            p = ob.matrix_world @ Vector(corner)
            for k in range(3):
                lo[k], hi[k] = min(lo[k], p[k]), max(hi[k], p[k])
        for slot in ob.material_slots:
            m = slot.material
            if m and m.node_tree and any(n.type == 'TEX_IMAGE' and n.image for n in m.node_tree.nodes):
                textured.add(m.name)
    print(f"{label:6} meshes {meshes:3}  textured materials {len(textured):2}  lamps {lamps:2}  empties {empties:2}  "
          f"box x {lo[0]:.1f}..{hi[0]:.1f}  y {lo[1]:.1f}..{hi[1]:.1f}  z {lo[2]:.1f}..{hi[2]:.1f}")
    return meshes


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=fbx)
report("fbx")
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=obj)
report("obj")
bpy.ops.wm.open_mainfile(filepath=blend)
report("blend")
images = [i for i in bpy.data.images if i.name != "Render Result"]
print(f"blend: {sum(1 for i in images if i.packed_file)} of {len(images)} images packed;"
      f" unpacked and unreadable: {[i.name for i in images if not i.packed_file and not i.has_data]}")
