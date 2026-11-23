"""Road asphalt (with painted lane markings, crosswalks and wear) and
sidewalk/curb concrete materials.

Both are single shared materials driven entirely by per-mesh data that
generators/city/streets.py writes while building each road strip - no
texture files, no per-object material copies:

- UV map ROAD_UV_NAME: `u` = meters along the strip, `v` = signed meters
  across it (0 at the centerline). Markings are pure functions of (u, v):
  a dashed center line (local streets) or a double solid yellow center
  line plus dashed lane dividers (arterials), solid edge lines, and
  zebra stripes near intersections.
- UV map ROAD_END_UV_NAME: `u`/`v` = distance (meters) from this point to
  the strip's start/end node, already minus the intersection fan radius,
  or a large sentinel for ends that are not intersections. The crosswalk
  band lives at 0.4..3.2 m of whichever end is nearer.
- Per-face float attributes `procgen_maps_road_width` (meters) and
  `procgen_maps_road_lanes` (1 = local, 2 = arterial), read with Attribute
  nodes in GEOMETRY mode (the whole network is one mesh object).

Sidewalk faces (same mesh, slot 1) reuse ROAD_UV_NAME's meter-scaled
coordinates for a paving-slab grid.
"""

ROAD_MATERIAL_NAME = "ProcgenMaps_RoadAsphalt"
SIDEWALK_MATERIAL_NAME = "ProcgenMaps_Sidewalk"
ROAD_UV_NAME = "ProcgenMaps_Road"
ROAD_END_UV_NAME = "ProcgenMaps_RoadEnds"
ROAD_END_SENTINEL = 1000.0   # "no crosswalk at this end" (not an intersection)
NO_MARKINGS_V = 1000.0       # across-coordinate used by intersection fans: outside every marking band

DASH_PERIOD = 6.0            # meters; 3 m painted, 3 m gap
CROSSWALK_START = 0.4        # meters past the fan edge
CROSSWALK_END = 3.2
CROSSWALK_STRIPE = 1.0       # meters; 0.5 painted, 0.5 gap

from . import atmosphere  # noqa: E402  (pure module, no bpy at import time)


class _NodeBuilder:
    """Tiny helper so the marking logic below reads like arithmetic instead
    of thirty near-identical nodes.new()/links.new() blocks."""

    def __init__(self, node_tree):
        self.nodes = node_tree.nodes
        self.links = node_tree.links
        self._x = -2200
        self._y = 0

    def _place(self, node):
        node.location = (self._x, self._y)
        self._y -= 180
        if self._y < -2600:
            self._y = 0
            self._x += 220
        return node

    def _plug(self, socket, value):
        if isinstance(value, (int, float)):
            socket.default_value = float(value)
        else:
            self.links.new(value, socket)

    def math(self, op, a, b=0.0, c=None):
        node = self._place(self.nodes.new("ShaderNodeMath"))
        node.operation = op
        self._plug(node.inputs[0], a)
        self._plug(node.inputs[1], b)
        if c is not None:
            self._plug(node.inputs[2], c)
        return node.outputs[0]

    def new(self, bl_idname):
        return self._place(self.nodes.new(bl_idname))

    def attribute(self, name):
        node = self.new("ShaderNodeAttribute")
        # Per-face mesh attributes (generators/city/streets.py writes them),
        # not object properties: the whole network is one object since v0.12.
        node.attribute_type = 'GEOMETRY'
        node.attribute_name = name
        return node.outputs["Fac"]

    def uv(self, name):
        node = self.new("ShaderNodeUVMap")
        node.uv_map = name
        sep = self.new("ShaderNodeSeparateXYZ")
        self.links.new(node.outputs["UV"], sep.inputs["Vector"])
        return sep.outputs["X"], sep.outputs["Y"]

    def noise(self, vector, scale, detail=2.0, roughness=0.5):
        node = self.new("ShaderNodeTexNoise")
        node.inputs["Scale"].default_value = scale
        node.inputs["Detail"].default_value = detail
        node.inputs["Roughness"].default_value = roughness
        if vector is not None:
            self.links.new(vector, node.inputs["Vector"])
        return node.outputs["Fac"]

    def mix_color(self, fac, color_a, color_b):
        node = self.new("ShaderNodeMix")
        node.data_type = 'RGBA'
        node.blend_type = 'MIX'
        self._plug(node.inputs[0], fac)
        for index, value in ((6, color_a), (7, color_b)):
            if isinstance(value, tuple):
                node.inputs[index].default_value = value
            else:
                self.links.new(value, node.inputs[index])
        return node.outputs[2]

    def map_range(self, value, from_min, from_max, to_min, to_max):
        node = self.new("ShaderNodeMapRange")
        self._plug(node.inputs["Value"], value)
        node.inputs["From Min"].default_value = from_min
        node.inputs["From Max"].default_value = from_max
        node.inputs["To Min"].default_value = to_min
        node.inputs["To Max"].default_value = to_max
        return node.outputs["Result"]

    def band(self, value, center, half_width):
        """1 where |value - center| < half_width, else 0."""
        return self.math('LESS_THAN', self.math('ABSOLUTE', self.math('SUBTRACT', value, center)), half_width)


def get_or_create_road_material():
    import bpy

    existing = bpy.data.materials.get(ROAD_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(ROAD_MATERIAL_NAME)
    mat.diffuse_color = (0.05, 0.05, 0.055, 1.0)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    nb = _NodeBuilder(tree)

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    bsdf.inputs["Specular IOR Level"].default_value = 0.35
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    u, v = nb.uv(ROAD_UV_NAME)
    end_a, end_b = nb.uv(ROAD_END_UV_NAME)
    width = nb.attribute("procgen_maps_road_width")
    lanes = nb.attribute("procgen_maps_road_lanes")
    is_arterial = nb.math('GREATER_THAN', lanes, 1.5)
    abs_v = nb.math('ABSOLUTE', v)
    half_width = nb.math('MULTIPLY', width, 0.5)

    dash = nb.math('LESS_THAN', nb.math('FRACT', nb.math('DIVIDE', u, DASH_PERIOD)), 0.5)

    center_local = nb.math('MULTIPLY', nb.math('LESS_THAN', abs_v, 0.07), dash)
    center_arterial = nb.band(abs_v, 0.17, 0.06)
    center = nb.math('ADD', nb.math('MULTIPLY', center_local, nb.math('SUBTRACT', 1.0, is_arterial)),
                     nb.math('MULTIPLY', center_arterial, is_arterial))

    lane_line = nb.math('MULTIPLY', nb.math('MULTIPLY', nb.band(abs_v, nb.math('MULTIPLY', width, 0.25), 0.06), dash),
                        is_arterial)
    edge_line = nb.band(abs_v, nb.math('SUBTRACT', half_width, 0.3), 0.06)

    end_distance = nb.math('MINIMUM', end_a, end_b)
    in_band = nb.math('MULTIPLY', nb.math('GREATER_THAN', end_distance, CROSSWALK_START),
                      nb.math('LESS_THAN', end_distance, CROSSWALK_END))
    stripe = nb.math('LESS_THAN', nb.math('FRACT', nb.math('DIVIDE', nb.math('ADD', v, 500.0), CROSSWALK_STRIPE)), 0.5)
    inside_road = nb.math('LESS_THAN', abs_v, nb.math('SUBTRACT', half_width, 0.25))
    crosswalk = nb.math('MULTIPLY', nb.math('MULTIPLY', in_band, stripe), inside_road)

    lines = nb.math('MULTIPLY', nb.math('MAXIMUM', nb.math('MAXIMUM', center, lane_line), edge_line),
                    nb.math('SUBTRACT', 1.0, in_band))
    markings = nb.math('MAXIMUM', lines, crosswalk)

    # Paint wear: large-scale noise along the strip thins the markings so
    # they don't read as freshly printed everywhere.
    tex_coord = nb.new("ShaderNodeTexCoord")
    wear = nb.map_range(nb.noise(tex_coord.outputs["Object"], 0.08, detail=3.0), 0.3, 0.7, 0.25, 1.0)
    markings = nb.math('MULTIPLY', markings, wear)

    yellow_mask = nb.math('MULTIPLY', center_arterial, is_arterial)
    marking_color = nb.mix_color(yellow_mask, (0.80, 0.80, 0.74, 1.0), (0.85, 0.62, 0.12, 1.0))

    # Asphalt: fine aggregate grain + broad patchiness, slightly darker
    # where tires run (two bands either side of the center line).
    grain = nb.noise(tex_coord.outputs["Object"], 4.0, detail=6.0, roughness=0.7)
    patches = nb.noise(tex_coord.outputs["Object"], 0.12, detail=2.0)
    tone = nb.math('MULTIPLY', nb.map_range(grain, 0.0, 1.0, 0.75, 1.25), nb.map_range(patches, 0.0, 1.0, 0.8, 1.2))
    tire = nb.math('MULTIPLY', nb.band(abs_v, nb.math('MULTIPLY', width, 0.28), 0.55), 0.18)
    tone = nb.math('MULTIPLY', tone, nb.math('SUBTRACT', 1.0, tire))
    asphalt_color = nb.mix_color(nb.map_range(tone, 0.5, 1.5, 0.0, 1.0),
                                 (0.030, 0.030, 0.033, 1.0), (0.085, 0.085, 0.09, 1.0))

    base_color = nb.mix_color(markings, asphalt_color, marking_color)
    tree.links.new(base_color, bsdf.inputs["Base Color"])

    # Paint is smoother than aggregate: lerp roughness toward 0.55 on markings.
    asphalt_roughness = nb.map_range(grain, 0.0, 1.0, 0.68, 0.9)
    roughness = nb.math('ADD', nb.math('MULTIPLY', asphalt_roughness, nb.math('SUBTRACT', 1.0, markings)),
                        nb.math('MULTIPLY', markings, 0.55))
    tree.links.new(roughness, bsdf.inputs["Roughness"])

    bump = nb.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.02
    tree.links.new(grain, bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    atmosphere.apply_haze(mat)
    return mat


def get_or_create_sidewalk_material():
    import bpy

    existing = bpy.data.materials.get(SIDEWALK_MATERIAL_NAME)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(SIDEWALK_MATERIAL_NAME)
    mat.diffuse_color = (0.55, 0.54, 0.52, 1.0)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    nb = _NodeBuilder(tree)

    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    bsdf.inputs["Specular IOR Level"].default_value = 0.3
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    u, v = nb.uv(ROAD_UV_NAME)
    slab = 1.5
    joint_u = nb.math('LESS_THAN', nb.math('FRACT', nb.math('DIVIDE', u, slab)), 0.03)
    joint_v = nb.math('LESS_THAN', nb.math('FRACT', nb.math('DIVIDE', nb.math('ADD', v, 500.0), slab)), 0.045)
    joints = nb.math('MAXIMUM', joint_u, joint_v)

    tex_coord = nb.new("ShaderNodeTexCoord")
    grain = nb.noise(tex_coord.outputs["Object"], 3.0, detail=5.0, roughness=0.6)
    patches = nb.noise(tex_coord.outputs["Object"], 0.15, detail=2.0)
    tone = nb.math('MULTIPLY', nb.map_range(grain, 0.0, 1.0, 0.85, 1.15), nb.map_range(patches, 0.0, 1.0, 0.8, 1.15))
    tone = nb.math('MULTIPLY', tone, nb.math('SUBTRACT', 1.0, nb.math('MULTIPLY', joints, 0.45)))
    color = nb.mix_color(nb.map_range(tone, 0.6, 1.3, 0.0, 1.0), (0.26, 0.25, 0.24, 1.0), (0.58, 0.57, 0.54, 1.0))
    tree.links.new(color, bsdf.inputs["Base Color"])
    tree.links.new(nb.map_range(grain, 0.0, 1.0, 0.75, 0.95), bsdf.inputs["Roughness"])

    bump = nb.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.2
    bump.inputs["Distance"].default_value = 0.02
    tree.links.new(nb.math('SUBTRACT', grain, joints), bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    atmosphere.apply_haze(mat)
    return mat
