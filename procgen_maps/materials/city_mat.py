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

The wall material also mixes in a per-object noise-driven "grain" (using
Object-space coordinates, so the pattern is stable per building rather than
flickering with world position) for subtle concrete/panel variation on top
of the flat facade color, plus a matching Bump for micro-surface detail -
both pure shading tricks, no extra geometry or texture files needed.
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
    (0.85, 0.85, 0.90, 1.0),  # supermarket (neutral shell - the sign carries the branding color)
    (0.75, 0.78, 0.85, 1.0),  # police_station
    (0.92, 0.92, 0.90, 1.0),  # hospital
    (0.42, 0.20, 0.18, 1.0),  # fire_station
    (0.80, 0.72, 0.52, 1.0),  # school
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

    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-700, -150)

    grain_noise = nodes.new("ShaderNodeTexNoise")
    grain_noise.location = (-500, -150)
    grain_noise.inputs["Scale"].default_value = 25.0
    grain_noise.inputs["Detail"].default_value = 5.0
    links.new(tex_coord.outputs["Object"], grain_noise.inputs["Vector"])

    grain_remap = nodes.new("ShaderNodeMapRange")
    grain_remap.location = (-300, -150)
    grain_remap.inputs["To Min"].default_value = 0.9
    grain_remap.inputs["To Max"].default_value = 1.1
    links.new(grain_noise.outputs["Fac"], grain_remap.inputs["Value"])

    grain_color = nodes.new("ShaderNodeCombineColor")
    grain_color.location = (-100, -150)
    links.new(grain_remap.outputs["Result"], grain_color.inputs[0])
    links.new(grain_remap.outputs["Result"], grain_color.inputs[1])
    links.new(grain_remap.outputs["Result"], grain_color.inputs[2])

    grain_mix = nodes.new("ShaderNodeMix")
    grain_mix.location = (200, 150)
    grain_mix.data_type = 'RGBA'
    grain_mix.blend_type = 'MULTIPLY'
    grain_mix.inputs[0].default_value = 1.0
    links.new(tint_mix.outputs[2], grain_mix.inputs[6])
    links.new(grain_color.outputs["Color"], grain_mix.inputs[7])
    links.new(grain_mix.outputs[2], bsdf.inputs["Base Color"])

    bump = nodes.new("ShaderNodeBump")
    bump.location = (300, -150)
    bump.inputs["Strength"].default_value = 0.1
    bump.inputs["Distance"].default_value = 0.2
    links.new(grain_noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    return mat


def get_or_create_window_material():
    """The material assigned to recessed window-pane faces (mesh slot 1) -
    see generators/city/buildings.py's _add_window_row, which sets
    face.material_index = 1 on the pane it carves.

    Transmission Weight = 1 makes this actual see-through glass (so the
    generated building interiors - generators/city/buildings.py's
    _build_ground_floor_interior - are visible from outside), rather than a
    solid tinted panel. Requires EEVEE Next's raytracing (scene.eevee.
    use_raytracing = True) to actually render as transparent - without it,
    EEVEE falls back to treating it as opaque."""
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
    bsdf.inputs["Base Color"].default_value = (0.55, 0.65, 0.70, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.05
    bsdf.inputs["Transmission Weight"].default_value = 0.92
    bsdf.inputs["IOR"].default_value = 1.45
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
    """Toggle the window material's emissive glow on/off.

    Deliberately modest (0.25, not 1.0): this material is shared by every
    window on every floor, including upper floors that have no modeled
    interior behind them, so it needs *some* emission for those to read as
    "lit" at night - but at full strength it blows out the transmission
    view of the real ground-floor interiors (generators/city/buildings.py's
    _build_ground_floor_interior), which then defeats the point of having
    built them."""
    import bpy

    mat = bpy.data.materials.get(WINDOW_MATERIAL_NAME)
    if mat is None or not mat.use_nodes:
        return
    node = mat.node_tree.nodes.get(NIGHT_MODE_NODE_NAME)
    if node is not None:
        node.outputs[0].default_value = 0.25 if enabled else 0.0
