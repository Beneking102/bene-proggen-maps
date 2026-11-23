"""Map validation: finds overlaps, gaps, disconnected streets and (inside
Blender) floating/buried objects and orphan datablocks.

Split per the project's PP/BPY convention (see ARCHITECTURE.md):
`validate_city_plan` works on the pure-Python `CityPlan` that
generators/city/__init__.py's `plan_city` returns, so the multi-seed fuzz
tests can run it over hundreds of (preset, seed) pairs without Blender.
`validate_scene` adds the checks that need the built scene (object z
against the terrain, orphan meshes/materials/collections after a
regenerate). ui/operators.py's "Validate Map" button runs both and
prints the report.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# Two overlapping shapes within this many meters still count as clean -
# everything placed on the 0.5 m street snap grid is accurate to far
# better than this, so anything bigger is a real intrusion.
_EPSILON = 0.02


@dataclass
class ValidationIssue:
    kind: str          # e.g. 'building_overlap', 'prop_on_road'
    message: str
    location: Tuple[float, float] = (0.0, 0.0)


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)
    checked: Dict[str, int] = field(default_factory=dict)   # kind -> how many items were examined

    def add(self, kind, message, location=(0.0, 0.0)):
        self.issues.append(ValidationIssue(kind, message, location))

    def count(self, kind: str) -> int:
        return sum(1 for issue in self.issues if issue.kind == kind)

    def counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for issue in self.issues:
            counts[issue.kind] += 1
        return dict(counts)

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        if not self.issues:
            return "OK - no issues"
        parts = [f"{kind}: {n}" for kind, n in sorted(self.counts().items())]
        return f"{len(self.issues)} issue(s) - " + ", ".join(parts)

    def format(self, max_lines: int = 40) -> str:
        lines = [self.summary()]
        if self.checked:
            lines.append("checked: " + ", ".join(f"{k}={v}" for k, v in sorted(self.checked.items())))
        for issue in self.issues[:max_lines]:
            lines.append(f"  [{issue.kind}] {issue.message} @ ({issue.location[0]:.1f}, {issue.location[1]:.1f})")
        if len(self.issues) > max_lines:
            lines.append(f"  ... {len(self.issues) - max_lines} more")
        return "\n".join(lines)


# --- pure geometry helpers -------------------------------------------------

def _rect_of(footprint) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in footprint]
    ys = [p[1] for p in footprint]
    return (min(xs), min(ys), max(xs), max(ys))


def _rects_overlap(a, b, eps=_EPSILON) -> bool:
    return a[0] < b[2] - eps and b[0] < a[2] - eps and a[1] < b[3] - eps and b[1] < a[3] - eps


def _point_in_rect(x, y, rect, eps=_EPSILON) -> bool:
    return rect[0] + eps < x < rect[2] - eps and rect[1] + eps < y < rect[3] - eps


def _point_segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def street_components(graph) -> List[int]:
    """Sizes (node counts) of the street graph's connected components,
    largest first. A clean city has exactly one."""
    adjacency = defaultdict(set)
    for a, b, _cls in graph.edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen = set()
    sizes = []
    for start in range(len(graph.nodes)):
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        size = 0
        while stack:
            node = stack.pop()
            size += 1
            for other in adjacency[node]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        sizes.append(size)
    return sorted(sizes, reverse=True)


class _RoadIndex:
    """Roads bucketed into coarse cells so a point/rect query only visits
    nearby segments (a Metropole has ~900 road edges and ~5000 props)."""

    def __init__(self, graph, preset, cell=40.0):
        self.cell = cell
        self.roads = []
        self._cells = defaultdict(list)
        for (a, b, street_class) in graph.edges:
            width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
            (ax, ay), (bx, by) = graph.nodes[a], graph.nodes[b]
            index = len(self.roads)
            self.roads.append((ax, ay, bx, by, width))
            pad = width / 2.0 + 1.0
            for cx in range(int(math.floor((min(ax, bx) - pad) / cell)),
                            int(math.floor((max(ax, bx) + pad) / cell)) + 1):
                for cy in range(int(math.floor((min(ay, by) - pad) / cell)),
                                int(math.floor((max(ay, by) + pad) / cell)) + 1):
                    self._cells[(cx, cy)].append(index)

    def near_point(self, x, y):
        key = (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))
        return [self.roads[i] for i in self._cells.get(key, ())]

    def near_rect(self, rect):
        seen = set()
        for cx in range(int(math.floor(rect[0] / self.cell)), int(math.floor(rect[2] / self.cell)) + 1):
            for cy in range(int(math.floor(rect[1] / self.cell)), int(math.floor(rect[3] / self.cell)) + 1):
                seen.update(self._cells.get((cx, cy), ()))
        return [self.roads[i] for i in seen]

    def depth_into_road(self, x, y) -> float:
        """How far (meters) the point lies inside the nearest road's asphalt
        (<= 0 when it is off the road)."""
        best = -math.inf
        for (ax, ay, bx, by, width) in self.near_point(x, y):
            best = max(best, width / 2.0 - _point_segment_distance(x, y, ax, ay, bx, by))
        return best


def _rect_road_overlap(rect, roads) -> float:
    """Largest intrusion (meters) of any of `roads` into the rectangle."""
    from .generators.city.setback import rect_segment_distance

    worst = -math.inf
    for (ax, ay, bx, by, width) in roads:
        worst = max(worst, width / 2.0 - rect_segment_distance(rect, (ax, ay, bx, by)))
    return worst


# --- the pure-Python validator --------------------------------------------

def validate_city_plan(plan, preset) -> ValidationReport:
    """Check a `CityPlan` (generators/city/__init__.py) for every class of
    placement problem the generator can produce: building footprints that
    overlap each other or the road surface, props/signs standing inside a
    building or on the asphalt, props overlapping each other, parking lots
    across a road or a neighbouring building, and a street graph that is
    not one connected network."""
    from .assets import library

    report = ValidationReport()
    graph = plan.street_graph
    roads = _RoadIndex(graph, preset)

    # Buildings vs buildings (sweep over sorted min_x) and vs roads.
    footprints = [(index, _rect_of(p.footprint), p) for index, p in enumerate(plan.all_building_plans)]
    report.checked["buildings"] = len(footprints)
    by_min_x = sorted(footprints, key=lambda item: item[1][0])
    for i, (index_a, rect_a, plan_a) in enumerate(by_min_x):
        center = ((rect_a[0] + rect_a[2]) / 2.0, (rect_a[1] + rect_a[3]) / 2.0)
        for index_b, rect_b, plan_b in by_min_x[i + 1:]:
            if rect_b[0] >= rect_a[2] - _EPSILON:
                break
            if _rects_overlap(rect_a, rect_b):
                report.add("building_overlap",
                           f"building {index_a} ({plan_a.facade.key}) overlaps building {index_b} ({plan_b.facade.key})",
                           center)
        intrusion = _rect_road_overlap(rect_a, roads.near_rect(rect_a))
        if intrusion > _EPSILON:
            report.add("building_on_road",
                       f"building {index_a} ({plan_a.facade.key}) is {intrusion:.2f} m inside a road", center)

    building_rects = [rect for _, rect, _ in footprints]
    rect_cells = defaultdict(list)
    cell = 40.0
    for rect in building_rects:
        for cx in range(int(math.floor(rect[0] / cell)), int(math.floor(rect[2] / cell)) + 1):
            for cy in range(int(math.floor(rect[1] / cell)), int(math.floor(rect[3] / cell)) + 1):
                rect_cells[(cx, cy)].append(rect)

    def building_rects_near(x, y):
        return rect_cells.get((int(math.floor(x / cell)), int(math.floor(y / cell))), ())

    # Props: inside a building, on the asphalt (cars excepted - they park
    # on it by design), or overlapping another prop.
    report.checked["props"] = len(plan.prop_placements)
    placed: Dict[Tuple[int, int], List[Tuple[int, float, float, float]]] = defaultdict(list)
    prop_cell = 10.0
    for index, placement in enumerate(plan.prop_placements):
        x, y, _z = placement.location
        asset = library.get_asset(placement.asset_id)
        radius = asset.footprint_radius * placement.scale
        if any(_point_in_rect(x, y, rect) for rect in building_rects_near(x, y)):
            report.add("prop_in_building", f"{placement.asset_id} stands inside a building", (x, y))
        if asset.category != "car":
            depth = roads.depth_into_road(x, y)
            if depth > _EPSILON:
                report.add("prop_on_road", f"{placement.asset_id} stands {depth:.2f} m onto the asphalt", (x, y))
        key = (int(math.floor(x / prop_cell)), int(math.floor(y / prop_cell)))
        for cx in (key[0] - 1, key[0], key[0] + 1):
            for cy in (key[1] - 1, key[1], key[1] + 1):
                for other_index, ox, oy, other_radius in placed.get((cx, cy), ()):
                    if math.hypot(ox - x, oy - y) < radius + other_radius - _EPSILON:
                        report.add("prop_overlap",
                                   f"{placement.asset_id} overlaps {plan.prop_placements[other_index].asset_id}",
                                   (x, y))
        placed[key].append((index, x, y, radius))

    # Signs: inside a building or on the asphalt.
    report.checked["signs"] = len(plan.sign_placements)
    for placement in plan.sign_placements:
        x, y, _z = placement.location
        if any(_point_in_rect(x, y, rect) for rect in building_rects_near(x, y)):
            report.add("sign_in_building", f"{placement.kind} sign stands inside a building", (x, y))
        depth = roads.depth_into_road(x, y)
        if depth > _EPSILON:
            report.add("sign_on_road", f"{placement.kind} sign stands {depth:.2f} m onto the asphalt", (x, y))

    # Parking lots: across a road, or into a building other than their own.
    report.checked["parking_lots"] = len(plan.parking_lot_specs)
    for spec in plan.parking_lot_specs:
        rect = (spec.cx - spec.half_width, spec.cy - spec.half_depth,
                spec.cx + spec.half_width, spec.cy + spec.half_depth)
        intrusion = _rect_road_overlap(rect, roads.near_rect(rect))
        if intrusion > _EPSILON:
            report.add("parking_on_road",
                       f"parking lot of block {spec.block_id} is {intrusion:.2f} m inside a road", (spec.cx, spec.cy))
        for _index, other_rect, other_plan in footprints:
            if other_plan.block_id != spec.block_id and _rects_overlap(rect, other_rect):
                report.add("parking_in_building",
                           f"parking lot of block {spec.block_id} overlaps a building", (spec.cx, spec.cy))

    # Water: no building, tree, bench or fountain in the river or on its
    # banks (lamps and cars on a bridge deck are fine); at least one bridge.
    river = getattr(plan.layout, "river", None)
    if river is not None:
        from .generators.city.water import bridge_edges

        report.checked["river_corridors"] = len(river.points) - 1
        for index, rect, building in footprints:
            corners = ((rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3]),
                       ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0))
            if any(river.contains(x, y) for x, y in corners):
                report.add("building_in_water", f"building {index} ({building.facade.key}) stands in the river",
                           corners[4])
        for placement in plan.prop_placements:
            x, y, _z = placement.location
            if library.get_asset(placement.asset_id).category in ("lamp", "car"):
                continue
            if river.contains(x, y, river.bank_margin):
                report.add("prop_in_water", f"{placement.asset_id} stands in the river or on its bank", (x, y))
        for placement in plan.sign_placements:
            x, y, _z = placement.location
            if river.contains(x, y, river.bank_margin):
                report.add("sign_in_water", f"{placement.kind} sign stands in the river or on its bank", (x, y))
        if not bridge_edges(graph, river):
            report.add("river_without_bridge", "no street crosses the river")

    # Park paths: trees never stand on a path (plaza rims have planted trees).
    park_paths = [spec for spec in (getattr(plan, "park_specs", None) or []) if spec.kind == "park"]
    if park_paths:
        report.checked["parks"] = len(park_paths)
        for placement in plan.prop_placements:
            x, y, _z = placement.location
            if library.get_asset(placement.asset_id).category != "tree":
                continue
            if any(spec.on_paving(x, y) for spec in park_paths):
                report.add("tree_on_path", f"{placement.asset_id} stands on a park path", (x, y))

    # Street network: one connected component, no degenerate edges.
    report.checked["street_edges"] = len(graph.edges)
    components = street_components(graph)
    if len(components) > 1:
        report.add("disconnected_streets",
                   f"street network has {len(components)} components (sizes {components[:5]})")
    for a, b, _cls in graph.edges:
        (ax, ay), (bx, by) = graph.nodes[a], graph.nodes[b]
        if math.hypot(bx - ax, by - ay) < 1.0:
            report.add("degenerate_street", f"street edge {a}-{b} is shorter than 1 m", (ax, ay))

    return report


# --- bpy-side checks ------------------------------------------------------

def validate_scene(terrain_params=None, report: ValidationReport = None) -> ValidationReport:
    """Checks that need the built Blender scene: generated objects floating
    above or buried in the terrain, and orphan datablocks (meshes,
    materials, lights, curves, collections with no users) left behind by a
    regenerate. Adds to `report` if given."""
    import bpy

    from .assets import library
    from .generators import terrain as terrain_gen
    from .generators.city.streets import ROAD_LIFT, SIDEWALK_SKIRT

    report = report if report is not None else ValidationReport()

    if terrain_params is not None and bpy.data.objects.get(terrain_gen.TERRAIN_OBJECT_NAME) is not None:
        checked = 0
        for obj in bpy.data.objects:
            asset_id = obj.get("procgen_maps_asset_id")
            if asset_id is not None and not obj.name.startswith("ProcgenMaps_AssetMaster"):
                category = library.get_asset(asset_id).category
                if category in ("furniture", "rooftop"):
                    continue   # placed on floors/roofs, not on the ground
                expected = terrain_gen.sample_world_height(obj.location.x, obj.location.y, terrain_params)
                checked += 1
                _check_height(report, obj, expected, tolerance=0.3)
            elif obj.get("procgen_maps_facade_type") is not None and obj.get("procgen_maps_base_z") is not None:
                if obj.get("procgen_maps_center_x") is not None:
                    cx, cy = float(obj["procgen_maps_center_x"]), float(obj["procgen_maps_center_y"])
                else:
                    # Older meshes: the bounding box centre (no balconies/awnings then).
                    xs = [v.co.x for v in obj.data.vertices]
                    ys = [v.co.y for v in obj.data.vertices]
                    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
                expected = terrain_gen.sample_world_height(cx, cy, terrain_params)
                checked += 1
                _check_height(report, obj, expected, tolerance=0.05, actual=float(obj["procgen_maps_base_z"]))
            elif obj.get("procgen_maps_street_class") is not None:
                checked += 1
                worst = 0.0
                for vertex in obj.data.vertices:
                    expected = terrain_gen.sample_world_height(vertex.co.x, vertex.co.y, terrain_params)
                    # Asphalt sits ROAD_LIFT above the ground, sidewalks a curb
                    # higher, and the sidewalk skirt reaches SIDEWALK_SKIRT down.
                    delta = vertex.co.z - expected - ROAD_LIFT
                    worst = max(worst, delta - 0.2, -delta - SIDEWALK_SKIRT - 0.2)
                if worst > 0.0:
                    report.add("street_off_ground", f"{obj.name} deviates {worst:.2f} m from the terrain",
                               (obj.location.x, obj.location.y))
        report.checked["ground_contact"] = checked

    if bpy.data.objects.get(terrain_gen.TERRAIN_OBJECT_NAME) is not None:
        above, checked = terrain_above_roads()
        report.checked["road_cover"] = checked
        for x, y, depth in above[:50]:
            report.add("terrain_above_road", f"terrain pokes {depth:.2f} m through a street surface", (x, y))
        if len(above) > 50:
            report.add("terrain_above_road", f"... {len(above) - 50} more terrain vertices above a street surface")

    report.checked["datablocks"] = sum(len(getattr(bpy.data, attr))
                                       for attr in ("meshes", "materials", "lights", "curves", "collections"))
    for kind, names in orphan_datablocks().items():
        for name in names:
            report.add("orphan_datablock", f"{kind} '{name}' has no users")
    return report


def terrain_above_roads(tolerance=0.005):
    """Every terrain vertex that lies above a street surface (asphalt strip,
    intersection fan or sidewalk) directly over it - the ground showing
    through the road. Casts a ray straight down from far above each
    terrain vertex against one BVH of all street geometry; a hit below the
    vertex (by more than `tolerance`) is a poke-through. Returns
    ([(x, y, depth), ...], vertices_checked). Only vertices inside some
    street object's bounding box are tested (the rest cannot hit)."""
    import bpy
    import numpy as np
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    from .generators import terrain as terrain_gen
    from .generators.city.streets import STREETS_PREFIX

    terrain = bpy.data.objects.get(terrain_gen.TERRAIN_OBJECT_NAME)
    streets = [obj for obj in bpy.data.objects if obj.name.startswith(STREETS_PREFIX) and obj.type == 'MESH']
    if terrain is None or not streets:
        return [], 0
    verts, polys, boxes = [], [], []
    for obj in streets:
        mesh = obj.data
        base = len(verts)
        matrix = obj.matrix_world
        obj_verts = [matrix @ v.co for v in mesh.vertices]
        verts.extend(obj_verts)
        polys.extend([tuple(base + i for i in poly.vertices) for poly in mesh.polygons])
        xs = [v.x for v in obj_verts]
        ys = [v.y for v in obj_verts]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    tree = BVHTree.FromPolygons(verts, polys)

    coords = np.empty(len(terrain.data.vertices) * 3, dtype=np.float32)
    terrain.data.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3).astype(np.float64)
    mask = np.zeros(len(coords), dtype=bool)
    for (min_x, min_y, max_x, max_y) in boxes:
        mask |= (coords[:, 0] >= min_x) & (coords[:, 0] <= max_x) & (coords[:, 1] >= min_y) & (coords[:, 1] <= max_y)
    candidates = coords[mask]
    top = float(coords[:, 2].max()) + 100.0
    down = Vector((0.0, 0.0, -1.0))
    above = []
    for x, y, z in candidates:
        hit = tree.ray_cast(Vector((x, y, top)), down)
        if hit[0] is not None and hit[0].z < z - tolerance:
            above.append((float(x), float(y), float(z - hit[0].z)))
    return above, int(len(candidates))


def _check_height(report, obj, expected, tolerance, actual=None):
    z = obj.location.z if actual is None else actual
    delta = z - expected
    if delta > tolerance:
        report.add("floating_object", f"{obj.name} floats {delta:.2f} m above the ground",
                   (obj.location.x, obj.location.y))
    elif delta < -tolerance:
        report.add("buried_object", f"{obj.name} is buried {-delta:.2f} m in the ground",
                   (obj.location.x, obj.location.y))


def _is_procgen_datablock(name: str) -> bool:
    """Only this addon's own datablocks count: a factory-startup file
    already carries unused blocks of its own (e.g. the 'Dots Stroke'
    grease-pencil material) that are not ours to report."""
    return name.startswith("ProcgenMaps") or "_inst" in name or name.endswith("_light")


def orphan_datablocks() -> Dict[str, List[str]]:
    """Names of meshes/materials/lights/curves/node groups/collections with
    zero users (fake users excepted). Materials and node groups are shared
    get_or_create singletons that deliberately survive a regenerate, so
    they only show up here when nothing at all references them anymore."""
    import bpy

    result: Dict[str, List[str]] = {}
    for attr in ("meshes", "materials", "lights", "curves", "node_groups", "collections"):
        names = [block.name for block in getattr(bpy.data, attr)
                 if block.users == 0 and not block.use_fake_user
                 and _is_procgen_datablock(block.name)
                 # assets/factory.py's instance sources are never linked
                 # into the scene on purpose (OPTIMIZATION.md).
                 and not block.name.startswith("ProcgenMaps_AssetSrc_")]
        if names:
            result[attr] = names
    return result
