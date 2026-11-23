"""Render the dressed arena: a plan, an aerial, or an eye-level shot, by day or at dusk.

    python render/render_liminal.py models/liminal/arena_liminal.glb images/liminal_plan.png plan
    python render/render_liminal.py models/liminal/arena_liminal.glb images/x.jpg aerial day
    python render/render_liminal.py models/liminal/arena_liminal.glb images/x.jpg mid dusk
    ... lane | lookout | store | deck | b | walk        and `day` wherever `dusk` stands

    python render/render_liminal.py <glb> <out> <view> <day|dusk> 50 32     a quick look: half size, few samples

The cameras and the callouts are `views`', so each picture pairs with the grey one that
`render_blockout.py` makes of the same view. The plan is lit evenly and has no dusk.

Nothing is in a picture that the file does not hold, beyond the plain under the arena, the
sun, the sky and a thin mist. By day the sun does the lighting. At dusk it hangs below the
horizon, and the rooms are lit by their ceiling tiles and the pads by their own outlines: the
24 point lamps under `Lights` are hidden, because they sit beneath tiles that already light the
rooms. What is brighter than white blooms a little, as it would on film. Cycles on GPU where
there is one; JPEG when the name ends in .jpg.
"""
import math
import sys
from pathlib import Path

import bpy
import mathutils

sys.path.insert(0, str(Path(__file__).resolve().parent))
import views  # noqa: E402

PLAIN = (0.10, 0.11, 0.13)               # what the arena stands on: a dark plain with no end and no feature
PALE = ((1.0, 1.0, 1.0), 0.6)            # the plan's even light, so that the textures show their own colours

# for each time of day: the sun's watts a square metre on level ground, which keeps a view as
# bright whatever height its sun hangs at (none once it has set); that height, in degrees (None:
# where the view's sun is); the sky's strength and how blue it is kept; the density of the mist
# lying over the plain; the exposure, in stops; and the bloom, as (what starts to glow, how far
# it spreads)
LIGHT = {
    "day":  dict(ground=3.2, height=None, sky=0.07, blue=1.0, mist=0.003, stops=-0.2, glare=(1.0, 0.12)),
    "dusk": dict(ground=0.0, height=-3.0, sky=0.3, blue=0.6, mist=0.006, stops=3.0, glare=(1.0, 0.5)),
}


def sky(scene, toward, strength, blue):
    """The world: a physical sky with its sun where `toward` has it, kept `blue` of its blue."""
    world = bpy.data.worlds.new("sky")
    if world.node_tree is None:
        world.use_nodes = True
    nt = world.node_tree
    bg = next(n for n in nt.nodes if n.type == 'BACKGROUND')
    tex = nt.nodes.new('ShaderNodeTexSky')
    tex.sky_type, tex.sun_disc = 'MULTIPLE_SCATTERING', False     # the lamp is the sun: it throws the softer shadow
    tex.sun_elevation = math.asin(toward.z)
    tex.sun_rotation = math.atan2(toward.x, toward.y)             # no turn is north, a right angle east
    wash = nt.nodes.new('ShaderNodeHueSaturation')
    wash.inputs['Saturation'].default_value = blue
    nt.links.new(tex.outputs[0], wash.inputs['Color'])
    nt.links.new(wash.outputs[0], bg.inputs[0])
    bg.inputs[1].default_value = strength
    scene.world = world


def mist(scene, density):
    """A layer of mist `density` a metre, lying over the plain: thirty metres deep, so that the
    sky and the sun cross it in a breath while a sightline along the ground loses itself in it.
    It cannot be the whole world's: that far away, no light would get through it at all."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 14))
    ob = bpy.context.object
    ob.name, ob.scale = "mist", (2000, 2000, 30)
    mat = bpy.data.materials.new("mist")
    nt = mat.node_tree
    nt.nodes.clear()
    fog = nt.nodes.new('ShaderNodeVolumeScatter')
    fog.inputs['Density'].default_value = density
    fog.inputs['Anisotropy'].default_value = 0.3
    nt.links.new(fog.outputs[0], nt.nodes.new('ShaderNodeOutputMaterial').inputs['Volume'])
    ob.data.materials.append(mat)


def sun(scene, toward, watts, soft):
    lamp = bpy.data.lights.new("sun", 'SUN')
    lamp.energy, lamp.angle, lamp.color = watts, soft, (1.0, 0.93, 0.80)
    ob = bpy.data.objects.new("sun", lamp)
    scene.collection.objects.link(ob)
    ob.rotation_euler = (-toward).to_track_quat('-Z', 'Y').to_euler()


def pale(scene):
    """The plan's light: a bright even sky and a sun almost overhead, so that shadows stay short."""
    world = bpy.data.worlds.new("pale")
    if world.node_tree is None:
        world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == 'BACKGROUND')
    bg.inputs[0].default_value = (*PALE[0], 1.0)
    bg.inputs[1].default_value = PALE[1]
    scene.world = world
    sun(scene, mathutils.Vector((-0.2, -0.24, 1.0)).normalized(), 1.6, 0.02)


def glow(scene, threshold, spread):
    """Bloom: what is brighter than `threshold` bleeds into its neighbours."""
    tree = bpy.data.node_groups.new("Compositing", 'CompositorNodeTree')
    tree.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    scene.compositing_node_group = tree
    layer, glare = tree.nodes.new('CompositorNodeRLayers'), tree.nodes.new('CompositorNodeGlare')
    glare.inputs['Type'].default_value = 'Bloom'
    glare.inputs['Threshold'].default_value = threshold
    glare.inputs['Strength'].default_value = spread
    tree.links.new(layer.outputs['Image'], glare.inputs['Image'])
    tree.links.new(glare.outputs['Image'], tree.nodes.new('NodeGroupOutput').inputs[0])


def main():
    glb, out = sys.argv[1], sys.argv[2]
    shot = sys.argv[3] if len(sys.argv) > 3 else "aerial"
    when = sys.argv[4] if len(sys.argv) > 4 else "day"
    if shot not in ("plan", "aerial", *views.VIEWS) or when not in LIGHT:
        raise SystemExit(__doc__)           # the usage, for a name that is not one
    percent = int(sys.argv[5]) if len(sys.argv) > 5 else 100
    samples = int(sys.argv[6]) if len(sys.argv) > 6 else 256
    mode = shot if shot in ("plan", "aerial") else "eye"
    light = LIGHT[when]

    scene, meshes, marks = views.load(glb)
    views.hide(meshes, marks, *(("roof",) if mode == "plan" else ()))
    for ob in scene.objects:
        ob.hide_render = ob.hide_render or ob.type == 'LIGHT'
    far = mode != "eye"                    # from above a pixel holds many texels, up close one texel holds many
    for node in (n for m in bpy.data.materials if m.node_tree for n in m.node_tree.nodes if n.type == 'TEX_IMAGE'):
        node.interpolation = 'Linear' if far else 'Closest'

    bpy.ops.mesh.primitive_plane_add(size=2000, location=(0, 0, -1.02))
    bpy.context.object.data.materials.append(views.shaded("plain", PLAIN))

    size, toward, _ = views.camera(mode, shot)
    if mode == "plan":
        views.plan_names(back=views.WHITE, halo=0.15)
        pale(scene)
    else:
        if mode == "aerial":
            views.aerial_names(backed=True)
        toward = mathutils.Vector(toward).normalized()
        if light["height"] is not None:    # the sun, sunk `height` degrees on the bearing the view gave it
            h = math.radians(light["height"])
            toward = mathutils.Vector((toward.x * math.cos(h), toward.y * math.cos(h), math.sin(h)))
        sky(scene, toward, light["sky"], light["blue"])
        if light["mist"]:
            mist(scene, light["mist"])
        if light["ground"]:
            sun(scene, toward, light["ground"] / toward.z, math.radians(2.0))
        glow(scene, *light["glare"])
        scene.view_settings.exposure = light["stops"]

    views.cycles(scene, samples)
    scene.view_settings.view_transform = 'Standard'
    views.still(scene, out, size, percent)
    print(f"rendered {out}  {shot} {when}  {size[0] * percent // 100}x{size[1] * percent // 100}  {samples} samples")


if __name__ == "__main__":
    main()
