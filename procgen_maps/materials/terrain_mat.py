"""Terrain shader: a Principled BSDF whose base color is driven by world-space
height (grass at low elevation, dirt/rock in the middle, snow/rock highlight
at peaks), so slopes read visually without needing baked textures. A large-
scale noise texture breaks up the flat per-band color into grass/rock
speckle variation, and a second, finer noise drives a Bump node for subtle
micro-surface roughness - both purely shading tricks (no extra geometry),
which is what makes them cheap enough to use everywhere.
"""

TERRAIN_MATERIAL_NAME = "ProcgenMaps_Terrain"


def get_or_create_terrain_material(max_height: float = 40.0):
    import bpy

    existing = bpy.data.materials.get(TERRAIN_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(TERRAIN_MATERIAL_NAME)
    mat.diffuse_color = (0.12, 0.28, 0.10, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (900, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (600, 0)
    bsdf.inputs["Roughness"].default_value = 0.85
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.location = (-900, 200)

    separate_xyz = nodes.new("ShaderNodeSeparateXYZ")
    separate_xyz.location = (-700, 200)
    links.new(geometry.outputs["Position"], separate_xyz.inputs["Vector"])

    map_range = nodes.new("ShaderNodeMapRange")
    map_range.location = (-500, 200)
    map_range.inputs["From Min"].default_value = 0.0
    map_range.inputs["From Max"].default_value = max_height
    links.new(separate_xyz.outputs["Z"], map_range.inputs["Value"])

    color_ramp = nodes.new("ShaderNodeValToRGB")
    color_ramp.location = (-300, 200)
    elements = color_ramp.color_ramp.elements
    elements[0].position = 0.0
    elements[0].color = (0.05, 0.2, 0.05, 1.0)     # low ground: grass
    elements[1].position = 0.6
    elements[1].color = (0.35, 0.3, 0.22, 1.0)     # mid slopes: dirt/rock
    peak = elements.new(1.0)
    peak.color = (0.9, 0.9, 0.92, 1.0)             # peaks: snow/rock highlight
    links.new(map_range.outputs["Result"], color_ramp.inputs["Fac"])

    speckle_noise = nodes.new("ShaderNodeTexNoise")
    speckle_noise.location = (-700, -100)
    speckle_noise.inputs["Scale"].default_value = 6.0
    speckle_noise.inputs["Detail"].default_value = 4.0

    speckle_remap = nodes.new("ShaderNodeMapRange")
    speckle_remap.location = (-500, -100)
    speckle_remap.inputs["To Min"].default_value = 0.85
    speckle_remap.inputs["To Max"].default_value = 1.15
    links.new(speckle_noise.outputs["Fac"], speckle_remap.inputs["Value"])

    speckle_color = nodes.new("ShaderNodeCombineColor")
    speckle_color.location = (-300, -100)
    links.new(speckle_remap.outputs["Result"], speckle_color.inputs[0])
    links.new(speckle_remap.outputs["Result"], speckle_color.inputs[1])
    links.new(speckle_remap.outputs["Result"], speckle_color.inputs[2])

    # ShaderNodeMix has separate hidden A/B/Result sockets per data_type - by
    # name, "A"/"B"/"Result" resolve to the first (Float) variant, not RGBA,
    # so the RGBA sockets must be addressed by index (6/7 in, 2 out).
    speckle_mix = nodes.new("ShaderNodeMix")
    speckle_mix.location = (0, 100)
    speckle_mix.data_type = 'RGBA'
    speckle_mix.blend_type = 'MULTIPLY'
    speckle_mix.inputs[0].default_value = 1.0
    links.new(color_ramp.outputs["Color"], speckle_mix.inputs[6])
    links.new(speckle_color.outputs["Color"], speckle_mix.inputs[7])
    links.new(speckle_mix.outputs[2], bsdf.inputs["Base Color"])

    bump_noise = nodes.new("ShaderNodeTexNoise")
    bump_noise.location = (-300, -350)
    bump_noise.inputs["Scale"].default_value = 40.0
    bump_noise.inputs["Detail"].default_value = 6.0

    bump = nodes.new("ShaderNodeBump")
    bump.location = (300, -200)
    bump.inputs["Strength"].default_value = 0.15
    bump.inputs["Distance"].default_value = 0.3
    links.new(bump_noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return mat
