"""Street signage: stop signs, speed-limit signs, street-name signs, and
traffic lights - placed by deterministic, rule-based logic driven by the
street graph's intersection degree (reusing streets.py's own >=3 "real
intersection" gate via graph.node_degree) and street_class hierarchy
(arterial/local), the same way zones.py is rule-based classification
rather than props.py's density-driven scatter.

At every real intersection, the pair of incident edges most nearly
opposite each other (see _find_through_pair) is treated as the priority
"through" route and never gets a stop sign, mirroring how a real minor
junction works - only the non-through local approaches stop. Any
intersection with 2+ arterial approaches gets a traffic light instead
(a decorative red/yellow/green head, no simulation) rather than nothing,
since a real arterial-arterial crossing is normally signaled, not
stop-sign controlled. Every street still gets speed-limit repeater signs
at a class-dependent interval, and every arterial approach at a real
intersection gets a street-name sign (one name drawn per edge, from a
small deterministic word bank, so both ends of the same edge agree).

All three sign types are built as bespoke mesh/FONT objects directly in
this module's build step (mirroring special_buildings.py's _build_sign
idiom) instead of through assets/factory.py's Instance-Collection
pipeline: that pipeline shares one master mesh per asset_id across every
instance, which cannot vary per-instance text - needed here for
speed-limit numbers and street names - so bespoke construction keeps this
feature self-contained in one new module rather than touching the shared
assets/factory.py used by every other prop type.

Known pre-existing limitation (not introduced here): stop signs and
street-name signs only ever appear at a real intersection (graph.
node_degree[node] >= 3, the same gate streets.py's own intersection-fan
decoration already uses). Raster-mode presets (Metropole, Industrial)
currently never produce such a node at all - every node in their street
graph has degree 1 - because generators/city/layout.py's
_raster_street_segments computes each block's street-exit point per
neighbor pair from that pair's own center-to-center direction; a block
touching 3+ neighbors gets a different, non-coincident exit point for
each one, so build_street_graph's endpoint-snapping never merges them
into one shared junction node. Grid-mode presets (Kleinstadt, Dorf) are
unaffected. Speed-limit signs still place normally everywhere (they're
purely per-edge, not intersection-driven). Fixing the underlying raster
street topology is out of scope here - it would require deriving each
shared block-to-block boundary's actual midpoint from the raster grid's
adjacency rather than a centerline ray, a larger, separate change.
"""
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ...utils.spatial import SpatialHashGrid
from .buildings import BuildingPlan
from .props import PropPlacement
from .streets import StreetGraph

SIGNAGE_PREFIX = "ProcgenMaps_Signage"

SPEED_LIMIT_KMH = {"arterial": 50, "local": 30}
_SPEED_SIGN_INTERVAL = {"arterial": 140.0, "local": 200.0}
_SPEED_SIGN_MIN_EDGE_LENGTH = 15.0

_STREET_NAME_WORDS = ("Oak", "Maple", "Elm", "Birch", "Cedar", "Willow", "Pine", "Ash",
                      "Chestnut", "Linden", "Poplar", "Alder", "Hazel", "Rowan", "Larch", "Beech")
_STREET_NAME_SUFFIXES = ("Street", "Avenue", "Boulevard", "Lane", "Road", "Way", "Drive")
_SIGNAGE_SEED_OFFSET = 51829  # decorrelates this module's rng stream from special_buildings.py's (+9973)

# A pair of incident edges at a node is treated as one continuous priority
# "through" route (see _find_through_pair) when their outward directions
# are within this far of exactly opposite (dot product of the two unit
# direction vectors below this threshold). -1.0 is perfectly opposite;
# -0.5 allows roughly +-60 degrees of slack for jittered grid geometry.
_THROUGH_ROUTE_DOT_THRESHOLD = -0.5

_SIGN_FOOTPRINT_RADIUS = {"stop": 0.4, "speed_limit": 0.35, "street_name": 0.45, "traffic_light": 0.3}

_POLE_HALF_WIDTH = 0.03
_SPEED_SIGN_POLE_HEIGHT = 2.0
_SPEED_SIGN_BOARD_WIDTH = 0.6
_SPEED_SIGN_BOARD_HEIGHT = 0.6
_SPEED_SIGN_BOARD_THICKNESS = 0.03
_NAME_SIGN_POLE_HEIGHT = 2.4
_NAME_SIGN_BOARD_WIDTH = 1.1
_NAME_SIGN_BOARD_HEIGHT = 0.3
_NAME_SIGN_BOARD_THICKNESS = 0.03
_STOP_SIGN_POLE_HEIGHT = 2.3
_STOP_SIGN_BOARD_RADIUS = 0.35
_STOP_SIGN_BOARD_THICKNESS = 0.03
_TRAFFIC_LIGHT_POLE_HEIGHT = 4.0
_TRAFFIC_LIGHT_HEAD_WIDTH = 0.28
_TRAFFIC_LIGHT_HEAD_DEPTH = 0.18
_TRAFFIC_LIGHT_HEAD_HEIGHT = 0.8
_TRAFFIC_LIGHT_LENS_SIZE = 0.16
_TRAFFIC_LIGHT_LENS_THICKNESS = 0.02


@dataclass
class SignPlacement:
    kind: str                              # 'stop' | 'speed_limit' | 'street_name'
    location: Tuple[float, float, float]   # (x, y, z) - z already sampled from terrain, like PropPlacement
    rotation_z: float = 0.0
    text: str = ""                         # "" for stop; "30"/"50" for speed_limit; "<Word> <Suffix>" for street_name


def _facing_to_rotation_z(fx: float, fy: float) -> float:
    """Yaw about world Z so a sign's -Y-default-facing readable side (the
    same convention special_buildings.py's _build_sign establishes: a
    fresh Font object's front points world -Y at rotation_euler=(pi/2, 0,
    0), see that function's own comment) points toward the horizontal unit
    direction (fx, fy) instead. Derivation: Blender's default 'XYZ' euler
    composes as Rz(rz) @ Ry(ry) @ Rx(rx); with rx=pi/2 fixed, Rx alone
    sends local +Z to world -Y (the base case), then Rz(theta) sends that
    to world (sin(theta), -cos(theta)) - solving (fx, fy) = (sin(theta),
    -cos(theta)) for theta gives atan2(fx, -fy)."""
    return math.atan2(fx, -fy)


def _build_incident_edges(graph: StreetGraph) -> Dict[int, List[int]]:
    """node_index -> list of indices into graph.edges touching that node."""
    incident: Dict[int, List[int]] = defaultdict(list)
    for i, (a, b, _street_class) in enumerate(graph.edges):
        incident[a].append(i)
        incident[b].append(i)
    return incident


def _find_through_pair(node: int, incident_edge_indices: List[int], graph: StreetGraph):
    """Return the set of {edge_index, edge_index} for the pair of incident
    edges whose outward-from-node directions are closest to exactly
    opposite - treated as one continuous priority route that doesn't stop,
    the same way a real minor intersection has one street that "keeps
    going" without a stop sign. Returns an empty set if there are fewer
    than 2 usable directions or no pair is within
    _THROUGH_ROUTE_DOT_THRESHOLD of opposite."""
    node_pos = graph.nodes[node]
    directions = []
    for edge_index in incident_edge_indices:
        a, b, _street_class = graph.edges[edge_index]
        other = b if a == node else a
        other_pos = graph.nodes[other]
        dx = node_pos[0] - other_pos[0]
        dy = node_pos[1] - other_pos[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        directions.append((edge_index, dx / length, dy / length))

    best_pair = None
    best_dot = 1.0
    for i in range(len(directions)):
        for j in range(i + 1, len(directions)):
            _, ax1, ay1 = directions[i]
            _, ax2, ay2 = directions[j]
            dot = ax1 * ax2 + ay1 * ay2
            if dot < best_dot:
                best_dot = dot
                best_pair = (directions[i][0], directions[j][0])

    if best_pair is not None and best_dot < _THROUGH_ROUTE_DOT_THRESHOLD:
        return {best_pair[0], best_pair[1]}
    return set()


def plan_signage(graph: StreetGraph, preset, seed=None, terrain_params=None,
                  building_plans: List[BuildingPlan] = None,
                  prop_placements: List[PropPlacement] = None) -> List[SignPlacement]:
    """Compute collision-free stop/speed-limit/street-name sign placements.

    Driven entirely by the street graph's own edges/node_degree - no
    block/zone input, unlike plan_props, since none of the three sign
    types are zone-driven. `building_plans`/`prop_placements` are only
    used to pre-register existing geometry in the collision grid so a
    sign never lands on top of a building or an already-planned prop."""
    from .. import terrain as terrain_gen
    from ...assets import library

    rng = random.Random((preset.seed if seed is None else seed) + _SIGNAGE_SEED_OFFSET)
    grid = SpatialHashGrid(cell_size=5.0)
    placements: List[SignPlacement] = []

    for index, plan in enumerate(building_plans or []):
        cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
        cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
        radius = max(math.hypot(p[0] - cx, p[1] - cy) for p in plan.footprint)
        grid.insert(f"building_{index}", cx, cy, radius)

    for index, placement in enumerate(prop_placements or []):
        px, py, _pz = placement.location
        radius = library.get_asset(placement.asset_id).footprint_radius
        grid.insert(f"prop_{index}", px, py, radius)

    def height_at(x, y):
        if terrain_params is None:
            return 0.0
        # +0.02: a sign post's flat bottom cap sits exactly at ground
        # height otherwise, coplanar with the terrain surface there and
        # prone to the same z-fighting streets.py's own +0.05 road-strip
        # epsilon avoids (smaller here since posts are near-vertical, not
        # a flat strip lying full-face against the terrain).
        return terrain_gen.sample_world_height(x, y, terrain_params) + 0.02

    def try_place(kind, x, y, rotation_z, text=""):
        radius = _SIGN_FOOTPRINT_RADIUS[kind]
        if grid.has_collision(x, y, radius):
            return False
        grid.insert(f"{kind}_{len(placements)}", x, y, radius)
        placements.append(SignPlacement(kind, (x, y, height_at(x, y)), rotation_z, text))
        return True

    incident = _build_incident_edges(graph)

    # Stop signs: every local approach at a real (degree >= 3) intersection,
    # except the one pair of edges forming the priority "through" route
    # (_find_through_pair) - that route never stops, matching how a real
    # minor junction works, and meaningfully cutting down how many stop
    # signs cluster at ordinary intersections. An all-arterial intersection
    # gets a traffic light instead (below), not stop signs.
    for node, degree in graph.node_degree.items():
        if degree < 3:
            continue
        node_pos = graph.nodes[node]
        through_pair = _find_through_pair(node, incident[node], graph)
        for edge_index in incident[node]:
            if edge_index in through_pair:
                continue
            a, b, street_class = graph.edges[edge_index]
            if street_class != "local":
                continue
            other = b if a == node else a
            other_pos = graph.nodes[other]
            dx, dy = node_pos[0] - other_pos[0], node_pos[1] - other_pos[1]
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue
            ax, ay = dx / length, dy / length
            perp_x, perp_y = -ay, ax
            half_width = preset.street_width_local / 2.0
            # Clamped to a fraction of this edge's own length - otherwise a
            # short edge (a tight jittered block corner, a short cross
            # street) could push the sign's setback past the edge's other
            # endpoint entirely, landing it off the street altogether.
            setback = min(max(preset.street_width_arterial, preset.street_width_local) / 2.0 + 1.5,
                          length * 0.4)
            side_offset = half_width + 1.0
            x = node_pos[0] - ax * setback + perp_x * side_offset
            y = node_pos[1] - ay * setback + perp_y * side_offset
            rotation_z = _facing_to_rotation_z(-ax, -ay)  # faces back toward `other`, the oncoming driver
            try_place("stop", x, y, rotation_z)

    # Traffic lights: any intersection with 2+ arterial incident edges (an
    # arterial-arterial crossing) gets one instead of relying on stop
    # signs or nothing - real crossings like this are normally signaled.
    for node, degree in graph.node_degree.items():
        if degree < 3:
            continue
        node_pos = graph.nodes[node]
        arterial_edges = [e for e in incident[node] if graph.edges[e][2] == "arterial"]
        if len(arterial_edges) < 2:
            continue
        a, b, _street_class = graph.edges[arterial_edges[0]]
        other = b if a == node else a
        other_pos = graph.nodes[other]
        dx, dy = node_pos[0] - other_pos[0], node_pos[1] - other_pos[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        ax, ay = dx / length, dy / length
        perp_x, perp_y = -ay, ax
        offset = min(max(preset.street_width_arterial, preset.street_width_local) / 2.0 + 1.5, length * 0.4)
        x = node_pos[0] + perp_x * offset
        y = node_pos[1] + perp_y * offset
        try_place("traffic_light", x, y, rotation_z=0.0)

    # Speed-limit repeater signs: per-edge, independent of prop_density
    # (a traffic-logic rule, not a decorative-density knob).
    for a, b, street_class in graph.edges:
        start, end = graph.nodes[a], graph.nodes[b]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length < _SPEED_SIGN_MIN_EDGE_LENGTH:
            continue
        dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        perp_x, perp_y = -dy, dx
        half_width = (preset.street_width_arterial if street_class == "arterial"
                      else preset.street_width_local) / 2.0
        side_offset = half_width + 1.0
        n_signs = max(1, int(length / _SPEED_SIGN_INTERVAL[street_class]))
        for i in range(n_signs):
            t = (i + 0.5) / n_signs
            x = start[0] + dx * length * t + perp_x * side_offset
            y = start[1] + dy * length * t + perp_y * side_offset
            rotation_z = _facing_to_rotation_z(dx, dy)  # faces traffic moving start -> end
            try_place("speed_limit", x, y, rotation_z, text=str(SPEED_LIMIT_KMH[street_class]))

    # Street-name signs: one name per arterial edge (so both ends agree),
    # posted at every real intersection the edge touches, on the curb
    # opposite the stop-sign slot and facing across the road.
    arterial_edge_names: Dict[int, str] = {}
    for edge_index, (a, b, street_class) in enumerate(graph.edges):
        if street_class == "arterial":
            arterial_edge_names[edge_index] = (f"{rng.choice(_STREET_NAME_WORDS)} "
                                                f"{rng.choice(_STREET_NAME_SUFFIXES)}")

    for node, degree in graph.node_degree.items():
        if degree < 3:
            continue
        node_pos = graph.nodes[node]
        for edge_index in incident[node]:
            a, b, street_class = graph.edges[edge_index]
            if street_class != "arterial":
                continue
            other = b if a == node else a
            other_pos = graph.nodes[other]
            dx, dy = node_pos[0] - other_pos[0], node_pos[1] - other_pos[1]
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue
            ax, ay = dx / length, dy / length
            perp_x, perp_y = -ay, ax
            half_width = preset.street_width_arterial / 2.0
            setback = min(max(preset.street_width_arterial, preset.street_width_local) / 2.0 + 1.5,
                          length * 0.4)
            side_offset = half_width + 1.0
            x = node_pos[0] - ax * setback - perp_x * side_offset   # opposite curb from the stop-sign slot
            y = node_pos[1] - ay * setback - perp_y * side_offset
            rotation_z = _facing_to_rotation_z(perp_x, perp_y)      # faces across the road
            try_place("street_name", x, y, rotation_z, text=arterial_edge_names[edge_index])

    return placements


def _get_or_create_signage_material(key, color, emission_strength=0.0):
    import bpy

    name = f"ProcgenMaps_Signage_{key}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        if emission_strength > 0.0:
            bsdf.inputs["Emission Color"].default_value = color
            bsdf.inputs["Emission Strength"].default_value = emission_strength
    return mat


def _assign_material_slots(mesh, pole_mat, board_mat, board_face_start):
    mesh.materials.append(pole_mat)
    mesh.materials.append(board_mat)
    for index, polygon in enumerate(mesh.polygons):
        polygon.material_index = 1 if index >= board_face_start else 0


def _post_mesh_data(pole_height):
    hp = _POLE_HALF_WIDTH
    verts = [
        (-hp, -hp, 0.0), (hp, -hp, 0.0), (hp, hp, 0.0), (-hp, hp, 0.0),
        (-hp, -hp, pole_height), (hp, -hp, pole_height), (hp, hp, pole_height), (-hp, hp, pole_height),
    ]
    faces = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (0, 3, 2, 1), (4, 5, 6, 7)]
    return verts, faces


def _rect_prism_data(width, depth, height, x_offset=0.0, y_offset=0.0, z0=0.0):
    """(verts, faces) for an axis-aligned box: width along local X, depth
    along local Y, height along local Z, bottom at z0, centered at
    (x_offset, y_offset) in XY. Used for the traffic light's head and lens
    insets, where several differently-sized/positioned boxes need to be
    combined into one mesh (unlike the single-board signs' fixed layout)."""
    hw, hd = width / 2.0, depth / 2.0
    z1 = z0 + height
    verts = [
        (x_offset - hw, y_offset - hd, z0), (x_offset + hw, y_offset - hd, z0),
        (x_offset + hw, y_offset + hd, z0), (x_offset - hw, y_offset + hd, z0),
        (x_offset - hw, y_offset - hd, z1), (x_offset + hw, y_offset - hd, z1),
        (x_offset + hw, y_offset + hd, z1), (x_offset - hw, y_offset + hd, z1),
    ]
    faces = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (0, 3, 2, 1), (4, 5, 6, 7)]
    return verts, faces


def _build_post_and_rect_board_mesh(name, pole_height, board_width, board_height, board_thickness):
    """Post + a flat rectangular board, front (readable) face at local
    y = -board_thickness/2 - the -Y-default side per _facing_to_rotation_z's
    convention. Combined into one mesh: material slot 0 = post (polygons
    0-5), slot 1 = board (polygons 6+)."""
    import bpy

    verts_post, faces_post = _post_mesh_data(pole_height)

    bw2, bt2 = board_width / 2.0, board_thickness / 2.0
    z0, z1 = pole_height, pole_height + board_height
    verts_board = [
        (-bw2, -bt2, z0), (bw2, -bt2, z0), (bw2, bt2, z0), (-bw2, bt2, z0),
        (-bw2, -bt2, z1), (bw2, -bt2, z1), (bw2, bt2, z1), (-bw2, bt2, z1),
    ]
    faces_board = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (0, 3, 2, 1), (4, 5, 6, 7)]

    verts = verts_post + verts_board
    faces = faces_post + [tuple(i + 8 for i in f) for f in faces_board]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    return mesh


def _build_octagon_post_and_board_mesh(name, pole_height, board_radius, board_thickness, sides=8):
    """Post + a flat, upright octagonal board (the stop-sign shape), front
    face at local y = -board_thickness/2, same -Y-default convention.
    Returns (mesh, board_center_z)."""
    import bpy

    verts_post, faces_post = _post_mesh_data(pole_height)

    board_center_z = pole_height + board_radius
    bt2 = board_thickness / 2.0
    ring_front, ring_back = [], []
    for i in range(sides):
        angle = -math.pi / 2.0 + i * (2 * math.pi / sides)
        vx = board_radius * math.cos(angle)
        vz = board_center_z + board_radius * math.sin(angle)
        ring_front.append((vx, -bt2, vz))
        ring_back.append((vx, bt2, vz))
    verts_board = ring_front + ring_back
    faces_board = [tuple(range(sides)), tuple(reversed(range(sides, 2 * sides)))]
    for i in range(sides):
        j = (i + 1) % sides
        faces_board.append((i, j, sides + j, sides + i))

    verts = verts_post + verts_board
    faces = faces_post + [tuple(i + 8 for i in f) for f in faces_board]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    return mesh, board_center_z


def _build_sign_text(name, placement: SignPlacement, text, material_key, color,
                      board_center_z, front_offset, size_cap, board_width, min_chars, emission_strength=0.6):
    """A FONT object mounted just proud of a board's front (-Y-default)
    face, offset and rotated to match the board's own world position/yaw -
    mirrors special_buildings.py's _build_sign idiom, generalized to an
    arbitrary yaw instead of a fixed -Y facing."""
    import bpy

    x, y, z = placement.location
    rotation_z = placement.rotation_z
    cos_r, sin_r = math.cos(rotation_z), math.sin(rotation_z)
    local_x, local_y = 0.0, -front_offset
    world_dx = local_x * cos_r - local_y * sin_r
    world_dy = local_x * sin_r + local_y * cos_r

    curve = bpy.data.curves.new(name, type='FONT')
    curve.body = text
    curve.size = min(size_cap, board_width * 0.8 / max(min_chars, len(text)))
    curve.align_x = 'CENTER'
    curve.align_y = 'CENTER'
    curve.extrude = 0.02
    curve.materials.append(_get_or_create_signage_material(material_key, color, emission_strength))

    obj = bpy.data.objects.new(name, curve)
    obj.location = (x + world_dx, y + world_dy, z + board_center_z)
    obj.rotation_euler = (math.pi / 2.0, 0.0, rotation_z)
    obj["procgen_maps_sign_kind"] = material_key
    return obj


def _build_stop_sign(name, placement: SignPlacement):
    import bpy

    mesh, board_center_z = _build_octagon_post_and_board_mesh(
        name, _STOP_SIGN_POLE_HEIGHT, _STOP_SIGN_BOARD_RADIUS, _STOP_SIGN_BOARD_THICKNESS)
    pole_mat = _get_or_create_signage_material("pole", (0.25, 0.25, 0.25, 1.0))
    board_mat = _get_or_create_signage_material("stop_board", (0.75, 0.05, 0.05, 1.0))
    _assign_material_slots(mesh, pole_mat, board_mat, board_face_start=6)

    obj = bpy.data.objects.new(name, mesh)
    obj.location = placement.location
    obj.rotation_euler = (0.0, 0.0, placement.rotation_z)
    obj["procgen_maps_sign_kind"] = "stop"

    text_obj = _build_sign_text(f"{name}_Text", placement, "STOP", "stop_text", (0.95, 0.95, 0.95, 1.0),
                                 board_center_z=board_center_z,
                                 front_offset=_STOP_SIGN_BOARD_THICKNESS / 2.0 + 0.02,
                                 size_cap=0.32, board_width=_STOP_SIGN_BOARD_RADIUS * 1.6, min_chars=4)
    return [obj, text_obj]


def _build_speed_limit_sign(name, placement: SignPlacement):
    import bpy

    mesh = _build_post_and_rect_board_mesh(name, _SPEED_SIGN_POLE_HEIGHT, _SPEED_SIGN_BOARD_WIDTH,
                                            _SPEED_SIGN_BOARD_HEIGHT, _SPEED_SIGN_BOARD_THICKNESS)
    pole_mat = _get_or_create_signage_material("pole", (0.25, 0.25, 0.25, 1.0))
    board_mat = _get_or_create_signage_material("speed_limit_board", (0.95, 0.95, 0.95, 1.0))
    _assign_material_slots(mesh, pole_mat, board_mat, board_face_start=6)

    obj = bpy.data.objects.new(name, mesh)
    obj.location = placement.location
    obj.rotation_euler = (0.0, 0.0, placement.rotation_z)
    obj["procgen_maps_sign_kind"] = "speed_limit"

    board_center_z = _SPEED_SIGN_POLE_HEIGHT + _SPEED_SIGN_BOARD_HEIGHT / 2.0
    text_obj = _build_sign_text(f"{name}_Text", placement, placement.text, "speed_limit_text",
                                 (0.05, 0.05, 0.05, 1.0), board_center_z=board_center_z,
                                 front_offset=_SPEED_SIGN_BOARD_THICKNESS / 2.0 + 0.02,
                                 size_cap=0.4, board_width=_SPEED_SIGN_BOARD_WIDTH, min_chars=2)
    return [obj, text_obj]


def _build_street_name_sign(name, placement: SignPlacement):
    import bpy

    mesh = _build_post_and_rect_board_mesh(name, _NAME_SIGN_POLE_HEIGHT, _NAME_SIGN_BOARD_WIDTH,
                                            _NAME_SIGN_BOARD_HEIGHT, _NAME_SIGN_BOARD_THICKNESS)
    pole_mat = _get_or_create_signage_material("pole", (0.25, 0.25, 0.25, 1.0))
    board_mat = _get_or_create_signage_material("street_name_board", (0.05, 0.35, 0.15, 1.0))
    _assign_material_slots(mesh, pole_mat, board_mat, board_face_start=6)

    obj = bpy.data.objects.new(name, mesh)
    obj.location = placement.location
    obj.rotation_euler = (0.0, 0.0, placement.rotation_z)
    obj["procgen_maps_sign_kind"] = "street_name"

    board_center_z = _NAME_SIGN_POLE_HEIGHT + _NAME_SIGN_BOARD_HEIGHT / 2.0
    text_obj = _build_sign_text(f"{name}_Text", placement, placement.text, "street_name_text",
                                 (0.95, 0.95, 0.9, 1.0), board_center_z=board_center_z,
                                 front_offset=_NAME_SIGN_BOARD_THICKNESS / 2.0 + 0.02,
                                 size_cap=0.22, board_width=_NAME_SIGN_BOARD_WIDTH, min_chars=6)
    return [obj, text_obj]


def _build_traffic_light(name, placement: SignPlacement):
    """Post + a dark head box + 3 stacked red/yellow/green lens insets on
    its front (-Y-default) face - decorative only, no simulation, but a
    recognizable traffic light silhouette rather than a plain box."""
    import bpy

    verts_post, faces_post = _post_mesh_data(_TRAFFIC_LIGHT_POLE_HEIGHT)
    all_verts = list(verts_post)
    all_faces = list(faces_post)

    def _append(verts, faces):
        offset = len(all_verts)
        all_verts.extend(verts)
        all_faces.extend(tuple(i + offset for i in f) for f in faces)

    head_verts, head_faces = _rect_prism_data(_TRAFFIC_LIGHT_HEAD_WIDTH, _TRAFFIC_LIGHT_HEAD_DEPTH,
                                               _TRAFFIC_LIGHT_HEAD_HEIGHT, z0=_TRAFFIC_LIGHT_POLE_HEIGHT)
    _append(head_verts, head_faces)

    lens_depth_y = -(_TRAFFIC_LIGHT_HEAD_DEPTH / 2.0 + _TRAFFIC_LIGHT_LENS_THICKNESS / 2.0)
    lens_z_centers = [
        _TRAFFIC_LIGHT_POLE_HEIGHT + _TRAFFIC_LIGHT_HEAD_HEIGHT * 0.78,
        _TRAFFIC_LIGHT_POLE_HEIGHT + _TRAFFIC_LIGHT_HEAD_HEIGHT * 0.50,
        _TRAFFIC_LIGHT_POLE_HEIGHT + _TRAFFIC_LIGHT_HEAD_HEIGHT * 0.22,
    ]
    lens_face_starts = []
    for lens_z_center in lens_z_centers:
        lens_face_starts.append(len(all_faces))
        lens_verts, lens_faces = _rect_prism_data(
            _TRAFFIC_LIGHT_LENS_SIZE, _TRAFFIC_LIGHT_LENS_THICKNESS, _TRAFFIC_LIGHT_LENS_SIZE,
            y_offset=lens_depth_y, z0=lens_z_center - _TRAFFIC_LIGHT_LENS_SIZE / 2.0)
        _append(lens_verts, lens_faces)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(all_verts, [], all_faces)
    mesh.update(calc_edges=True)

    body_mat = _get_or_create_signage_material("traffic_light_body", (0.08, 0.08, 0.08, 1.0))
    red_mat = _get_or_create_signage_material("traffic_light_red", (0.85, 0.1, 0.05, 1.0), emission_strength=1.0)
    yellow_mat = _get_or_create_signage_material("traffic_light_yellow", (0.9, 0.75, 0.05, 1.0),
                                                  emission_strength=0.4)
    green_mat = _get_or_create_signage_material("traffic_light_green", (0.05, 0.65, 0.15, 1.0),
                                                 emission_strength=0.4)
    for mat in (body_mat, red_mat, yellow_mat, green_mat):
        mesh.materials.append(mat)

    for index, polygon in enumerate(mesh.polygons):
        if index < lens_face_starts[0]:
            polygon.material_index = 0  # pole + head
        elif index < lens_face_starts[1]:
            polygon.material_index = 1  # red
        elif index < lens_face_starts[2]:
            polygon.material_index = 2  # yellow
        else:
            polygon.material_index = 3  # green

    obj = bpy.data.objects.new(name, mesh)
    obj.location = placement.location
    obj.rotation_euler = (0.0, 0.0, placement.rotation_z)
    obj["procgen_maps_sign_kind"] = "traffic_light"
    return [obj]


_BUILDERS = {
    "stop": _build_stop_sign,
    "speed_limit": _build_speed_limit_sign,
    "street_name": _build_street_name_sign,
    "traffic_light": _build_traffic_light,
}


def build_signage_meshes(placements: List[SignPlacement], collection=None):
    """Build a post+board(+FONT text) object per placement. Returns every
    created object (mesh post/board and its companion FONT text)."""
    created = []
    for index, placement in enumerate(placements):
        name = f"{SIGNAGE_PREFIX}_{index}_{placement.kind}"
        for obj in _BUILDERS[placement.kind](name, placement):
            if collection is not None:
                collection.objects.link(obj)
            created.append(obj)
    return created
