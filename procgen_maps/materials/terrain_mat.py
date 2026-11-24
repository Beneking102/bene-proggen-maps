"""Terrain shader: green ground with a countryside patchwork outside the
city, slope-driven rock, snow on real peaks, fine speckle and bump - all
procedural, no texture files.

The v0.9 shader mapped absolute height 0..max_height straight onto a
grass -> dirt -> snow ramp. With the gentler v0.8 terrain (max 22 m,
typical ground at 8-16 m) that put most of a city on the "dirt/snow"
half of the ramp, which is why every earlier render had a pale grey-green
ground that read as frost. Height now only adds a subtle variation on
top of grass; what actually turns the ground to rock is *slope* (the
geometry normal's z), which is where rock shows on real hillsides.

v0.15: the countryside is no longer a radial "meadow band" (which came
out as one pale beige plain) but a patchwork read from per-vertex
attributes the terrain generator writes from generators/landscape.py -
field cells with their own tone (meadow greens, a darker crop, a
ripening yellow-green, golden stubble, ploughed earth), dark hedgerow
lines on the cell borders, and the forest floor exactly under the tree
clumps. The same functions place the trees, so ground and vegetation
agree. Without the attributes (a terrain generated some other way, the
horizon skirt) the shader falls back to plain grass.
"""

TERRAIN_MATERIAL_NAME = "ProcgenMaps_Terrain"
_SNOW_NODE = "ProcgenMaps_SnowLine"                # Map Range over absolute height: the snow line in meters
_FIELD_RAMP_NODE = "ProcgenMaps_FieldTones"        # Color Ramp: field tone 0..1 -> field colour
SNOW_PERCENTILE = 92.0                             # share of a mountain heightmap below the snow line
SNOW_MIN_RELIEF = 40.0                             # meters of total relief a terrain needs before its peaks get snow

# Ground palette (scene-linear). Kept dark and saturated on purpose: the
# physical sky plus sun are bright and AgX desaturates highlights, so
# anything paler than this renders as beige.
GRASS_DARK = (0.018, 0.075, 0.012, 1.0)
GRASS_LIGHT = (0.040, 0.135, 0.022, 1.0)
GRASS_DRY = (0.095, 0.120, 0.034, 1.0)
HEDGE_COLOR = (0.014, 0.045, 0.010, 1.0)
FOREST_FLOOR_DARK = (0.045, 0.032, 0.018, 1.0)
FOREST_FLOOR_LIGHT = (0.095, 0.068, 0.036, 1.0)
# Field tones by the cell's random value: mostly meadow greens, some crops.
FIELD_TONES = (
    (0.00, (0.030, 0.105, 0.018, 1.0)),   # meadow
    (0.30, (0.048, 0.145, 0.026, 1.0)),   # lush pasture
    (0.48, (0.020, 0.075, 0.016, 1.0)),   # dark crop (maize, beet)
    (0.66, (0.095, 0.150, 0.030, 1.0)),   # ripening barley
    (0.82, (0.220, 0.165, 0.055, 1.0)),   # golden stubble
    (0.93, (0.085, 0.055, 0.032, 1.0)),   # ploughed earth
)


def snow_line_height(heights, percentile: float = SNOW_PERCENTILE) -> float:
    """The absolute height (meters) above which the terrain turns to snow:
    a percentile of the generated heightmap, so only the highest peaks
    are white whatever the noise's actual value range - and no snow at
    all (an infinite line) on gentle city terrain with less than
    SNOW_MIN_RELIEF meters of relief, where the "peaks" are plateaus a
    few meters above the streets. Pure numpy."""
    import math

    import numpy as np

    values = np.asarray(heights, dtype=np.float64)
    if float(values.max() - values.min()) < SNOW_MIN_RELIEF:
        return math.inf
    return float(np.percentile(values, percentile))


def set_terrain_biomes(city_radius: float, snow_z: float = None):
    """Retune the terrain material for the terrain that was just generated
    (the material is created once and shared): snow above `snow_z`
    meters (see snow_line_height). `city_radius` is accepted for the
    callers that still pass it; the field / forest bands come from the
    mesh attributes since v0.15 and need no retuning."""
    import bpy

    mat = bpy.data.materials.get(TERRAIN_MATERIAL_NAME)
    if mat is None or not mat.use_nodes:
        return
    snow = mat.node_tree.nodes.get(_SNOW_NODE)
    if snow is not None and snow_z is not None:
        snow_z = min(snow_z, 1.0e5)   # an infinite line (no snow) still needs finite node values
        snow.inputs["From Min"].default_value = snow_z - 1.5
        snow.inputs["From Max"].default_value = snow_z + 1.0


def get_or_create_terrain_material(max_height: float = 40.0, city_radius: float = 300.0):
    """The shared terrain material (created once, see the module docstring)."""
    import bpy

    from . import atmosphere

    existing = bpy.data.materials.get(TERRAIN_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(TERRAIN_MATERIAL_NAME)
    mat.diffuse_color = (0.12, 0.26, 0.09, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    def new(bl_idname, x, y):
        node = nodes.new(bl_idname)
        node.location = (x, y)
        return node

    def math(op, a, b=0.0, x=0, y=0):
        node = new("ShaderNodeMath", x, y)
        node.operation = op
        for index, value in ((0, a), (1, b)):
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]

    def map_range(value, from_min, from_max, to_min, to_max, x=0, y=0):
        node = new("ShaderNodeMapRange", x, y)
        links.new(value, node.inputs["Value"])
        node.inputs["From Min"].default_value = from_min
        node.inputs["From Max"].default_value = from_max
        node.inputs["To Min"].default_value = to_min
        node.inputs["To Max"].default_value = to_max
        return node.outputs["Result"]

    def mix_color(fac, color_a, color_b, x=0, y=0):
        node = new("ShaderNodeMix", x, y)
        node.data_type = 'RGBA'
        node.blend_type = 'MIX'
        if isinstance(fac, (int, float)):
            node.inputs[0].default_value = fac
        else:
            links.new(fac, node.inputs[0])
        for index, value in ((6, color_a), (7, color_b)):
            if isinstance(value, tuple):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[2]

    def noise(vector, scale, detail=2.0, roughness=0.5, x=0, y=0):
        node = new("ShaderNodeTexNoise", x, y)
        node.inputs["Scale"].default_value = scale
        node.inputs["Detail"].default_value = detail
        node.inputs["Roughness"].default_value = roughness
        links.new(vector, node.inputs["Vector"])
        return node.outputs["Fac"]

    def attribute(name, x, y):
        node = new("ShaderNodeAttribute", x, y)
        node.attribute_type = 'GEOMETRY'
        node.attribute_name = name
        return node.outputs["Fac"]

    output = new("ShaderNodeOutputMaterial", 1300, 0)
    bsdf = new("ShaderNodeBsdfPrincipled", 1000, 0)
    bsdf.inputs["Specular IOR Level"].default_value = 0.15
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    geometry = new("ShaderNodeNewGeometry", -1400, 300)
    position = geometry.outputs["Position"]
    separate = new("ShaderNodeSeparateXYZ", -1200, 300)
    links.new(position, separate.inputs["Vector"])
    normal_z = new("ShaderNodeSeparateXYZ", -1200, 100)
    links.new(geometry.outputs["Normal"], normal_z.inputs["Vector"])

    # Grass: two greens blended by a broad noise (meadow patchiness) plus a
    # fine speckle, and a few drier olive patches from a very large noise.
    patch = noise(position, 0.02, detail=3.0, x=-1000, y=-100)
    speckle = noise(position, 2.5, detail=5.0, roughness=0.6, x=-1000, y=-300)
    dry = map_range(noise(position, 0.006, detail=2.0, x=-1000, y=-500), 0.68, 0.88, 0.0, 0.35, x=-800, y=-500)
    grass = mix_color(patch, GRASS_DARK, GRASS_LIGHT, x=-600, y=-100)
    speckle_tone = map_range(speckle, 0.0, 1.0, 0.84, 1.16, x=-600, y=-300)
    grass = mix_color(dry, grass, GRASS_DRY, x=-400, y=-100)

    # Countryside patchwork from the landscape attributes (see the module
    # docstring): field cells in their own tone, hedgerow lines, forest
    # floor under the tree clumps. A tone of 0 everywhere (no attributes)
    # leaves the plain grass.
    field_fac = attribute("procgen_maps_field", -1000, 900)
    field_tone = attribute("procgen_maps_field_tone", -1000, 750)
    hedge_fac = attribute("procgen_maps_hedge", -1000, 600)
    forest_fac = attribute("procgen_maps_forest", -1000, 450)
    ramp = new("ShaderNodeValToRGB", -800, 750)
    ramp.name = _FIELD_RAMP_NODE
    ramp.color_ramp.interpolation = 'CONSTANT'
    elements = ramp.color_ramp.elements
    while len(elements) > 1:
        elements.remove(elements[-1])
    elements[0].position = FIELD_TONES[0][0]
    elements[0].color = FIELD_TONES[0][1]
    for position_value, color in FIELD_TONES[1:]:
        element = elements.new(position_value)
        element.color = color
    links.new(field_tone, ramp.inputs["Fac"])
    # A little of the grass patchiness inside every field so it is not a flat fill.
    field_color = mix_color(map_range(patch, 0.0, 1.0, -0.12, 0.12, x=-600, y=850), ramp.outputs["Color"],
                            GRASS_LIGHT, x=-400, y=800)
    ground = mix_color(field_fac, grass, field_color, x=-200, y=700)
    ground = mix_color(math('MULTIPLY', hedge_fac, 0.85, x=-400, y=600), ground, HEDGE_COLOR, x=-50, y=600)
    litter = mix_color(speckle, FOREST_FLOOR_DARK, FOREST_FLOOR_LIGHT, x=-400, y=450)
    forest_mix = map_range(forest_fac, 0.55, 0.95, 0.0, 0.8, x=-400, y=350)
    ground = mix_color(forest_mix, ground, litter, x=100, y=500)

    # Height adds a gentle cool/dark shift toward the top of the field.
    height_fac = map_range(separate.outputs["Z"], 0.0, max(max_height, 1.0), 0.0, 1.0, x=-1000, y=300)
    ground = mix_color(math('MULTIPLY', height_fac, 0.14, x=-800, y=300), ground, (0.12, 0.15, 0.08, 1.0),
                       x=250, y=300)

    # Rock on slopes: normal.z below ~0.93 (~21 degrees) starts showing rock.
    slope = map_range(normal_z.outputs["Z"], 0.80, 0.94, 1.0, 0.0, x=-1000, y=100)
    rock_noise = noise(position, 0.6, detail=4.0, x=-800, y=-700)
    rock = mix_color(rock_noise, (0.20, 0.18, 0.16, 1.0), (0.38, 0.34, 0.30, 1.0), x=-400, y=-700)
    ground = mix_color(slope, ground, rock, x=400, y=100)

    # Snow on the high, flat ground (the peaks): above the snow line
    # (absolute meters, set per terrain by set_terrain_biomes from the
    # heightmap's own distribution), fading in over a noisy band so the
    # line is ragged; steeper ground stays rock.
    snow_node = new("ShaderNodeMapRange", -800, 1100)
    snow_node.name = _SNOW_NODE
    links.new(separate.outputs["Z"], snow_node.inputs["Value"])
    snow_node.inputs["From Min"].default_value = max_height * 0.95 - 1.5
    snow_node.inputs["From Max"].default_value = max_height * 0.95 + 1.0
    snow_fac = math('MULTIPLY', snow_node.outputs["Result"],
                    map_range(normal_z.outputs["Z"], 0.75, 0.9, 0.0, 1.0, x=-800, y=1250), x=-600, y=1150)
    snow_fac = math('MULTIPLY', snow_fac, map_range(patch, 0.2, 0.8, 0.6, 1.0, x=-600, y=1300), x=-400, y=1150)
    ground = mix_color(snow_fac, ground, (0.85, 0.88, 0.92, 1.0), x=550, y=300)

    tone = new("ShaderNodeMix", 750, 100)
    tone.data_type = 'RGBA'
    tone.blend_type = 'MULTIPLY'
    tone.inputs[0].default_value = 1.0
    links.new(ground, tone.inputs[6])
    speckle_rgb = new("ShaderNodeCombineColor", 550, -300)
    for index in range(3):
        links.new(speckle_tone, speckle_rgb.inputs[index])
    links.new(speckle_rgb.outputs["Color"], tone.inputs[7])
    links.new(tone.outputs[2], bsdf.inputs["Base Color"])

    links.new(map_range(rock_noise, 0.0, 1.0, 0.80, 0.96, x=550, y=-500), bsdf.inputs["Roughness"])

    bump_noise = noise(position, 12.0, detail=6.0, roughness=0.65, x=350, y=-700)
    bump = new("ShaderNodeBump", 750, -600)
    bump.inputs["Strength"].default_value = 0.22
    bump.inputs["Distance"].default_value = 0.15
    links.new(bump_noise, bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    atmosphere.apply_haze(mat)
    return mat
