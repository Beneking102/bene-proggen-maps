"""River water surface, banks and bridges.

The river itself is planned in layout.py (`plan_river` -> `RiverSpec`): it
runs through one column of the street grid, so lots never touch it and
the cross streets spanning that column are the bridges. This module is
the build half plus the small pure helpers around it:

- `bridge_edges` (pure) finds the street edges that span the river.
- `build_water_mesh` lays a water strip along the centerline, sunk
  `WATER_DROP` below the heightfield; generators/city/__init__.py sinks
  the terrain `RIVER_DEPTH` under the water and blends it back up over
  the bank margin (terrain.lower_terrain_under_corridors), which is what
  makes the sloped banks.
- `build_bridges` adds a deck slab, two railings and a pier under every
  bridge edge, so the road strip (which simply keeps following the
  heightfield across the gap) reads as a bridge rather than a floating
  ribbon.
"""
import math
from typing import List, Tuple

from .layout import RiverSpec
from .setback import SIDEWALK_WIDTH
from .streets import CURB_HEIGHT, ROAD_LIFT, StreetGraph, node_fan_radii, sidewalk_trim

WATER_OBJECT_NAME = "ProcgenMaps_Water"
BRIDGES_OBJECT_NAME = "ProcgenMaps_Bridges"
RIVER_DEPTH = 2.6          # meters the terrain is sunk under the water
WATER_DROP = 1.2           # meters the water surface sits below the heightfield
_WATER_CHUNK = 6.0         # meters between water-strip rings
_RAILING_HEIGHT = 1.1
_DECK_THICKNESS = 0.7


def bridge_edges(graph: StreetGraph, river: RiverSpec) -> List[int]:
    """Indices of the street edges that cross the river: their midpoint is
    inside the river's column and the edge runs across it. Pure Python."""
    if river is None:
        return []
    x_lo, x_hi = river.x_range
    result = []
    for index, (a, b, _cls) in enumerate(graph.edges):
        (ax, ay), (bx, by) = graph.nodes[a], graph.nodes[b]
        mid_x = (ax + bx) / 2.0
        if x_lo + 0.5 < mid_x < x_hi - 0.5 and abs(bx - ax) > abs(by - ay):
            result.append(index)
    return result


def build_water_mesh(river: RiverSpec, terrain_params, collection=None):
    """One strip mesh along the river centerline, `WATER_DROP` under the
    heightfield (sampled on the centerline so both banks share a level),
    wide enough to run into the sloped bank on either side."""
    import bpy

    from .. import terrain as terrain_gen
    from ...materials import prop_mat

    if river is None:
        return []
    half = river.half_width + river.bank_margin * 0.45
    verts: List[Tuple[float, float, float]] = []
    faces = []
    points = _resample(river.points, _WATER_CHUNK)
    for index, (x, y) in enumerate(points):
        prev_point = points[max(0, index - 1)]
        next_point = points[min(len(points) - 1, index + 1)]
        dx, dy = next_point[0] - prev_point[0], next_point[1] - prev_point[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        z = (terrain_gen.sample_world_height(x, y, terrain_params) if terrain_params is not None else 0.0) - WATER_DROP
        verts.append((x + nx * half, y + ny * half, z))
        verts.append((x - nx * half, y - ny * half, z))
        if index > 0:
            base = 2 * index
            faces.append((base - 2, base - 1, base + 1, base))
    mesh = bpy.data.meshes.new(WATER_OBJECT_NAME)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(prop_mat.get_or_create_prop_material("water"))
    obj = bpy.data.objects.new(WATER_OBJECT_NAME, mesh)
    obj["procgen_maps_water"] = "river"
    if collection is not None:
        collection.objects.link(obj)
    return [obj]


def build_bridges(graph: StreetGraph, river: RiverSpec, preset, terrain_params, collection=None):
    """Deck slab + two railings + one pier per bridge edge, as one mesh
    object. The deck's top stays just under the road strip (which samples
    the heightfield at its own vertices), the railings stand on the
    sidewalks' outer edge over the trimmed sidewalk range."""
    import bpy

    from .. import terrain as terrain_gen
    from ...materials import prop_mat

    edges = bridge_edges(graph, river)
    if not edges:
        return []
    radii = node_fan_radii(graph, preset)
    verts: List[Tuple[float, float, float]] = []
    faces = []
    material_index = []

    def box(x0, x1, y0, y1, z0, z1, material):
        base = len(verts)
        verts.extend((x, y, z) for z in (z0, z1) for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))
        for quad in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            faces.append(tuple(base + i for i in quad))
            material_index.append(material)

    def height(x, y):
        return terrain_gen.sample_world_height(x, y, terrain_params) if terrain_params is not None else 0.0

    for index in edges:
        a, b, street_class = graph.edges[index]
        (ax, ay), (bx, by) = graph.nodes[a], graph.nodes[b]
        if ax > bx:
            (ax, ay), (bx, by), a, b = (bx, by), (ax, ay), b, a
        width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
        half = width / 2.0 + SIDEWALK_WIDTH
        trim_a = sidewalk_trim(graph.node_degree.get(a, 0), radii[a])
        trim_b = sidewalk_trim(graph.node_degree.get(b, 0), radii[b])
        x0, x1 = ax + trim_a, bx - trim_b
        if x1 - x0 < 2.0:
            continue
        y = (ay + by) / 2.0
        z_a, z_b = height(ax, ay) + ROAD_LIFT, height(bx, by) + ROAD_LIFT
        deck_top = min(z_a, z_b) - 0.02
        box(x0, x1, y - half, y + half, deck_top - _DECK_THICKNESS, deck_top, 0)
        rail_z0 = min(z_a, z_b) + CURB_HEIGHT
        rail_z1 = max(z_a, z_b) + CURB_HEIGHT + _RAILING_HEIGHT
        for side in (-1.0, 1.0):
            outer = y + side * half
            inner = outer - side * 0.12
            box(x0, x1, min(outer, inner), max(outer, inner), rail_z0, rail_z1, 1)
        # One pier in the middle of the water, down into the riverbed.
        px = river.points[min(range(len(river.points)), key=lambda i: abs(river.points[i][1] - y))][0]
        px = max(x0 + 1.5, min(x1 - 1.5, px))
        bed = height(px, y) - RIVER_DEPTH - 0.5
        box(px - 1.0, px + 1.0, y - half * 0.7, y + half * 0.7, bed, deck_top - _DECK_THICKNESS + 0.05, 0)

    mesh = bpy.data.meshes.new(BRIDGES_OBJECT_NAME)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(prop_mat.get_or_create_prop_material("bridge"))
    mesh.materials.append(prop_mat.get_or_create_prop_material("furniture_metal"))
    mesh.polygons.foreach_set("material_index", material_index)
    obj = bpy.data.objects.new(BRIDGES_OBJECT_NAME, mesh)
    obj["procgen_maps_bridges"] = len(edges)
    if collection is not None:
        collection.objects.link(obj)
    return [obj]


def _resample(points, spacing):
    """The polyline with extra points so no piece is longer than `spacing`."""
    result = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        length = math.hypot(bx - ax, by - ay)
        pieces = max(1, int(math.ceil(length / spacing)))
        for k in range(1, pieces + 1):
            t = k / pieces
            result.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return result
