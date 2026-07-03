"""Procedural sky environment.

A Nishita sky texture gives every render a realistic gradient sky + sun
without needing an HDRI image file - set once when terrain is generated
(see ui/operators.py's PROCGEN_OT_generate_terrain), so users get decent
default lighting/reflections without having to build a world shader
themselves.
"""

WORLD_NAME = "ProcgenMaps_Sky"


def get_or_create_sky_world(sun_elevation=0.6, sun_rotation=2.4):
    import bpy

    existing = bpy.data.worlds.get(WORLD_NAME)
    if existing is not None:
        return existing

    world = bpy.data.worlds.new(WORLD_NAME)
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputWorld")
    output.location = (300, 0)

    background = nodes.new("ShaderNodeBackground")
    background.location = (0, 0)
    background.inputs["Strength"].default_value = 1.0
    links.new(background.outputs["Background"], output.inputs["Surface"])

    sky = nodes.new("ShaderNodeTexSky")
    sky.location = (-300, 0)
    sky.sky_type = 'NISHITA'
    sky.sun_elevation = sun_elevation
    sky.sun_rotation = sun_rotation
    sky.air_density = 1.0
    sky.dust_density = 1.0
    links.new(sky.outputs["Color"], background.inputs["Color"])

    return world


def set_sun_position(world, sun_elevation, sun_rotation):
    """Reuse the same sky texture for night mode by dipping the sun below
    the horizon rather than swapping to a separate flat-color world."""
    if world is None or not world.use_nodes:
        return
    sky = next((n for n in world.node_tree.nodes if n.bl_idname == "ShaderNodeTexSky"), None)
    if sky is not None:
        sky.sun_elevation = sun_elevation
        sky.sun_rotation = sun_rotation
