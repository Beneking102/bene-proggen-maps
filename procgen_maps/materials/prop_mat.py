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


def get_or_create_prop_material(key: str):
    import bpy

    name = f"ProcgenMaps_Prop_{key}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    color = _COLORS[key]
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = _ROUGHNESS_OVERRIDES.get(key, 0.75)
    return mat
