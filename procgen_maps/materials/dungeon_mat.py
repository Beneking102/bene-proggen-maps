"""Materials for the BSP dungeon generator (generators/dungeon.py), one
set per theme: stone wall/floor/ceiling, distinct floor tints for the
start/boss rooms, a door material, the theme props, and a warm emissive
material for torch flames. Mirrors prop_mat.py's lightweight idiom (Base
Color + Roughness on the default Principled BSDF, plus a noise grain and
bump on the stone) rather than city_mat.py's - dungeon.py is deliberately
independent of the city/terrain pipeline.
"""

# Per-theme colors; a key missing from a theme falls back to "crypt".
_THEME_COLORS = {
    "crypt": {
        "wall": (0.30, 0.29, 0.30, 1.0),
        "floor": (0.21, 0.20, 0.21, 1.0),
        "ceiling": (0.15, 0.14, 0.15, 1.0),
        "floor_start": (0.16, 0.30, 0.34, 1.0),   # cool blue-teal tint: the start room
        "floor_boss": (0.38, 0.14, 0.13, 1.0),    # warm red tint: the largest/boss room
        "torch_holder": (0.18, 0.14, 0.10, 1.0),
        "torch_flame": (1.0, 0.55, 0.15, 1.0),
        "door": (0.22, 0.20, 0.19, 1.0),          # iron gate
        "prop": (0.52, 0.50, 0.46, 1.0),          # pale sarcophagus stone
        "banner": (0.35, 0.10, 0.12, 1.0),
    },
    "cave": {
        "wall": (0.32, 0.25, 0.19, 1.0),
        "floor": (0.26, 0.21, 0.16, 1.0),
        "ceiling": (0.17, 0.13, 0.10, 1.0),
        "floor_start": (0.20, 0.28, 0.24, 1.0),
        "floor_boss": (0.36, 0.17, 0.12, 1.0),
        "torch_holder": (0.18, 0.14, 0.10, 1.0),
        "torch_flame": (1.0, 0.5, 0.12, 1.0),
        "door": (0.28, 0.22, 0.16, 1.0),
        "prop": (0.36, 0.30, 0.24, 1.0),          # boulders
        "banner": (0.35, 0.10, 0.12, 1.0),
    },
    "castle": {
        "wall": (0.50, 0.45, 0.37, 1.0),          # sandstone
        "floor": (0.34, 0.32, 0.29, 1.0),         # flagstones
        "ceiling": (0.26, 0.18, 0.11, 1.0),       # timber
        "floor_start": (0.22, 0.34, 0.36, 1.0),
        "floor_boss": (0.42, 0.16, 0.14, 1.0),
        "torch_holder": (0.16, 0.13, 0.10, 1.0),
        "torch_flame": (1.0, 0.6, 0.2, 1.0),
        "door": (0.36, 0.22, 0.11, 1.0),          # oak
        "prop": (0.36, 0.22, 0.11, 1.0),
        "banner": (0.55, 0.08, 0.08, 1.0),        # heraldic red cloth
    },
}

_ROUGHNESS_OVERRIDES = {
    "wall": 0.85,
    "floor": 0.8,
    "ceiling": 0.9,
    "floor_start": 0.8,
    "floor_boss": 0.8,
    "torch_holder": 0.7,
    "door": 0.55,
    "banner": 0.95,
}

_EMISSION_STRENGTH = {
    "torch_flame": 4.0,
}

_STONE_KEYS = ("wall", "floor", "ceiling", "floor_start", "floor_boss", "prop")
_GRAIN_SCALE = {"crypt": 1.8, "cave": 1.2, "castle": 2.4}
_BUMP_STRENGTH = {"crypt": 0.35, "cave": 0.6, "castle": 0.25}


def get_or_create_dungeon_material(key: str, theme: str = "crypt"):
    """The dungeon stone material for `theme` (created once per theme)."""
    import bpy

    theme = theme if theme in _THEME_COLORS else "crypt"
    name = f"ProcgenMaps_Dungeon_{theme}_{key}" if theme != "crypt" else f"ProcgenMaps_Dungeon_{key}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    color = _THEME_COLORS[theme].get(key, _THEME_COLORS["crypt"][key])
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
            _add_stone_grain(mat, bsdf, color, _GRAIN_SCALE[theme], _BUMP_STRENGTH[theme])
    return mat


def _add_stone_grain(mat, bsdf, color, scale=1.8, bump_strength=0.35):
    """Noise-driven tone variation + bump so the flat stone reads as rough
    masonry under the torch light instead of smooth painted plaster."""
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-900, -200)
    grain = nodes.new("ShaderNodeTexNoise")
    grain.location = (-700, -200)
    grain.inputs["Scale"].default_value = scale
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
    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = 0.08
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
