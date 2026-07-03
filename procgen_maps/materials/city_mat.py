"""Shared, parametrized building-facade shader, plus the window-glass material.

`get_or_create_city_material` (mesh material slot 0, the walls) serves all 12
facade types (generators.city.buildings.FACADE_TYPES): an Attribute node reads
each building's `procgen_maps_material_index` custom property and drives a
12-stop ColorRamp for the base facade color, so material count stays flat
regardless of city size (see OPTIMIZATION.md). A second Attribute node reads
`procgen_maps_tint` (a per-building random 0..1 value) and multiplies it into
the facade color as a +-15% brightness variation, so buildings sharing the
same facade type don't look like identical copies.

`get_or_create_window_material` (slot 1) is assigned to the actual recessed
window-pane faces carved by generators.city.buildings._add_window_row, and
carries the emissive night-mode glow - since real geometric windows exist
now, the glow belongs on them rather than faked via noise on the wall
material. Both materials' NIGHT_MODE_NODE_NAME value node are toggled
together by `set_night_mode`, which ui/operators.py's night-mode toggle calls.
"""

CITY_MATERIAL_NAME = "ProcgenMaps_CityFacade"
WINDOW_MATERIAL_NAME = "ProcgenMaps_CityWindow"
NIGHT_MODE_NODE_NAME = "ProcgenMaps_NightModeFactor"

_FACADE_COLORS = (
    (0.55, 0.75, 0.85, 1.0),  # glass_tower
    (0.65, 0.65, 0.68, 1.0),  # office_block
    (0.55, 0.35, 0.28, 1.0),  # brick_commercial
    (0.75, 0.72, 0.65, 1.0),  # apartment_slab
    (0.70, 0.68, 0.60, 1.0),  # apartment_tower
    (0.80, 0.60, 0.45, 1.0),  # townhouse
    (0.85, 0.75, 0.55, 1.0),  # cottage
    (0.90, 0.85, 0.30, 1.0),  # shopfront
    (0.50, 0.50, 0.45, 1.0),  # warehouse
    (0.45, 0.45, 0.42, 1.0),  # factory_hall
    (0.40, 0.40, 0.40, 1.0),  # industrial_tower
    (0.60, 0.55, 0.70, 1.0),  # mixed_use
)


def get_or_create_city_material():
    import bpy

    existing = bpy.data.materials.get(CITY_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(CITY_MATERIAL_NAME)
    mat.diffuse_color = (0.62, 0.60, 0.55, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (800, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (500, 0)
    bsdf.inputs["Roughness"].default_value = 0.75
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    attribute = nodes.new("ShaderNodeAttribute")
    attribute.location = (-700, 250)
    attribute.attribute_type = 'OBJECT'
    attribute.attribute_name = "procgen_maps_material_index"

    divide = nodes.new("ShaderNodeMath")
    divide.location = (-500, 250)
    divide.operation = 'DIVIDE'
    divide.inputs[1].default_value = float(max(1, len(_FACADE_COLORS) - 1))
    links.new(attribute.outputs["Fac"], divide.inputs[0])

    color_ramp = nodes.new("ShaderNodeValToRGB")
    color_ramp.location = (-200, 250)
    color_ramp.color_ramp.interpolation = 'CONSTANT'
    _configure_facade_ramp(color_ramp)
    links.new(divide.outputs["Value"], color_ramp.inputs["Fac"])

    tint_attribute = nodes.new("ShaderNodeAttribute")
    tint_attribute.location = (-700, 450)
    tint_attribute.attribute_type = 'OBJECT'
    tint_attribute.attribute_name = "procgen_maps_tint"

    tint_remap = nodes.new("ShaderNodeMapRange")
    tint_remap.location = (-500, 450)
    tint_remap.inputs["To Min"].default_value = 0.85
    tint_remap.inputs["To Max"].default_value = 1.15
    links.new(tint_attribute.outputs["Fac"], tint_remap.inputs["Value"])

    tint_color = nodes.new("ShaderNodeCombineColor")
    tint_color.location = (-300, 450)
    links.new(tint_remap.outputs["Result"], tint_color.inputs[0])
    links.new(tint_remap.outputs["Result"], tint_color.inputs[1])
    links.new(tint_remap.outputs["Result"], tint_color.inputs[2])

    # ShaderNodeMix has separate hidden A/B/Result sockets per data_type - by
    # name, "A"/"B"/"Result" resolve to the first (Float) variant, not RGBA,
    # so the RGBA sockets must be addressed by index (6/7 in, 2 out).
    tint_mix = nodes.new("ShaderNodeMix")
    tint_mix.location = (0, 300)
    tint_mix.data_type = 'RGBA'
    tint_mix.blend_type = 'MULTIPLY'
    tint_mix.inputs[0].default_value = 1.0
    links.new(color_ramp.outputs["Color"], tint_mix.inputs[6])
    links.new(tint_color.outputs["Color"], tint_mix.inputs[7])
    links.new(tint_mix.outputs[2], bsdf.inputs["Base Color"])

    return mat


def get_or_create_window_material():
    """The material assigned to recessed window-pane faces (mesh slot 1) -
    see generators/city/buildings.py's _add_window_row, which sets
    face.material_index = 1 on the pane it carves."""
    import bpy

    existing = bpy.data.materials.get(WINDOW_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(WINDOW_MATERIAL_NAME)
    mat.diffuse_color = (0.12, 0.18, 0.24, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (100, 0)
    bsdf.inputs["Base Color"].default_value = (0.10, 0.16, 0.22, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.15
    bsdf.inputs["Emission Color"].default_value = (1.0, 0.85, 0.5, 1.0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    night_value = nodes.new("ShaderNodeValue")
    night_value.location = (-200, -200)
    night_value.name = NIGHT_MODE_NODE_NAME
    night_value.label = "Night Mode"
    night_value.outputs[0].default_value = 0.0
    links.new(night_value.outputs["Value"], bsdf.inputs["Emission Strength"])

    return mat


def _configure_facade_ramp(color_ramp):
    elements = color_ramp.color_ramp.elements
    elements[0].position = 0.0
    elements[0].color = _FACADE_COLORS[0]
    count = len(_FACADE_COLORS)
    for index, color in enumerate(_FACADE_COLORS[1:], start=1):
        element = elements.new(index / (count - 1))
        element.color = color


def set_night_mode(enabled: bool):
    """Toggle the window material's emissive glow on/off."""
    import bpy

    mat = bpy.data.materials.get(WINDOW_MATERIAL_NAME)
    if mat is None or not mat.use_nodes:
        return
    node = mat.node_tree.nodes.get(NIGHT_MODE_NODE_NAME)
    if node is not None:
        node.outputs[0].default_value = 1.0 if enabled else 0.0
