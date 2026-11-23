"""Building setback from streets: pure-Python rectangle-vs-road-edge
clearance, used by buildings.plan_buildings / special_buildings to keep
every footprint a sidewalk's width away from the nearest road edge.

Before this existed, footprints were only ever a random *fraction* of
their block, while the roads were laid along the block boundaries with
their full width - measured over seed 42, the median Metropole building
had its wall 3.7 m *inside* the road surface (raster blocks touch the
street centerline; even grid-layout arterials overlap the blocks by
~1.7 m). It was invisible as long as roads were untextured white planes
under the buildings, but it leaves no room for the sidewalks/curbs
streets.py now builds, and it is why cars/lamps sat on the grass.

Everything here is plain math on tuples so it is pytest-covered without
Blender (see ARCHITECTURE.md's PP/BPY split).
"""
import math
from typing import Iterable, List, Sequence, Tuple

SIDEWALK_WIDTH = 2.0      # meters; must match streets.py's sidewalk strip
SETBACK_MARGIN = 0.6      # meters of grass/kerb between sidewalk and wall
MIN_HALF_SIZE = 3.0       # meters; never shrink a footprint below a 6 m x 6 m hut


def _point_segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _point_rect_distance(px, py, min_x, min_y, max_x, max_y) -> float:
    dx = max(min_x - px, 0.0, px - max_x)
    dy = max(min_y - py, 0.0, py - max_y)
    return math.hypot(dx, dy)


def _segment_intersects_rect(ax, ay, bx, by, min_x, min_y, max_x, max_y) -> bool:
    """Liang-Barsky clip test: does the segment a-b touch the rectangle?"""
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - min_x), (dx, max_x - ax), (-dy, ay - min_y), (dy, max_y - ay)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t0 <= t1


def rect_segment_distance(rect: Tuple[float, float, float, float],
                          segment: Tuple[float, float, float, float]) -> float:
    """Shortest distance between an axis-aligned rectangle (min_x, min_y,
    max_x, max_y) and a line segment (ax, ay, bx, by); 0 if they touch.
    Both shapes are convex, so when disjoint the minimum is attained at a
    vertex of one of them - checking the 4 corners against the segment and
    the 2 endpoints against the rectangle is exact, not an approximation."""
    min_x, min_y, max_x, max_y = rect
    ax, ay, bx, by = segment
    if _segment_intersects_rect(ax, ay, bx, by, min_x, min_y, max_x, max_y):
        return 0.0
    best = min(_point_rect_distance(ax, ay, *rect), _point_rect_distance(bx, by, *rect))
    for (cx, cy) in ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)):
        best = min(best, _point_segment_distance(cx, cy, ax, ay, bx, by))
    return best


def road_clearance(rect: Tuple[float, float, float, float],
                   roads: Iterable[Tuple[float, float, float, float, float]]) -> float:
    """Minimum over `roads` ((ax, ay, bx, by, width) tuples) of the distance
    from the rectangle to that road's *edge* (negative when the road
    surface already overlaps the footprint). +inf without roads."""
    best = math.inf
    for (ax, ay, bx, by, width) in roads:
        best = min(best, rect_segment_distance(rect, (ax, ay, bx, by)) - width / 2.0)
    return best


def shrink_footprint_for_setback(center: Tuple[float, float], half_size: Tuple[float, float],
                                 roads: Sequence[Tuple[float, float, float, float, float]],
                                 required: float = SIDEWALK_WIDTH + SETBACK_MARGIN,
                                 min_half_size: float = MIN_HALF_SIZE,
                                 max_iterations: int = 8) -> Tuple[float, float]:
    """Return (half_width, half_height) <= `half_size` such that the
    centered rectangle keeps at least `required` meters between its walls
    and every road edge, shrinking both axes by the deficit until it does
    (or until `min_half_size` is hit, for blocks a street actually runs
    through, where no shrink can help). Shrinking toward the center can
    only increase clearance, so the deficit loop converges in a couple of
    steps; the roads list should be prefiltered to the block's own
    neighborhood for speed (see roads_near)."""
    cx, cy = center
    hw, hh = half_size
    for _ in range(max_iterations):
        if hw <= min_half_size and hh <= min_half_size:
            break
        clearance = road_clearance((cx - hw, cy - hh, cx + hw, cy + hh), roads)
        deficit = required - clearance
        if deficit <= 1e-6:
            break
        hw = max(min_half_size, hw - deficit)
        hh = max(min_half_size, hh - deficit)
    return hw, hh


def roads_near(center: Tuple[float, float], half_size: Tuple[float, float],
               roads: Sequence[Tuple[float, float, float, float, float]],
               reach: float = 30.0) -> List[Tuple[float, float, float, float, float]]:
    """Prefilter `roads` to those whose bounding box comes within `reach`
    of the block's own bounding box - a cheap AABB test so a 200-building
    city doesn't do 200 x 600 exact distance evaluations per iteration."""
    cx, cy = center
    hw, hh = half_size
    min_x, max_x = cx - hw - reach, cx + hw + reach
    min_y, max_y = cy - hh - reach, cy + hh + reach
    near = []
    for road in roads:
        ax, ay, bx, by, width = road
        pad = width / 2.0
        if max(ax, bx) + pad < min_x or min(ax, bx) - pad > max_x:
            continue
        if max(ay, by) + pad < min_y or min(ay, by) - pad > max_y:
            continue
        near.append(road)
    return near


def roads_from_graph(graph, preset) -> List[Tuple[float, float, float, float, float]]:
    """Flatten a streets.StreetGraph into (ax, ay, bx, by, width) tuples."""
    roads = []
    for (a, b, street_class) in graph.edges:
        width = preset.street_width_arterial if street_class == "arterial" else preset.street_width_local
        (ax, ay), (bx, by) = graph.nodes[a], graph.nodes[b]
        roads.append((ax, ay, bx, by, width))
    return roads
