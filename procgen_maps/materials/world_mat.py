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
    sky.location = (-600, 0)
    _set_physical_sky_model(sky)
    sky.sun_elevation = sun_elevation
    sky.sun_rotation = sun_rotation
    if hasattr(sky, "ground_albedo"):
        # Below the horizon the physical sky is black; a grassy albedo
        # gives it the faint bounce glow a real landscape horizon has.
        sky.ground_albedo = 0.3

    # Zenith lift: at the -2.4 EV the daytime presets need, the physical
    # sky's zenith is ~10x darker than its horizon and renders near-black.
    # Adding a small constant (before the Background strength, so night
    # mode's 0.05 strength scales it down too) keeps it a deep blue and
    # doubles as a little ambient fill in the shadows.
    lift = nodes.new("ShaderNodeMix")
    lift.location = (-300, 0)
    lift.data_type = 'RGBA'
    lift.blend_type = 'ADD'
    lift.inputs[0].default_value = 1.0
    lift.inputs[7].default_value = (0.35, 0.5, 0.8, 1.0)
    links.new(sky.outputs["Color"], lift.inputs[6])

    # Below the horizon (view direction z < 0) the physical sky is a flat
    # dark disc; where a low camera looks past the terrain edge that read
    # as a black sea. Blend to a pale hazy ground tone there instead - it
    # only matters for that band and for reflections.
    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-900, -300)
    direction = nodes.new("ShaderNodeSeparateXYZ")
    direction.location = (-700, -300)
    links.new(tex_coord.outputs["Generated"], direction.inputs["Vector"])
    below = nodes.new("ShaderNodeMapRange")
    below.location = (-500, -300)
    below.inputs["From Min"].default_value = -0.02
    below.inputs["From Max"].default_value = 0.0
    below.inputs["To Min"].default_value = 1.0
    below.inputs["To Max"].default_value = 0.0
    links.new(direction.outputs["Z"], below.inputs["Value"])
    ground = nodes.new("ShaderNodeMix")
    ground.location = (-150, -200)
    ground.data_type = 'RGBA'
    ground.blend_type = 'MIX'
    ground.inputs[7].default_value = (2.2, 2.6, 3.2, 1.0)
    links.new(below.outputs["Result"], ground.inputs[0])
    links.new(lift.outputs[2], ground.inputs[6])
    # Extra dust/aerosol gives a paler, hazier sky dome than the deep
    # ultramarine the physical model produces at the exposure the addon
    # uses (-2.4 EV by default). dust_density is Blender 4.x's Nishita
    # name, aerosol_density the 5.x multiple-scattering model's.
    if hasattr(sky, "air_density"):
        sky.air_density = 1.0
    for prop_name in ("dust_density", "aerosol_density"):
        if hasattr(sky, prop_name):
            setattr(sky, prop_name, 2.5)
    links.new(ground.outputs[2], background.inputs["Color"])

    return world


def _set_physical_sky_model(sky_node):
    """Blender 4.x calls the physically based sky model 'NISHITA'; Blender
    5.0 replaced it with 'MULTIPLE_SCATTERING' (plus 'SINGLE_SCATTERING')
    and dropped the old identifier entirely, so assigning 'NISHITA' there
    raises TypeError and broke Generate Terrain outright. Both models share
    the same sun_elevation/sun_rotation/air/dust inputs, so everything else
    in this module is version-agnostic."""
    valid = {item.identifier for item in sky_node.bl_rna.properties["sky_type"].enum_items}
    for candidate in ("NISHITA", "MULTIPLE_SCATTERING"):
        if candidate in valid:
            sky_node.sky_type = candidate
            return


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
