"""Procedural sky environment.

A Nishita sky texture gives every render a realistic gradient sky + sun
without needing an HDRI image file - set once when terrain is generated
(see ui/operators.py's PROCGEN_OT_generate_terrain), so users get decent
default lighting/reflections without having to build a world shader
themselves.
"""

WORLD_NAME = "ProcgenMaps_Sky"


def get_or_create_sky_world(sun_elevation=0.6, sun_rotation=2.4, sun_energy=1.0):
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
    background.inputs["Strength"].default_value = sun_energy
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
    the horizon rather than swapping to a separate flat-color world.

    Only ever touches the addon's own `ProcgenMaps_Sky` world (checked by
    name), not whatever World happens to be assigned to the scene - the
    Lighting panel's sun fields (ui/panels.py) live-update on every drag,
    including before "Generate Terrain" has ever run, when
    `context.scene.world` is still Blender's own default World (or some
    other scene's World); without this check a drag would silently mutate
    that unrelated World's nodes instead of safely no-op'ing."""
    if world is None or not world.use_nodes or world.name != WORLD_NAME:
        return
    sky = next((n for n in world.node_tree.nodes if n.bl_idname == "ShaderNodeTexSky"), None)
    if sky is not None:
        sky.sun_elevation = sun_elevation
        sky.sun_rotation = sun_rotation


def set_sun_energy(world, energy):
    """Mirrors set_sun_position but targets the Background node's Strength
    input rather than the sky texture's sun angle - this Strength value is
    the addon's only 'how bright is the world lit' knob since no separate
    Sun light object exists in the main generate/night-mode scene (only
    rendering/showcase.py's one-off showcase render adds an actual Sun
    light, which this function intentionally does not touch).

    Same by-name guard as set_sun_position, and even more important here:
    a Background node (unlike a Sky Texture node) exists on almost any
    node-enabled World, so without the name check this would silently
    overwrite an unrelated World's Strength far more often."""
    if world is None or not world.use_nodes or world.name != WORLD_NAME:
        return
    background = next((n for n in world.node_tree.nodes if n.bl_idname == "ShaderNodeBackground"), None)
    if background is not None:
        background.inputs["Strength"].default_value = energy
