"""Flat materials for placeholder prop primitives (trees, lamp, bench, car,
sign). Assigned once per shared master mesh in assets/factory.py's instance
source collections, so every Instance-Collection Empty shows the same
material at zero extra cost.

Each material sets `diffuse_color` in addition to a Principled BSDF node
setup: Blender's Solid viewport shading mode reads `diffuse_color` (a flat,
non-node RGBA field) for its "Material" color option, ignoring the node
graph entirely - without it, props render as flat mid-gray in Solid mode
even though Material Preview/Rendered would show the right color.
"""

_COLORS = {
    "tree_trunk": (0.25, 0.16, 0.08, 1.0),
    "tree_canopy": (0.14, 0.40, 0.12, 1.0),
    "lamp_pole": (0.15, 0.15, 0.17, 1.0),
    "lamp_head": (0.85, 0.80, 0.55, 1.0),
    "bench": (0.32, 0.20, 0.10, 1.0),
    "car": (0.32, 0.42, 0.55, 1.0),
    "sign_pole": (0.20, 0.20, 0.20, 1.0),
    "sign_board": (0.90, 0.90, 0.85, 1.0),
    "rooftop_unit": (0.55, 0.55, 0.58, 1.0),
    "furniture_wood": (0.45, 0.30, 0.18, 1.0),
    "furniture_soft": (0.55, 0.20, 0.22, 1.0),
    "furniture_metal": (0.5, 0.5, 0.52, 1.0),
    "furniture_crate": (0.62, 0.48, 0.30, 1.0),
    "interior_floor": (0.55, 0.48, 0.40, 1.0),
    "interior_ceiling": (0.85, 0.84, 0.80, 1.0),
    "fountain_stone": (0.62, 0.60, 0.55, 1.0),
    "fountain_water": (0.16, 0.42, 0.52, 1.0),
    "parking_asphalt": (0.08, 0.08, 0.09, 1.0),
    "parking_line": (0.85, 0.85, 0.78, 1.0),
}

_ROUGHNESS_OVERRIDES = {
    "car": 0.35,
    "lamp_head": 0.4,
    "furniture_metal": 0.3,
    "interior_floor": 0.6,
    "fountain_water": 0.12,
    "parking_asphalt": 0.9,
    "parking_line": 0.6,
}


# Keys whose color is varied per *instance* through Object Info > Random
# (Cycles gives every collection instance its own random value), so a
# street of identical tree/car meshes doesn't read as copy-paste: (hue
# shift range, value/brightness range).
_PER_INSTANCE_VARIATION = {
    "tree_canopy": (0.05, (0.75, 1.25)),
    "tree_trunk": (0.02, (0.8, 1.2)),
    "bench": (0.02, (0.8, 1.15)),
}

# Parked cars: a palette picked per instance instead of one blue for all.
_CAR_PALETTE = (
    (0.80, 0.80, 0.82, 1.0),   # silver
    (0.08, 0.08, 0.09, 1.0),   # black
    (0.55, 0.08, 0.06, 1.0),   # red
    (0.12, 0.20, 0.45, 1.0),   # blue
    (0.85, 0.85, 0.85, 1.0),   # white
    (0.25, 0.26, 0.28, 1.0),   # graphite
)


def get_or_create_prop_material(key: str):
    import bpy

    from . import atmosphere

    name = f"ProcgenMaps_Prop_{key}"
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
        bsdf.inputs["Roughness"].default_value = _ROUGHNESS_OVERRIDES.get(key, 0.75)
        if key == "car":
            _add_car_palette(mat, bsdf)
        elif key in _PER_INSTANCE_VARIATION:
            _add_instance_variation(mat, bsdf, color, *_PER_INSTANCE_VARIATION[key])
    atmosphere.apply_haze(mat)
    return mat


def _add_instance_variation(mat, bsdf, color, hue_range, value_range):
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    info = nodes.new("ShaderNodeObjectInfo")
    info.location = (-700, -200)
    hue = nodes.new("ShaderNodeMapRange")
    hue.location = (-500, -100)
    hue.inputs["To Min"].default_value = 0.5 - hue_range
    hue.inputs["To Max"].default_value = 0.5 + hue_range
    links.new(info.outputs["Random"], hue.inputs["Value"])
    # A second, decorrelated random from the same value for brightness.
    second = nodes.new("ShaderNodeMath")
    second.location = (-500, -300)
    second.operation = 'FRACT'
    scaled = nodes.new("ShaderNodeMath")
    scaled.location = (-650, -400)
    scaled.operation = 'MULTIPLY'
    scaled.inputs[1].default_value = 7.31
    links.new(info.outputs["Random"], scaled.inputs[0])
    links.new(scaled.outputs[0], second.inputs[0])
    value = nodes.new("ShaderNodeMapRange")
    value.location = (-300, -300)
    value.inputs["To Min"].default_value = value_range[0]
    value.inputs["To Max"].default_value = value_range[1]
    links.new(second.outputs[0], value.inputs["Value"])
    hsv = nodes.new("ShaderNodeHueSaturation")
    hsv.location = (-100, 0)
    hsv.inputs["Color"].default_value = color
    links.new(hue.outputs["Result"], hsv.inputs["Hue"])
    links.new(value.outputs["Result"], hsv.inputs["Value"])
    links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])


def _add_car_palette(mat, bsdf):
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    info = nodes.new("ShaderNodeObjectInfo")
    info.location = (-500, -200)
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-300, 0)
    ramp.color_ramp.interpolation = 'CONSTANT'
    elements = ramp.color_ramp.elements
    elements[0].position = 0.0
    elements[0].color = _CAR_PALETTE[0]
    for index, color in enumerate(_CAR_PALETTE[1:], start=1):
        element = elements.new(index / len(_CAR_PALETTE))
        element.color = color
    links.new(info.outputs["Random"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.3
    try:
        bsdf.inputs["Coat Weight"].default_value = 0.6   # clear-coat car paint
    except KeyError:
        pass
