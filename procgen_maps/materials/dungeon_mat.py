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
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = _ROUGHNESS_OVERRIDES.get(key, 0.8)
        emission_strength = _EMISSION_STRENGTH.get(key, 0.0)
        if emission_strength > 0.0:
            bsdf.inputs["Emission Color"].default_value = color
            bsdf.inputs["Emission Strength"].default_value = emission_strength
    return mat
