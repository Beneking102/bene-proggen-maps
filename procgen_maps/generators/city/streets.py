"""Street network construction from raw layout segments, and road mesh building.

`build_street_graph` is pure Python: it snaps and dedupes the raw, often
overlapping per-block perimeter segments from `layout.py` into a clean
node/edge graph and classifies intersections by node degree.
`build_street_meshes` is the bpy build step: one road object per edge -
the asphalt strip (width by street class) plus a raised sidewalk with a
curb on each side, in one mesh with two material slots - and a small
asphalt fan at each 3+-way intersection. Terrain-follow samples
`generators.terrain.sample_world_height` directly rather than ray-casting
against the generated mesh, for speed and to avoid depsgraph-ordering
issues.

Every road strip carries two UV maps in *meters* (materials/road_mat.py
reads them to paint lane lines, crosswalks and paving joints without any
texture files): `ProcgenMaps_Road` = (distance along, signed distance
across) and `ProcgenMaps_RoadEnds` = (distance to the start node,
distance to the end node) minus the intersection fan radius, or a large
sentinel at ends that are not intersections, so zebra crossings only
appear where streets actually cross.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .layout import StreetSegment
from .setback import SIDEWALK_WIDTH

_SNAP_TOLERANCE = 0.5  # meters; merges near-identical endpoints from overlapping block-perimeter segments
_MAX_ROAD_SEGMENT_LENGTH = 12.0  # meters; long strips are chopped into chunks this size at most (see _build_road_strip)

STREETS_PREFIX = "ProcgenMaps_Street"

CURB_HEIGHT = 0.12            # meters the sidewalk surface sits above the asphalt
ROAD_LIFT = 0.05              # meters above terrain, avoids z-fighting with the ground
_SIDEWALK_MIN_LENGTH = 1.0    # meters; shorter leftover sidewalk pieces (tiny blocks) are skipped


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


def node_fan_radii(graph: StreetGraph, preset) -> Dict[int, float]:
    """Intersection fan radius per node = half the widest street meeting
    there. Previously every fan used the preset's widest street, so a
    crossing of two local streets got an arterial-sized asphalt disc that
    poked past the lot lines. Pure Python, unit-tested."""
    radii: Dict[int, float] = {}
    for (a, b, street_class) in graph.edges:
        width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
        for node in (a, b):
            radii[node] = max(radii.get(node, 0.0), width / 2.0)
    return radii


def sidewalk_trim(node_degree: int, fan_radius: float) -> float:
    """How far (meters) a strip's sidewalk stops short of the node at that
    end: at any node another street meets (degree >= 2, i.e. a corner or
    an intersection) the sidewalk must clear the crossing street's own
    half width plus its sidewalk, otherwise it would run straight across
    that street's asphalt. A dead end (degree 1) keeps its sidewalk to the
    very end. Pure Python, unit-tested."""
    if node_degree <= 1:
        return 0.0
    return fan_radius + SIDEWALK_WIDTH


def build_street_meshes(graph: StreetGraph, preset, terrain_params=None, collection=None):
    """Build one road object per edge (asphalt + sidewalks/curbs) plus
    intersection fans. Returns the created objects."""
    from .. import terrain as terrain_gen
    from ...materials import road_mat

    def height_at(x, y):
        if terrain_params is None:
            return ROAD_LIFT
        return terrain_gen.sample_world_height(x, y, terrain_params) + ROAD_LIFT

    asphalt = road_mat.get_or_create_road_material()
    sidewalk = road_mat.get_or_create_sidewalk_material()
    fan_radius_by_node = node_fan_radii(graph, preset)

    created = []
    for index, (a, b, street_class) in enumerate(graph.edges):
        arterial = street_class == "arterial"
        width = preset.street_width_arterial if arterial else preset.street_width_local
        degree_a = graph.node_degree.get(a, 0)
        degree_b = graph.node_degree.get(b, 0)
        radius_a, radius_b = fan_radius_by_node[a], fan_radius_by_node[b]
        obj = _build_road_strip(
            f"{STREETS_PREFIX}_Seg{index}", graph.nodes[a], graph.nodes[b], width, height_at,
            crosswalk_ends=(degree_a >= 3, degree_b >= 3), fan_radii=(radius_a, radius_b),
            sidewalk_trims=(sidewalk_trim(degree_a, radius_a), sidewalk_trim(degree_b, radius_b)),
            materials=(asphalt, sidewalk))
        obj["procgen_maps_street_class"] = street_class
        obj["procgen_maps_road_width"] = float(width)
        obj["procgen_maps_road_lanes"] = 2.0 if arterial else 1.0
        if collection is not None:
            collection.objects.link(obj)
        created.append(obj)

    for node_index, degree in graph.node_degree.items():
        if degree < 3:
            continue
        obj = _build_intersection_fan(f"{STREETS_PREFIX}_Node{node_index}", graph.nodes[node_index],
                                      fan_radius_by_node[node_index], height_at, material=asphalt)
        if collection is not None:
            collection.objects.link(obj)
        created.append(obj)

    return created


class _RoadMeshData:
    """Accumulates verts/faces plus the per-vertex UV pairs and per-face
    material index for one road object, then writes them to a mesh."""

    def __init__(self):
        self.verts = []
        self.road_uv = []      # (along, across) meters, per vertex
        self.end_uv = []       # (to start, to end) meters minus fan radius, per vertex
        self.faces = []
        self.material_index = []

    def add_vert(self, co, road_uv, end_uv):
        self.verts.append(co)
        self.road_uv.append(road_uv)
        self.end_uv.append(end_uv)
        return len(self.verts) - 1

    def add_face(self, indices, material_index):
        self.faces.append(tuple(indices))
        self.material_index.append(material_index)

    def to_mesh(self, name, materials):
        import bpy

        from ...materials import road_mat

        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.verts, [], self.faces)
        mesh.update(calc_edges=True)
        for material in materials:
            mesh.materials.append(material)
        mesh.polygons.foreach_set("material_index", self.material_index)

        loop_verts = [i for face in self.faces for i in face]
        for uv_name, per_vertex in ((road_mat.ROAD_UV_NAME, self.road_uv), (road_mat.ROAD_END_UV_NAME, self.end_uv)):
            layer = mesh.uv_layers.new(name=uv_name)
            flat = []
            for i in loop_verts:
                flat.extend(per_vertex[i])
            layer.data.foreach_set("uv", flat)
        return mesh


def _build_road_strip(name, start, end, width, height_at, crosswalk_ends=(False, False), fan_radii=(0.0, 0.0),
                      sidewalk_trims=(0.0, 0.0), materials=()):
    """A single quad spanning the whole edge only samples terrain height at
    its 4 corners - fine for a short block face, but a long arterial can
    run for 100+ meters, and the terrain can undulate meaningfully between
    those two endpoints (nothing in between ever gets sampled). The result
    is a rigid flat plank that clips into hills or leaves a gap over dips
    along its length, even though its own endpoints match the ground
    exactly. Chopping into _MAX_ROAD_SEGMENT_LENGTH chunks - each sampling
    its own corners - makes the strip actually follow the terrain profile,
    the same reason the terrain mesh itself is a fine grid and not one
    giant quad.

    The sidewalks (one per side, SIDEWALK_WIDTH wide, CURB_HEIGHT above
    the asphalt, with a vertical curb face) are built the same chunked way
    over the trimmed range [trim_a, length - trim_b] so they stop short of
    crossing streets (see sidewalk_trim)."""
    import bpy
    import mathutils

    from ...materials import road_mat

    direction = mathutils.Vector((end[0] - start[0], end[1] - start[1]))
    length = direction.length
    if length < 1e-6:
        direction = mathutils.Vector((1.0, 0.0))
    else:
        direction.normalize()
    normal = mathutils.Vector((-direction.y, direction.x))
    half = width / 2.0

    def point_at(along, across):
        return (start[0] + direction.x * along + normal.x * across,
                start[1] + direction.y * along + normal.y * across)

    def end_uv_at(along):
        to_start = along - fan_radii[0] if crosswalk_ends[0] else road_mat.ROAD_END_SENTINEL
        to_end = (length - along) - fan_radii[1] if crosswalk_ends[1] else road_mat.ROAD_END_SENTINEL
        return (to_start, to_end)

    data = _RoadMeshData()

    # Asphalt strip: material 0, across = +half (left) / -half (right).
    segment_count = max(1, math.ceil(length / _MAX_ROAD_SEGMENT_LENGTH))
    ring = []
    for i in range(segment_count + 1):
        along = length * i / segment_count
        left = point_at(along, half)
        right = point_at(along, -half)
        ring.append((data.add_vert((left[0], left[1], height_at(*left)), (along, half), end_uv_at(along)),
                     data.add_vert((right[0], right[1], height_at(*right)), (along, -half), end_uv_at(along))))
    for i in range(segment_count):
        (l0, r0), (l1, r1) = ring[i], ring[i + 1]
        data.add_face((l0, r0, r1, l1), 0)

    # Sidewalks + curbs: material 1. No crosswalk/marking data needed (the
    # sidewalk shader only reads the along/across UV for its paving joints),
    # so their end UVs just carry the sentinel.
    no_ends = (road_mat.ROAD_END_SENTINEL, road_mat.ROAD_END_SENTINEL)
    walk_start = sidewalk_trims[0]
    walk_end = length - sidewalk_trims[1]
    if walk_end - walk_start >= _SIDEWALK_MIN_LENGTH:
        walk_length = walk_end - walk_start
        walk_segments = max(1, math.ceil(walk_length / _MAX_ROAD_SEGMENT_LENGTH))
        for side in (1.0, -1.0):
            inner_across = side * half
            outer_across = side * (half + SIDEWALK_WIDTH)
            rows = []
            for i in range(walk_segments + 1):
                along = walk_start + walk_length * i / walk_segments
                inner = point_at(along, inner_across)
                outer = point_at(along, outer_across)
                inner_z = height_at(*inner)
                rows.append((
                    data.add_vert((inner[0], inner[1], inner_z), (along, inner_across), no_ends),                 # curb foot
                    data.add_vert((inner[0], inner[1], inner_z + CURB_HEIGHT), (along, inner_across), no_ends),   # curb top
                    data.add_vert((outer[0], outer[1], height_at(*outer) + CURB_HEIGHT), (along, outer_across), no_ends),
                ))
            for i in range(walk_segments):
                (f0, t0, o0), (f1, t1, o1) = rows[i], rows[i + 1]
                if side > 0:
                    data.add_face((f0, f1, t1, t0), 1)   # curb face, facing the road
                    data.add_face((t0, t1, o1, o0), 1)   # sidewalk top
                else:
                    data.add_face((f1, f0, t0, t1), 1)
                    data.add_face((t1, t0, o0, o1), 1)

    mesh = data.to_mesh(name, materials)
    return bpy.data.objects.new(name, mesh)


def _build_intersection_fan(name, center, radius, height_at, sides=8, material=None):
    import bpy

    from ...materials import road_mat

    cx, cy = center
    data = _RoadMeshData()
    no_marks = (0.0, road_mat.NO_MARKINGS_V)
    no_ends = (road_mat.ROAD_END_SENTINEL, road_mat.ROAD_END_SENTINEL)
    data.add_vert((cx, cy, height_at(cx, cy)), no_marks, no_ends)
    for i in range(sides):
        angle = (2 * math.pi * i) / sides
        px = cx + radius * math.cos(angle)
        py = cy + radius * math.sin(angle)
        data.add_vert((px, py, height_at(px, py)), no_marks, no_ends)
    for i in range(sides):
        data.add_face((0, i + 1, (i + 1) % sides + 1), 0)

    mesh = data.to_mesh(name, (material,) if material is not None else ())
    return bpy.data.objects.new(name, mesh)
