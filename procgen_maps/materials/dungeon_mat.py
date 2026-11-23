"""Flat materials for the BSP dungeon generator (generators/dungeon.py):
stone wall/floor/ceiling, distinct floor tints for the start/boss rooms,
and a warm emissive material for torch flames. Mirrors prop_mat.py's
lightweight idiom (Base Color + Roughness on the default Principled BSDF,
no custom node graph) rather than city_mat.py's - dungeon.py is
deliberately independent of the city/terrain pipeline and doesn't need
the shared facade-material-index machinery those use.
"""

_COLORS = {
    "wall": (0.30, 0.29, 0.28, 1.0),
    "floor": (0.22, 0.21, 0.20, 1.0),
    "ceiling": (0.16, 0.15, 0.15, 1.0),
    "floor_start": (0.16, 0.30, 0.34, 1.0),   # cool blue-teal tint: the start room
    "floor_boss": (0.38, 0.14, 0.13, 1.0),    # warm red tint: the largest/boss room
    "torch_holder": (0.18, 0.14, 0.10, 1.0),
    "torch_flame": (1.0, 0.55, 0.15, 1.0),
}

_ROUGHNESS_OVERRIDES = {
    "wall": 0.85,
    "floor": 0.8,
    "ceiling": 0.9,
    "floor_start": 0.8,
    "floor_boss": 0.8,
    "torch_holder": 0.7,
}

_EMISSION_STRENGTH = {
    "torch_flame": 4.0,
}


def get_or_create_dungeon_material(key: str):
    import bpy

    name = f"ProcgenMaps_Dungeon_{key}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    color = _COLORS[key]
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = _ROUGHNESS_OVERRIDES.get(key, 0.8)
        emission_strength = _EMISSION_STRENGTH.get(key, 0.0)
        if emission_strength > 0.0:
            bsdf.inputs["Emission Color"].default_value = color
            bsdf.inputs["Emission Strength"].default_value = emission_strength
        elif key in _STONE_KEYS:
            _add_stone_grain(mat, bsdf, color)
    return mat


_STONE_KEYS = ("wall", "floor", "ceiling", "floor_start", "floor_boss")


def _add_stone_grain(mat, bsdf, color):
    """Noise-driven tone variation + bump so the flat stone reads as rough
    masonry under the torch light instead of smooth painted plaster."""
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-900, -200)
    grain = nodes.new("ShaderNodeTexNoise")
    grain.location = (-700, -200)
    grain.inputs["Scale"].default_value = 1.8
    grain.inputs["Detail"].default_value = 6.0
    grain.inputs["Roughness"].default_value = 0.7
    links.new(tex_coord.outputs["Object"], grain.inputs["Vector"])
    tone = nodes.new("ShaderNodeMapRange")
    tone.location = (-500, -200)
    tone.inputs["To Min"].default_value = 0.7
    tone.inputs["To Max"].default_value = 1.25
    links.new(grain.outputs["Fac"], tone.inputs["Value"])
    tone_rgb = nodes.new("ShaderNodeCombineColor")
    tone_rgb.location = (-300, -200)
    for index in range(3):
        links.new(tone.outputs["Result"], tone_rgb.inputs[index])
    mix = nodes.new("ShaderNodeMix")
    mix.location = (-100, 0)
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    mix.inputs[0].default_value = 1.0
    mix.inputs[6].default_value = color
    links.new(tone_rgb.outputs["Color"], mix.inputs[7])
    links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    bump = nodes.new("ShaderNodeBump")
    bump.location = (-100, -400)
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.08
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
