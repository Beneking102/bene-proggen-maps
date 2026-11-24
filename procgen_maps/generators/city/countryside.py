"""Countryside around the city: a country road out to the map edge and a
small village of cottages along it. Pure Python (planning only); the road
is ordinary `StreetSegment`s of class "country" that streets.py builds
without sidewalks, and the village is ordinary `BuildingPlan`s, so every
downstream stage (terrain pads, props keeping off buildings and roads,
signage, the validator) handles them without special cases.
"""
import math
import random
from typing import List, Tuple

from .buildings import FACADE_TYPES, BuildingPlan
from .layout import CityLayout, StreetSegment
from .setback import SETBACK_MARGIN, SIDEWALK_WIDTH

COUNTRY_CLASS = "country"
VILLAGE_BLOCK_ID_BASE = 100000      # village cottages' block ids, clear of every real block id
_ROAD_EDGE_MARGIN = 15.0            # meters the road stops short of the terrain edge
_ROAD_STUB = 24.0                   # meters the road leaves the city straight before it starts bending
_ROAD_BEND_RANGE = 0.3              # radians of heading drift per piece
_ROAD_PIECE_LENGTH = 70.0           # meters per polyline piece
_VILLAGE_RANGE = (1.32, 1.8)        # x city radius: where along the road the cottages stand
_VILLAGE_SPACING = (17.0, 24.0)     # meters between cottages along the road
_COTTAGE_SIZE = ((7.0, 9.5), (6.0, 8.0))   # (width range, depth range) meters


def plan_country_road(layout: CityLayout, preset, rng: random.Random, world_half: float
                      ) -> List[StreetSegment]:
    """A road from the city's eastern edge to the map edge: it starts at
    the outermost x street line's node nearest y = 0, runs straight for
    `_ROAD_STUB` meters, then bends gently piece by piece towards +x
    until `_ROAD_EDGE_MARGIN` from `world_half`. Returns the segments
    (class "country") to append to the layout, or [] when the map is not
    larger than the city."""
    if not layout.streets:
        return []
    points = {}
    for segment in layout.streets:
        for point in (segment.start, segment.end):
            points[point] = True
    max_x = max(p[0] for p in points)
    exit_node = min((p for p in points if p[0] >= max_x - 1.0), key=lambda p: (abs(p[1]), p[1]))
    target_x = world_half - _ROAD_EDGE_MARGIN
    if target_x - exit_node[0] < _ROAD_STUB + 20.0:
        return []
    polyline = [exit_node, (exit_node[0] + _ROAD_STUB, exit_node[1])]
    heading = 0.0
    x, y = polyline[-1]
    next_id = max(s.id for s in layout.streets) + 1
    while x < target_x - 1.0:
        heading = max(-0.6, min(0.6, heading + rng.uniform(-_ROAD_BEND_RANGE, _ROAD_BEND_RANGE)))
        step = min(_ROAD_PIECE_LENGTH, (target_x - x) / max(math.cos(heading), 0.5))
        x = min(target_x, x + step * math.cos(heading))
        y = max(-target_x, min(target_x, y + step * math.sin(heading)))
        polyline.append((round(x * 2.0) / 2.0, round(y * 2.0) / 2.0))
    segments = []
    for a, b in zip(polyline, polyline[1:]):
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
            continue
        segments.append(StreetSegment(next_id, a, b, COUNTRY_CLASS))
        next_id += 1
    return segments


def country_road_points(layout: CityLayout) -> List[Tuple[float, float]]:
    """The country road's polyline (ordered from the city outwards)."""
    segments = [s for s in layout.streets if s.street_class == COUNTRY_CLASS]
    if not segments:
        return []
    points = [segments[0].start]
    for segment in segments:
        points.append(segment.end)
    return points


def plan_village(layout: CityLayout, preset, rng: random.Random) -> List[BuildingPlan]:
    """Cottages along the country road between `_VILLAGE_RANGE` x the
    city radius, alternating sides, each an axis-aligned footprint set
    back far enough that even a diagonal road keeps a sidewalk's width
    plus the setback margin from its wall. Pure Python, unit-tested."""
    points = country_road_points(layout)
    cottage = next(f for f in FACADE_TYPES if f.key == "cottage")
    if len(points) < 2 or not getattr(preset, "village", True):
        return []
    road_half = preset.street_width_local / 2.0
    plans: List[BuildingPlan] = []
    lo, hi = _VILLAGE_RANGE[0] * layout.radius, _VILLAGE_RANGE[1] * layout.radius
    along_total = 0.0
    next_at = rng.uniform(*_VILLAGE_SPACING) * 0.5
    side = 1.0
    placed_rects: List[Tuple[float, float, float, float]] = []
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-6:
            continue
        dx, dy = (bx - ax) / length, (by - ay) / length
        nx, ny = -dy, dx
        while next_at <= along_total + length:
            t = next_at - along_total
            px, py = ax + dx * t, ay + dy * t
            next_at += rng.uniform(*_VILLAGE_SPACING)
            # Not too close to the piece ends: the next piece bends.
            if t < 9.0 or length - t < 9.0 or not (lo <= math.hypot(px, py) <= hi):
                continue
            width = rng.uniform(*_COTTAGE_SIZE[0])
            depth = rng.uniform(*_COTTAGE_SIZE[1])
            half_diag = math.hypot(width / 2.0, depth / 2.0)
            offset = road_half + SIDEWALK_WIDTH + SETBACK_MARGIN + half_diag
            cx, cy = px + nx * side * offset, py + ny * side * offset
            side = -side
            rect = (cx - width / 2.0, cy - depth / 2.0, cx + width / 2.0, cy + depth / 2.0)
            if any(_overlap(rect, other, 2.0) for other in placed_rects):
                continue
            placed_rects.append(rect)
            footprint = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
            floors = 1 if rng.random() < 0.6 else 2
            plans.append(BuildingPlan(VILLAGE_BLOCK_ID_BASE + len(plans), footprint, floors, 3.0, cottage,
                                      rng.uniform(0.0, 1.0)))
        along_total += length
    return plans


def _overlap(a, b, margin=0.0) -> bool:
    return not (a[2] + margin <= b[0] or b[2] + margin <= a[0] or a[3] + margin <= b[1] or b[3] + margin <= a[1])
