"""Terrain shader: a Principled BSDF whose base color is driven by world-space
height (grass at low elevation, dirt/rock in the middle, snow/rock highlight
at peaks), so slopes read visually without needing baked textures.
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
    output.location = (600, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    bsdf.inputs["Roughness"].default_value = 0.85
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.location = (-600, 0)

    separate_xyz = nodes.new("ShaderNodeSeparateXYZ")
    separate_xyz.location = (-450, 0)
    links.new(geometry.outputs["Position"], separate_xyz.inputs["Vector"])

    map_range = nodes.new("ShaderNodeMapRange")
    map_range.location = (-300, 0)
    map_range.inputs["From Min"].default_value = 0.0
    map_range.inputs["From Max"].default_value = max_height
    links.new(separate_xyz.outputs["Z"], map_range.inputs["Value"])

    color_ramp = nodes.new("ShaderNodeValToRGB")
    color_ramp.location = (-100, 0)
    elements = color_ramp.color_ramp.elements
    elements[0].position = 0.0
    elements[0].color = (0.05, 0.2, 0.05, 1.0)     # low ground: grass
    elements[1].position = 0.6
    elements[1].color = (0.35, 0.3, 0.22, 1.0)     # mid slopes: dirt/rock
    peak = elements.new(1.0)
    peak.color = (0.9, 0.9, 0.92, 1.0)             # peaks: snow/rock highlight

    links.new(map_range.outputs["Result"], color_ramp.inputs["Fac"])
    links.new(color_ramp.outputs["Color"], bsdf.inputs["Base Color"])

    return mat
