"""Terrain shader: grass-dominant ground with slope-driven rock, dry
patches, fine speckle and bump - all procedural, no texture files.

The v0.9 shader mapped absolute height 0..max_height straight onto a
grass -> dirt -> snow ramp. With the gentler v0.8 terrain (max 22 m,
typical ground at 8-16 m) that put most of a city on the "dirt/snow"
half of the ramp, which is why every earlier render had a pale grey-green
ground that read as frost. Height now only adds a subtle variation on
top of grass; what actually turns the ground to rock is *slope* (the
geometry normal's z), which is where rock shows on real hillsides.
"""

TERRAIN_MATERIAL_NAME = "ProcgenMaps_Terrain"
_FOREST_FLOOR_NODE = "ProcgenMaps_ForestFloor"     # Map Range over the radial distance: 0 at the city edge, 1 in the forest
_MEADOW_NODE = "ProcgenMaps_Meadow"                # Map Range: the drier meadow band just outside the city
_SNOW_NODE = "ProcgenMaps_SnowLine"                # Map Range over absolute height: the snow line in meters
SNOW_PERCENTILE = 92.0                             # share of a mountain heightmap below the snow line
SNOW_MIN_RELIEF = 40.0                             # meters of total relief a terrain needs before its peaks get snow


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
    """Retune the biome bands for the terrain/city that was just generated
    (the material is created once and shared): meadow from the city edge
    to 1.2 x radius, forest floor from 1.15 x radius outwards, snow
    above `snow_z` meters (see snow_line_height)."""
    import bpy

    mat = bpy.data.materials.get(TERRAIN_MATERIAL_NAME)
    if mat is None or not mat.use_nodes:
        return
    nodes = mat.node_tree.nodes
    forest = nodes.get(_FOREST_FLOOR_NODE)
    meadow = nodes.get(_MEADOW_NODE)
    snow = nodes.get(_SNOW_NODE)
    if snow is not None and snow_z is not None:
        snow_z = min(snow_z, 1.0e5)   # an infinite line (no snow) still needs finite node values
        snow.inputs["From Min"].default_value = snow_z - 1.5
        snow.inputs["From Max"].default_value = snow_z + 1.0
    if forest is not None:
        forest.inputs["From Min"].default_value = city_radius * 1.15
        forest.inputs["From Max"].default_value = city_radius * 1.6
    if meadow is not None:
        meadow.inputs["From Min"].default_value = city_radius * 0.95
        meadow.inputs["From Max"].default_value = city_radius * 1.2


def get_or_create_terrain_material(max_height: float = 40.0, city_radius: float = 300.0):
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

    output = new("ShaderNodeOutputMaterial", 1100, 0)
    bsdf = new("ShaderNodeBsdfPrincipled", 800, 0)
    bsdf.inputs["Specular IOR Level"].default_value = 0.2
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    geometry = new("ShaderNodeNewGeometry", -1400, 300)
    position = geometry.outputs["Position"]
    separate = new("ShaderNodeSeparateXYZ", -1200, 300)
    links.new(position, separate.inputs["Vector"])
    normal_z = new("ShaderNodeSeparateXYZ", -1200, 100)
    links.new(geometry.outputs["Normal"], normal_z.inputs["Vector"])

    # Grass: two greens blended by a broad noise (meadow patchiness) plus a
    # fine speckle, and dry straw-colored patches from a very large noise.
    patch = noise(position, 0.02, detail=3.0, x=-1000, y=-100)
    speckle = noise(position, 2.5, detail=5.0, roughness=0.6, x=-1000, y=-300)
    dry = map_range(noise(position, 0.006, detail=2.0, x=-1000, y=-500), 0.66, 0.86, 0.0, 0.45, x=-800, y=-500)
    grass = mix_color(patch, (0.055, 0.165, 0.035, 1.0), (0.11, 0.26, 0.06, 1.0), x=-600, y=-100)
    grass = mix_color(map_range(speckle, 0.0, 1.0, -0.12, 0.12, x=-600, y=-300), grass, (0.16, 0.30, 0.08, 1.0),
                      x=-400, y=-100) if False else grass
    speckle_tone = map_range(speckle, 0.0, 1.0, 0.82, 1.18, x=-600, y=-300)
    grass = mix_color(dry, grass, (0.36, 0.33, 0.15, 1.0), x=-400, y=-100)

    # Biomes by distance from the city (v0.14): the meadow band just
    # outside the city is drier and lighter, the forest ring's floor is
    # darker and browner with leaf litter. Both Map Range nodes are
    # retuned per preset by set_terrain_biomes.
    radial = math('SQRT', math('ADD', math('MULTIPLY', separate.outputs["X"], separate.outputs["X"], x=-1200, y=600),
                               math('MULTIPLY', separate.outputs["Y"], separate.outputs["Y"], x=-1200, y=500),
                               x=-1050, y=550), x=-950, y=550)
    meadow_node = new("ShaderNodeMapRange", -800, 650)
    meadow_node.name = _MEADOW_NODE
    links.new(radial, meadow_node.inputs["Value"])
    meadow_node.inputs["From Min"].default_value = city_radius * 0.95
    meadow_node.inputs["From Max"].default_value = city_radius * 1.2
    meadow_band = math('SUBTRACT', meadow_node.outputs["Result"], 0.0, x=-600, y=650)
    forest_node = new("ShaderNodeMapRange", -800, 450)
    forest_node.name = _FOREST_FLOOR_NODE
    links.new(radial, forest_node.inputs["Value"])
    forest_node.inputs["From Min"].default_value = city_radius * 1.15
    forest_node.inputs["From Max"].default_value = city_radius * 1.6
    # Meadow: hay-coloured patches blended by a mid-scale noise.
    meadow_mix = math('MULTIPLY', meadow_band, map_range(patch, 0.3, 0.7, 0.15, 0.85, x=-600, y=750), x=-400, y=700)
    grass = mix_color(meadow_mix, grass, (0.24, 0.30, 0.09, 1.0), x=-200, y=700)
    # Forest floor: dark humus with leaf-litter speckle, keeps a little grass.
    litter = mix_color(speckle, (0.11, 0.08, 0.05, 1.0), (0.22, 0.16, 0.09, 1.0), x=-400, y=500)
    grass = mix_color(math('MULTIPLY', forest_node.outputs["Result"], 0.9, x=-400, y=400), grass, litter,
                      x=-200, y=500)

    # Height adds a gentle cool/dark shift toward the top of the field.
    height_fac = map_range(separate.outputs["Z"], 0.0, max(max_height, 1.0), 0.0, 1.0, x=-1000, y=300)
    ground = mix_color(math('MULTIPLY', height_fac, 0.18, x=-800, y=300), grass, (0.20, 0.22, 0.14, 1.0),
                       x=-200, y=100)

    # Rock on slopes: normal.z below ~0.93 (~21 degrees) starts showing rock.
    slope = map_range(normal_z.outputs["Z"], 0.80, 0.94, 1.0, 0.0, x=-1000, y=100)
    rock_noise = noise(position, 0.6, detail=4.0, x=-800, y=-700)
    rock = mix_color(rock_noise, (0.22, 0.20, 0.18, 1.0), (0.42, 0.38, 0.33, 1.0), x=-400, y=-700)
    ground = mix_color(slope, ground, rock, x=100, y=100)

    # Snow on the high, flat ground (the peaks): above the snow line
    # (absolute meters, set per terrain by set_terrain_biomes from the
    # heightmap's own distribution), fading in over a noisy band so the
    # line is ragged; steeper ground stays rock.
    snow_node = new("ShaderNodeMapRange", -800, 900)
    snow_node.name = _SNOW_NODE
    links.new(separate.outputs["Z"], snow_node.inputs["Value"])
    snow_node.inputs["From Min"].default_value = max_height * 0.95 - 1.5
    snow_node.inputs["From Max"].default_value = max_height * 0.95 + 1.0
    snow_fac = math('MULTIPLY', snow_node.outputs["Result"],
                    map_range(normal_z.outputs["Z"], 0.75, 0.9, 0.0, 1.0, x=-800, y=1000), x=-600, y=950)
    snow_fac = math('MULTIPLY', snow_fac, map_range(patch, 0.2, 0.8, 0.6, 1.0, x=-600, y=1050), x=-400, y=950)
    ground = mix_color(snow_fac, ground, (0.85, 0.88, 0.92, 1.0), x=300, y=300)

    tone = new("ShaderNodeMix", 300, 100)
    tone.data_type = 'RGBA'
    tone.blend_type = 'MULTIPLY'
    tone.inputs[0].default_value = 1.0
    links.new(ground, tone.inputs[6])
    speckle_rgb = new("ShaderNodeCombineColor", 100, -300)
    for index in range(3):
        links.new(speckle_tone, speckle_rgb.inputs[index])
    links.new(speckle_rgb.outputs["Color"], tone.inputs[7])
    links.new(tone.outputs[2], bsdf.inputs["Base Color"])

    links.new(map_range(rock_noise, 0.0, 1.0, 0.78, 0.95, x=300, y=-500), bsdf.inputs["Roughness"])

    bump_noise = noise(position, 12.0, detail=6.0, roughness=0.65, x=100, y=-700)
    bump = new("ShaderNodeBump", 500, -600)
    bump.inputs["Strength"].default_value = 0.25
    bump.inputs["Distance"].default_value = 0.15
    links.new(bump_noise, bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    atmosphere.apply_haze(mat)
    return mat
