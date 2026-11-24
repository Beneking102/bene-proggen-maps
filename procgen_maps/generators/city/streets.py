"""Street network construction from raw layout segments, and road mesh building.

`build_street_graph` is pure Python: it snaps and dedupes the raw, often
overlapping per-block perimeter segments from `layout.py` into a clean
node/edge graph and classifies intersections by node degree.
`build_street_meshes` is the bpy build step: the whole street network as
ONE mesh object (v0.12.0; it used to be one object per edge and per
intersection, ~1,100 objects for a Metropole) - per edge an asphalt strip
(width by street class) plus a raised sidewalk with a curb on each side,
per node an asphalt fan and the sidewalk corner pieces, two material
slots. The road width/lane count the asphalt shader needs are per-face
float attributes (`procgen_maps_road_width` / `procgen_maps_road_lanes`)
instead of per-object custom properties. Terrain-follow samples
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
_MAX_ROAD_SEGMENT_LENGTH = 3.0   # meters; strips are chopped into chunks this size at most so they follow the terrain (see _build_road_strip)

STREETS_PREFIX = "ProcgenMaps_Street"
STREET_NETWORK_OBJECT_NAME = f"{STREETS_PREFIX}_Network"
ROAD_WIDTH_ATTRIBUTE = "procgen_maps_road_width"   # per-face float: asphalt width in meters (0 on sidewalks/fans)
ROAD_LANES_ATTRIBUTE = "procgen_maps_road_lanes"   # per-face float: 1 = local, 2 = arterial (0 on sidewalks/fans)

CURB_HEIGHT = 0.12            # meters the sidewalk surface sits above the asphalt
ROAD_LIFT = 0.05              # meters above terrain, avoids z-fighting with the ground
FAN_LIFT = 0.01               # meters an intersection fan sits above the strips it covers (coplanar = z-fighting)
SIDEWALK_SKIRT = 0.6          # meters the sidewalk's outer edge extends down into the ground (hides the road-bed step)
COUNTRY_SHOULDER = 2.2        # meters of sunk ground beside a country road's asphalt (no sidewalk; the grass verge covers it)
COUNTRY_VERGE_WIDTH = 3.0     # meters the grass verge reaches out from the asphalt edge ...
COUNTRY_VERGE_SINK = 0.3      # ... ending this far *below* the heightfield, so it disappears into the ground
VERGE_SLOT = 2                # material slot of the verge faces (the terrain material; validation skips them)
_SIDEWALK_MIN_LENGTH = 1.0    # meters; shorter leftover sidewalk pieces (tiny blocks) are skipped


@dataclass
class StreetGraph:
    """Nodes (points), edges (node pair + class) and per-node degree of the street network."""
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
        """The node index of a point, creating the node on first use."""
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


def _edge_half_width(street_class, preset):
    return (preset.street_width_arterial if street_class == "arterial" else preset.street_width_local) / 2.0


def _line_intersection(p, d, q, e):
    """Intersection of the lines p + s*d and q + t*e (2-D), or None if parallel."""
    denominator = d[0] * e[1] - d[1] * e[0]
    if abs(denominator) < 1e-9:
        return None
    s = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / denominator
    return (p[0] + d[0] * s, p[1] + d[1] * s)


def corner_sidewalk_paths(node, outgoing, fan_radius, arc_segments=6):
    """Sidewalk pieces that close the gaps between the trimmed sidewalk
    strips meeting at a node. `outgoing` is [(direction (unit dx, dy),
    half_width), ...] for every edge leaving `node`.

    Each strip stops `sidewalk_trim` short of the node, so without these
    pieces every 90-degree corner lost a sidewalk-width square of pavement
    (grass showing between the two curbs), a straight-through node lost a
    2 x trim long stretch, and the outside of a bend had nothing at all.

    Returns one (inner_path, outer_path) pair per wedge between two
    angularly consecutive edges - polylines of equal length that
    _build_node_mesh zips into curb + slab quads. The inner path runs
    along the asphalt edge: strip end -> asphalt corner -> strip end for a
    convex wedge, straight across for a 180-degree wedge, and an arc
    around the node for a reflex wedge (the outside of a bend). Pure
    Python, unit-tested."""
    if len(outgoing) < 2:
        return []
    trim = fan_radius + SIDEWALK_WIDTH
    ordered = sorted(outgoing, key=lambda item: math.atan2(item[0][1], item[0][0]))
    pieces = []
    for i, (d1, h1) in enumerate(ordered):
        d2, h2 = ordered[(i + 1) % len(ordered)]
        n1 = (-d1[1], d1[0])          # left of d1: faces the next edge counter-clockwise
        n2 = (d2[1], -d2[0])          # right of d2: faces back toward d1
        inner1 = (node[0] + d1[0] * trim + n1[0] * h1, node[1] + d1[1] * trim + n1[1] * h1)
        outer1 = (node[0] + d1[0] * trim + n1[0] * (h1 + SIDEWALK_WIDTH),
                  node[1] + d1[1] * trim + n1[1] * (h1 + SIDEWALK_WIDTH))
        inner2 = (node[0] + d2[0] * trim + n2[0] * h2, node[1] + d2[1] * trim + n2[1] * h2)
        outer2 = (node[0] + d2[0] * trim + n2[0] * (h2 + SIDEWALK_WIDTH),
                  node[1] + d2[1] * trim + n2[1] * (h2 + SIDEWALK_WIDTH))
        wedge = (math.atan2(d2[1], d2[0]) - math.atan2(d1[1], d1[0])) % (2 * math.pi)
        if abs(wedge - math.pi) < math.radians(3):
            pieces.append(([inner1, inner2], [outer1, outer2]))
        elif wedge < math.pi:
            corner = _line_intersection(inner1, d1, inner2, d2)
            outer_corner = _line_intersection(outer1, d1, outer2, d2)
            if corner is None or outer_corner is None:
                pieces.append(([inner1, inner2], [outer1, outer2]))
            else:
                pieces.append(([inner1, corner, inner2], [outer1, outer_corner, outer2]))
        else:
            # Reflex wedge (outside of a bend): follow strip 1's curb back
            # to the node, sweep around the asphalt fan, then out along
            # strip 2's curb, so the pavement hugs the asphalt with no
            # grass wedge between them.
            inner = [inner1, (node[0] + n1[0] * fan_radius, node[1] + n1[1] * fan_radius)]
            outer = [outer1, (node[0] + n1[0] * (fan_radius + SIDEWALK_WIDTH),
                              node[1] + n1[1] * (fan_radius + SIDEWALK_WIDTH))]
            start = math.atan2(n1[1], n1[0])
            sweep = (math.atan2(n2[1], n2[0]) - start) % (2 * math.pi)
            for k in range(1, arc_segments):
                angle = start + sweep * k / arc_segments
                inner.append((node[0] + math.cos(angle) * fan_radius, node[1] + math.sin(angle) * fan_radius))
                outer.append((node[0] + math.cos(angle) * (fan_radius + SIDEWALK_WIDTH),
                              node[1] + math.sin(angle) * (fan_radius + SIDEWALK_WIDTH)))
            inner.append((node[0] + n2[0] * fan_radius, node[1] + n2[1] * fan_radius))
            outer.append((node[0] + n2[0] * (fan_radius + SIDEWALK_WIDTH),
                          node[1] + n2[1] * (fan_radius + SIDEWALK_WIDTH)))
            inner.append(inner2)
            outer.append(outer2)
            pieces.append((inner, outer))
    return pieces


def road_corridors(graph: StreetGraph, preset):
    """(ax, ay, bx, by, half_width) per edge, half_width covering asphalt plus
    both sidewalks - the ground footprint every street object occupies
    (a country road has no sidewalks: asphalt plus its skirt).
    generators/terrain.py's lower_terrain_under_corridors uses it so the
    terrain mesh never pokes through the road surface."""
    return [(graph.nodes[a][0], graph.nodes[a][1], graph.nodes[b][0], graph.nodes[b][1],
             _edge_half_width(street_class, preset) + (COUNTRY_SHOULDER if street_class == "country" else SIDEWALK_WIDTH))
            for (a, b, street_class) in graph.edges]


def node_corridor_discs(graph: StreetGraph, preset):
    """(cx, cy, radius) per node of degree >= 2: the corner pieces and
    intersection fans reach fan_radius + 2 x SIDEWALK_WIDTH around the node
    (the outer corner of a crossing), and the outside of a bend sweeps a
    full arc of that radius."""
    radii = node_fan_radii(graph, preset)
    return [(graph.nodes[n][0], graph.nodes[n][1], (radii[n] + SIDEWALK_WIDTH) * math.sqrt(2.0))
            for n, degree in graph.node_degree.items() if degree >= 2]


def build_street_meshes(graph: StreetGraph, preset, terrain_params=None, collection=None):
    """Build the whole street network (asphalt strips + sidewalks/curbs per
    edge, fans + sidewalk corners per node) into one mesh object. Returns
    the created objects (a one-element list)."""
    import bpy

    from .. import terrain as terrain_gen
    from ...materials import road_mat

    def height_at(x, y):
        if terrain_params is None:
            return ROAD_LIFT
        return terrain_gen.sample_world_height(x, y, terrain_params) + ROAD_LIFT

    from ...materials import terrain_mat

    asphalt = road_mat.get_or_create_road_material()
    sidewalk = road_mat.get_or_create_sidewalk_material()
    # Country-road verges wear the terrain material so they blend into
    # whatever ground they lie on (it shades by world position).
    verge = terrain_mat.get_or_create_terrain_material()
    fan_radius_by_node = node_fan_radii(graph, preset)
    data = _RoadMeshData()

    for index, (a, b, street_class) in enumerate(graph.edges):
        arterial = street_class == "arterial"
        width = preset.street_width_arterial if arterial else preset.street_width_local
        degree_a = graph.node_degree.get(a, 0)
        degree_b = graph.node_degree.get(b, 0)
        radius_a, radius_b = fan_radius_by_node[a], fan_radius_by_node[b]
        _build_road_strip(
            data, graph.nodes[a], graph.nodes[b], width, height_at,
            crosswalk_ends=(degree_a >= 3, degree_b >= 3), fan_radii=(radius_a, radius_b),
            sidewalk_trims=(sidewalk_trim(degree_a, radius_a), sidewalk_trim(degree_b, radius_b)),
            lanes=2.0 if arterial else 1.0, with_sidewalks=street_class != "country")

    outgoing_by_node = defaultdict(list)
    country_nodes = defaultdict(lambda: True)   # True while every edge at the node is a country road
    for (a, b, street_class) in graph.edges:
        (ax, ay), (bx, by) = graph.nodes[a], graph.nodes[b]
        length = math.hypot(bx - ax, by - ay) or 1.0
        direction = ((bx - ax) / length, (by - ay) / length)
        half = _edge_half_width(street_class, preset)
        outgoing_by_node[a].append((direction, half))
        outgoing_by_node[b].append(((-direction[0], -direction[1]), half))
        country_nodes[a] &= street_class == "country"
        country_nodes[b] &= street_class == "country"

    for node_index, degree in graph.node_degree.items():
        if degree < 2:
            continue
        outgoing = outgoing_by_node[node_index]
        if country_nodes[node_index]:
            # A bend of the country road: the asphalt fan plus a verge ring, no sidewalk corners.
            _build_node_mesh(data, graph.nodes[node_index], fan_radius_by_node[node_index], [], height_at,
                             with_fan=True, verge=True)
            continue
        # A fan covers every crossing and every bend; a straight-through
        # node (two collinear edges) needs none, its strips already meet.
        straight = degree == 2 and abs(outgoing[0][0][0] * outgoing[1][0][0]
                                       + outgoing[0][0][1] * outgoing[1][0][1] + 1.0) < 1e-3
        _build_node_mesh(data, graph.nodes[node_index], fan_radius_by_node[node_index], outgoing, height_at,
                         with_fan=not straight)

    mesh = data.to_mesh(STREET_NETWORK_OBJECT_NAME, (asphalt, sidewalk, verge))
    obj = bpy.data.objects.new(STREET_NETWORK_OBJECT_NAME, mesh)
    obj["procgen_maps_street_class"] = "network"
    obj["procgen_maps_street_edges"] = len(graph.edges)
    obj["procgen_maps_street_nodes"] = len(graph.nodes)
    if collection is not None:
        collection.objects.link(obj)
    return [obj]


class _RoadMeshData:
    """Accumulates verts/faces plus the per-vertex UV pairs and per-face
    material index for one road object, then writes them to a mesh."""

    def __init__(self):
        self.verts = []
        self.road_uv = []      # (along, across) meters, per vertex
        self.end_uv = []       # (to start, to end) meters minus fan radius, per vertex
        self.faces = []
        self.material_index = []
        self.road_width = []   # per face, meters (asphalt strips only)
        self.road_lanes = []   # per face, 1 local / 2 arterial (asphalt strips only)

    def add_vert(self, co, road_uv, end_uv):
        self.verts.append(co)
        self.road_uv.append(road_uv)
        self.end_uv.append(end_uv)
        return len(self.verts) - 1

    def add_face(self, indices, material_index, width=0.0, lanes=0.0):
        self.faces.append(tuple(indices))
        self.material_index.append(material_index)
        self.road_width.append(width)
        self.road_lanes.append(lanes)

    def to_mesh(self, name, materials):
        import bpy

        from ...materials import road_mat

        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.verts, [], self.faces)
        mesh.update(calc_edges=True)
        for material in materials:
            mesh.materials.append(material)
        mesh.polygons.foreach_set("material_index", self.material_index)
        for attribute_name, values in ((ROAD_WIDTH_ATTRIBUTE, self.road_width), (ROAD_LANES_ATTRIBUTE, self.road_lanes)):
            attribute = mesh.attributes.new(attribute_name, 'FLOAT', 'FACE')
            attribute.data.foreach_set("value", values)

        loop_verts = [i for face in self.faces for i in face]
        for uv_name, per_vertex in ((road_mat.ROAD_UV_NAME, self.road_uv), (road_mat.ROAD_END_UV_NAME, self.end_uv)):
            layer = mesh.uv_layers.new(name=uv_name)
            flat = []
            for i in loop_verts:
                flat.extend(per_vertex[i])
            layer.data.foreach_set("uv", flat)
        return mesh


def _build_road_strip(data, start, end, width, height_at, crosswalk_ends=(False, False), fan_radii=(0.0, 0.0),
                      sidewalk_trims=(0.0, 0.0), lanes=1.0, with_sidewalks=True):
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
    crossing streets (see sidewalk_trim). Appends to the shared `data`."""
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
        data.add_face((l0, r0, r1, l1), 0, width=width, lanes=lanes)

    no_ends = (road_mat.ROAD_END_SENTINEL, road_mat.ROAD_END_SENTINEL)
    if not with_sidewalks:
        # Country road: no sidewalk but a grass verge (terrain material,
        # VERGE_SLOT) sloping from the asphalt edge down into the ground
        # COUNTRY_VERGE_WIDTH out, so the sunk road bed never shows as a
        # trench and the road's edge is a soft shoulder, not a dark drop
        # (v0.14 had a vertical asphalt skirt there, which read as a
        # floating road with a black outline).
        for side in (1.0, -1.0):
            edge = [ring[i][0 if side > 0 else 1] for i in range(segment_count + 1)]
            feet = []
            for i in range(segment_count + 1):
                along = length * i / segment_count
                across = side * (half + COUNTRY_VERGE_WIDTH)
                px, py = point_at(along, across)
                feet.append(data.add_vert((px, py, height_at(px, py) - ROAD_LIFT - COUNTRY_VERGE_SINK),
                                          (along, across), no_ends))
            for i in range(segment_count):
                if side > 0:
                    data.add_face((edge[i], edge[i + 1], feet[i + 1], feet[i]), VERGE_SLOT)
                else:
                    data.add_face((edge[i + 1], edge[i], feet[i], feet[i + 1]), VERGE_SLOT)
        return

    # Sidewalks + curbs: material 1. No crosswalk/marking data needed (the
    # sidewalk shader only reads the along/across UV for its paving joints),
    # so their end UVs just carry the sentinel.
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
                outer_z = height_at(*outer) + CURB_HEIGHT
                rows.append((
                    data.add_vert((inner[0], inner[1], inner_z), (along, inner_across), no_ends),                 # curb foot
                    data.add_vert((inner[0], inner[1], inner_z + CURB_HEIGHT), (along, inner_across), no_ends),   # curb top
                    data.add_vert((outer[0], outer[1], outer_z), (along, outer_across), no_ends),
                    data.add_vert((outer[0], outer[1], outer_z - SIDEWALK_SKIRT), (along, outer_across), no_ends),  # skirt foot
                ))
            for i in range(walk_segments):
                (f0, t0, o0, s0), (f1, t1, o1, s1) = rows[i], rows[i + 1]
                if side > 0:
                    data.add_face((f0, f1, t1, t0), 1)   # curb face, facing the road
                    data.add_face((t0, t1, o1, o0), 1)   # sidewalk top
                    data.add_face((o0, o1, s1, s0), 1)   # outer skirt, facing away from the road
                else:
                    data.add_face((f1, f0, t0, t1), 1)
                    data.add_face((t1, t0, o0, o1), 1)
                    data.add_face((o1, o0, s0, s1), 1)


def _build_node_mesh(data, center, radius, outgoing, height_at, with_fan=True, sides=8, verge=False):
    """Per street node: the asphalt fan (slot 0) that covers the overlapping
    strip ends, plus the sidewalk corner pieces (slot 1) from
    corner_sidewalk_paths that join the trimmed strips of the meeting
    streets - or, with `verge` (a country-road bend), a grass verge ring
    (VERGE_SLOT) around the fan instead. Appends to the shared `data`."""
    from ...materials import road_mat

    cx, cy = center
    no_marks = (0.0, road_mat.NO_MARKINGS_V)
    no_ends = (road_mat.ROAD_END_SENTINEL, road_mat.ROAD_END_SENTINEL)
    if with_fan:
        hub = data.add_vert((cx, cy, height_at(cx, cy) + FAN_LIFT), no_marks, no_ends)
        rim = []
        for i in range(sides):
            angle = (2 * math.pi * i) / sides
            px = cx + radius * math.cos(angle)
            py = cy + radius * math.sin(angle)
            rim.append(data.add_vert((px, py, height_at(px, py) + FAN_LIFT), no_marks, no_ends))
        for i in range(sides):
            data.add_face((hub, rim[i], rim[(i + 1) % sides]), 0)
        if verge:
            outer_radius = radius + COUNTRY_VERGE_WIDTH
            feet = []
            for i in range(sides):
                angle = (2 * math.pi * i) / sides
                px = cx + outer_radius * math.cos(angle)
                py = cy + outer_radius * math.sin(angle)
                feet.append(data.add_vert((px, py, height_at(px, py) - ROAD_LIFT - COUNTRY_VERGE_SINK),
                                          (px - cx, py - cy), no_ends))
            for i in range(sides):
                j = (i + 1) % sides
                data.add_face((rim[i], feet[i], feet[j], rim[j]), VERGE_SLOT)

    for inner_path, outer_path in corner_sidewalk_paths(center, outgoing, radius):
        feet, tops, outers, skirts = [], [], [], []
        for (ix, iy), (ox, oy) in zip(inner_path, outer_path):
            inner_z = height_at(ix, iy)
            feet.append(data.add_vert((ix, iy, inner_z), (ix - cx, iy - cy), no_ends))
            tops.append(data.add_vert((ix, iy, inner_z + CURB_HEIGHT), (ix - cx, iy - cy), no_ends))
            if outers and abs(data.verts[outers[-1]][0] - ox) < 1e-6 and abs(data.verts[outers[-1]][1] - oy) < 1e-6:
                outers.append(outers[-1])   # equal-width crossing: outer corner coincides with the strip ends
                skirts.append(skirts[-1])
            else:
                outer_z = height_at(ox, oy) + CURB_HEIGHT
                outers.append(data.add_vert((ox, oy, outer_z), (ox - cx, oy - cy), no_ends))
                skirts.append(data.add_vert((ox, oy, outer_z - SIDEWALK_SKIRT), (ox - cx, oy - cy), no_ends))
        for k in range(len(inner_path) - 1):
            if inner_path[k] != inner_path[k + 1]:
                data.add_face((feet[k], feet[k + 1], tops[k + 1], tops[k]), 1)     # curb, facing the road
            quad = (tops[k], tops[k + 1], outers[k + 1], outers[k])
            unique = []
            for v in quad:
                if v not in unique:
                    unique.append(v)
            if len(unique) >= 3:
                data.add_face(unique, 1)                                             # slab top
            if outers[k] != outers[k + 1]:
                data.add_face((outers[k + 1], outers[k], skirts[k], skirts[k + 1]), 1)   # outer skirt
