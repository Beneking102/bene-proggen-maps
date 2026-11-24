"""Aerial perspective ("haze") as one shared shader node group.

Distant geometry in real photographs fades toward the sky color; without
it, a 400 m-radius city renders as one flat, equally-contrasty plane and
the back rows read as sitting right behind the front ones. The physically
"correct" way is a scattering volume on the World, but in Cycles that
costs volume sampling on every ray and is very noisy at the low CPU
sample counts this addon's showcase renders aim for. Instead, every
material this addon creates routes its final shader through
`ProcgenMaps_AerialPerspective`: a Mix Shader blending the original BSDF
toward an Emission of the haze color by `1 - exp(-view_distance *
density)` (Camera Data > View Distance). It is a per-material shading
trick, so it is free at render time, deterministic, and - because all
materials share the single node group - `set_haze` retunes the whole city
by editing two nodes once (the Lighting panel's Haze slider and the sun
presets call it; a warm sunset sky gets a warm haze).

Why the compositor wasn't used: Blender 5.0 replaced the whole compositor
node API (the classic Composite/Mix node types no longer exist), and a
compositor setup also wouldn't show in the Material Preview viewport.
"""

GROUP_NAME = "ProcgenMaps_AerialPerspective"
DENSITY_NODE_NAME = "ProcgenMaps_HazeDensity"
COLOR_NODE_NAME = "ProcgenMaps_HazeColor"

# `haze_strength` (0..1 Lighting-panel slider) -> optical density per meter.
# 1.0 means ~63% fade at 500 m; the default 0.3 gives ~26% at 500 m, so a
# 400 m-radius city's far edge is visibly softer but still fully legible.
DENSITY_PER_STRENGTH = 0.002
DEFAULT_HAZE_STRENGTH = 0.3
DEFAULT_HAZE_COLOR = (0.62, 0.70, 0.82, 1.0)
# The haze colour is a display-ish 0..1 tint; the physical sky it should
# match sits at several units of scene-linear radiance near the horizon
# (the addon renders at about -2.7 EV), so the emission is scaled up by this
# much. v0.14 and earlier used 1.0, which made every distant surface fade
# to a *dark* grey-blue and the horizon read as a flat grey void.
HAZE_BRIGHTNESS = 1.6


def get_or_create_haze_group():
    """The shared aerial-perspective node group (created once; see the module docstring)."""
    import bpy

    existing = bpy.data.node_groups.get(GROUP_NAME)
    if existing is not None:
        return existing

    group = bpy.data.node_groups.new(GROUP_NAME, "ShaderNodeTree")
    group.interface.new_socket("Shader", in_out='INPUT', socket_type='NodeSocketShader')
    group.interface.new_socket("Shader", in_out='OUTPUT', socket_type='NodeSocketShader')
    nodes = group.nodes
    links = group.links

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-800, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (400, 0)

    camera = nodes.new("ShaderNodeCameraData")
    camera.location = (-800, -200)

    density = nodes.new("ShaderNodeValue")
    density.name = DENSITY_NODE_NAME
    density.label = "Haze Density"
    density.location = (-800, -350)
    density.outputs[0].default_value = DEFAULT_HAZE_STRENGTH * DENSITY_PER_STRENGTH

    scaled = nodes.new("ShaderNodeMath")
    scaled.operation = 'MULTIPLY'
    scaled.location = (-600, -250)
    links.new(camera.outputs["View Distance"], scaled.inputs[0])
    links.new(density.outputs[0], scaled.inputs[1])

    negated = nodes.new("ShaderNodeMath")
    negated.operation = 'MULTIPLY'
    negated.location = (-400, -250)
    negated.inputs[1].default_value = -1.0
    links.new(scaled.outputs[0], negated.inputs[0])

    transmittance = nodes.new("ShaderNodeMath")
    transmittance.operation = 'EXPONENT'
    transmittance.location = (-200, -250)
    links.new(negated.outputs[0], transmittance.inputs[0])

    factor = nodes.new("ShaderNodeMath")
    factor.operation = 'SUBTRACT'
    factor.location = (0, -250)
    factor.inputs[0].default_value = 1.0
    links.new(transmittance.outputs[0], factor.inputs[1])

    color = nodes.new("ShaderNodeRGB")
    color.name = COLOR_NODE_NAME
    color.label = "Haze Color"
    color.location = (-400, -500)
    color.outputs[0].default_value = DEFAULT_HAZE_COLOR

    emission = nodes.new("ShaderNodeEmission")
    emission.location = (-100, -450)
    emission.inputs["Strength"].default_value = HAZE_BRIGHTNESS
    links.new(color.outputs[0], emission.inputs["Color"])

    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (200, 0)
    links.new(factor.outputs[0], mix.inputs["Fac"])
    links.new(group_in.outputs[0], mix.inputs[1])
    links.new(emission.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], group_out.inputs[0])
    return group


def apply_haze(material):
    """Route `material`'s surface shader through the shared haze group.
    Idempotent: a material that already has the group inserted is left
    alone, and materials without nodes/output are ignored."""
    if material is None or not material.use_nodes:
        return material
    tree = material.node_tree
    if any(node.bl_idname == "ShaderNodeGroup" and node.node_tree is not None
           and node.node_tree.name == GROUP_NAME for node in tree.nodes):
        return material
    output = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if output is None or not output.inputs["Surface"].is_linked:
        return material
    surface_link = output.inputs["Surface"].links[0]
    source_socket = surface_link.from_socket

    group_node = tree.nodes.new("ShaderNodeGroup")
    group_node.node_tree = get_or_create_haze_group()
    group_node.location = (output.location.x - 180, output.location.y - 160)
    tree.links.remove(surface_link)
    tree.links.new(source_socket, group_node.inputs[0])
    tree.links.new(group_node.outputs[0], output.inputs["Surface"])
    return material


def set_haze(strength: float, color=None):
    """Retune the shared group: `strength` is the 0..1 panel value, `color`
    an optional RGBA (e.g. warmer for a low sun)."""
    import bpy

    group = bpy.data.node_groups.get(GROUP_NAME)
    if group is None:
        return
    density = group.nodes.get(DENSITY_NODE_NAME)
    if density is not None:
        density.outputs[0].default_value = max(0.0, float(strength)) * DENSITY_PER_STRENGTH
    if color is not None:
        color_node = group.nodes.get(COLOR_NODE_NAME)
        if color_node is not None:
            color_node.outputs[0].default_value = color


def haze_color_for_sun(sun_elevation: float):
    """Haze tint as a function of sun elevation (radians above the
    horizon): a high sun gives a cool blue-gray sky haze, a low sun a warm
    peach one, blended over the lowest ~17 degrees so the Lighting panel's
    Golden Hour preset gets a matching atmosphere automatically. Pure
    Python so it is unit-testable."""
    t = max(0.0, min(1.0, sun_elevation / 0.3))
    warm = (0.86, 0.66, 0.48, 1.0)
    cool = DEFAULT_HAZE_COLOR
    t = t * t * (3.0 - 2.0 * t)
    return tuple(warm[i] + (cool[i] - warm[i]) * t for i in range(4))
