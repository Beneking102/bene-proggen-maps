"""Street network construction from raw layout segments, and road mesh building.

`build_street_graph` is pure Python: it snaps and dedupes the raw, often
overlapping per-block perimeter segments from `layout.py` into a clean
node/edge graph and classifies intersections by node degree.
`build_street_meshes` is the bpy build step: one road-strip mesh per edge
(width by street class) plus a small fan at each 3+-way intersection.
Terrain-follow samples `generators.terrain.sample_world_height` directly
rather than ray-casting against the generated mesh, for speed and to avoid
depsgraph-ordering issues.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .layout import StreetSegment

_SNAP_TOLERANCE = 0.5  # meters; merges near-identical endpoints from overlapping block-perimeter segments
_MAX_ROAD_SEGMENT_LENGTH = 12.0  # meters; long strips are chopped into chunks this size at most (see _build_road_strip)

STREETS_PREFIX = "ProcgenMaps_Street"


@dataclass
class StreetGraph:
    nodes: List[Tuple[float, float]] = field(default_factory=list)
    edges: List[Tuple[int, int, str]] = field(default_factory=list)  # (node_a, node_b, street_class)
    node_degree: Dict[int, int] = field(default_factory=dict)


def _snap(point: Tuple[float, float]) -> Tuple[float, float]:
    return (round(point[0] / _SNAP_TOLERANCE) * _SNAP_TOLERANCE,
            round(point[1] / _SNAP_TOLERANCE) * _SNAP_TOLERANCE)


def build_street_graph(segments: List[StreetSegment]) -> StreetGraph:
    """Snap + dedupe raw layout segments into a clean node/edge graph."""
    node_index: Dict[Tuple[float, float], int] = {}
    nodes: List[Tuple[float, float]] = []

    def node_id(point):
        key = _snap(point)
        if key not in node_index:
            node_index[key] = len(nodes)
            nodes.append(key)
        return node_index[key]

    edge_class: Dict[Tuple[int, int], str] = {}
    for seg in segments:
        a, b = node_id(seg.start), node_id(seg.end)
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        if key in edge_class and (edge_class[key] == "arterial" or seg.street_class == "local"):
            continue
        edge_class[key] = seg.street_class

    degree: Dict[int, int] = defaultdict(int)
    edges = []
    for (a, b), street_class in edge_class.items():
        edges.append((a, b, street_class))
        degree[a] += 1
        degree[b] += 1

    return StreetGraph(nodes=nodes, edges=edges, node_degree=dict(degree))


def build_street_meshes(graph: StreetGraph, preset, terrain_params=None, collection=None):
    """Build one road-strip object per edge plus intersection fans. Returns the created objects."""
    from .. import terrain as terrain_gen

    def height_at(x, y):
        if terrain_params is None:
            return 0.0
        return terrain_gen.sample_world_height(x, y, terrain_params) + 0.05  # avoid z-fighting with terrain

    created = []
    for index, (a, b, street_class) in enumerate(graph.edges):
        width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
        obj = _build_road_strip(f"{STREETS_PREFIX}_Seg{index}", graph.nodes[a], graph.nodes[b], width, height_at)
        obj["procgen_maps_street_class"] = street_class
        if collection is not None:
            collection.objects.link(obj)
        created.append(obj)

    for node_index, degree in graph.node_degree.items():
        if degree < 3:
            continue
        radius = max(preset.street_width_arterial, preset.street_width_local) / 2.0
        obj = _build_intersection_fan(f"{STREETS_PREFIX}_Node{node_index}", graph.nodes[node_index], radius, height_at)
        if collection is not None:
            collection.objects.link(obj)
        created.append(obj)

    return created


def _build_road_strip(name, start, end, width, height_at):
    """A single quad spanning the whole edge only samples terrain height at
    its 4 corners - fine for a short block face, but a long arterial can
    run for 100+ meters, and the terrain can undulate meaningfully between
    those two endpoints (nothing in between ever gets sampled). The result
    is a rigid flat plank that clips into hills or leaves a gap over dips
    along its length, even though its own endpoints match the ground
    exactly. Chopping into _MAX_ROAD_SEGMENT_LENGTH chunks - each sampling
    its own corners - makes the strip actually follow the terrain profile,
    the same reason the terrain mesh itself is a fine grid and not one
    giant quad."""
    import bpy
    import mathutils

    direction = mathutils.Vector((end[0] - start[0], end[1] - start[1]))
    length = direction.length
    if length < 1e-6:
        direction = mathutils.Vector((1.0, 0.0))
    else:
        direction.normalize()
    side = mathutils.Vector((-direction.y, direction.x)) * (width / 2.0)

    segment_count = max(1, math.ceil(length / _MAX_ROAD_SEGMENT_LENGTH))
    verts = []
    faces = []
    for i in range(segment_count + 1):
        t = i / segment_count
        cx = start[0] + (end[0] - start[0]) * t
        cy = start[1] + (end[1] - start[1]) * t
        left = (cx + side.x, cy + side.y)
        right = (cx - side.x, cy - side.y)
        verts.append((left[0], left[1], height_at(left[0], left[1])))
        verts.append((right[0], right[1], height_at(right[0], right[1])))
    for i in range(segment_count):
        i0 = i * 2
        faces.append((i0, i0 + 1, i0 + 3, i0 + 2))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    return bpy.data.objects.new(name, mesh)


def _build_intersection_fan(name, center, radius, height_at, sides=8):
    import bpy

    cx, cy = center
    verts = [(cx, cy, height_at(cx, cy))]
    for i in range(sides):
        angle = (2 * math.pi * i) / sides
        px = cx + radius * math.cos(angle)
        py = cy + radius * math.sin(angle)
        verts.append((px, py, height_at(px, py)))

    faces = [(0, i + 1, (i + 1) % sides + 1) for i in range(sides)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    return bpy.data.objects.new(name, mesh)
