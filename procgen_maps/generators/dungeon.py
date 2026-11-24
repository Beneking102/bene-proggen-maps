"""BSP dungeon generation: recursive space-partition rooms + corridors -> mesh.

Split per the project's PP/BPY convention: `generate_dungeon` (BSP split,
room carving, corridor routing) is pure Python and safe to unit test outside
Blender; `build_dungeon_mesh` is the thin bpy build step. Fully independent
of generators/city and generators/terrain - no shared state, presets or
collections.

Each room is a fully enclosed box (floor + walls + ceiling, all materialed
- generators/dungeon.py originally left rooms as open-topped, unmaterialed
shells with corridors dead-ending against solid walls rather than actually
connecting through them). `_find_doorways` locates, for a given room, every
corridor that connects to it - every corridor segment `connect()` creates
starts from a room's own *center* point, so a corridor's exit wall/position
is derived directly from that segment's own direction, no geometric
intersection test needed - and `_wall_segments_with_gaps` builds each of
the room's 4 walls as one or more quads with a gap left open at each
doorway. A torch (a small wall bracket + warm point light, self-contained
in this module rather than routed through assets/factory.py, matching this
generator's existing independence from the city pipeline) is placed at
every doorway. The single largest room is tagged as the "boss" room and
the single smallest as the "start" room (a distinct floor tint each) -
cheap, since both are already known from each room's own width/height.

v0.14 adds, all in the pure half and checked by `validate_dungeon`:

- **levels**: `DungeonParams.levels` BSP layouts stacked `LEVEL_DROP`
  meters apart (level 0 on top). Each pair of neighbouring levels is
  joined by one staircase (`plan_stairs`): the pair of rooms (one per
  level) whose plan-view overlap best fits a stair of `STAIR_LENGTH` x
  `STAIR_WIDTH`; the upper room's floor and the lower room's ceiling get
  a hole over it, a shaft closes the gap. If no pair of rooms overlaps
  enough the lower level is re-rolled from a derived seed.
- **doors**: a lintel over every doorway (the gap used to run to the
  ceiling) and a door leaf standing ajar in it (not in the cave theme).
- **themes** (`crypt`, `cave`, `castle`): the stone colors, the door
  material and the room props (`plan_room_props`: sarcophagi, boulders,
  wall banners) - the layout itself is theme-independent.
- **lighting**: torches on the long walls as well as at the doorways,
  plus one dim fill light per room so the far corners are not black.
"""
import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DUNGEON_ROOT_COLLECTION_NAME = "ProcgenMaps_Dungeon"

ROOM_PREFIX = "ProcgenMaps_DungeonRoom"
CORRIDOR_PREFIX = "ProcgenMaps_DungeonCorridor"
TORCH_PREFIX = "ProcgenMaps_DungeonTorch"

_ROOM_MARGIN_RANGE = (0.5, 1.5)   # meters shrunk from each partition edge so rooms don't touch partition walls
_SPLIT_RATIO_RANGE = (0.4, 0.6)  # keeps both children of a split within a comparable size range
_SQUARE_BIAS_RATIO = 1.25        # aspect ratio above which a partition is split along its longer axis first
_DOORWAY_MARGIN = 0.4            # meters wider than the corridor itself, each side, for a clear doorway gap

_TORCH_HOLDER_SIZE = 0.16
_TORCH_HOLDER_HEIGHT = 0.35
_TORCH_MOUNT_HEIGHT = 1.9        # meters above the floor
_TORCH_INSET = 0.35              # meters into the room from the wall, so it doesn't clip through it
_TORCH_LIGHT_ENERGY = 120.0
_WALL_TORCH_SPACING = 7.0        # meters between extra torches along a wall
_FILL_LIGHT_ENERGY = 18.0        # the per-room ambient fill, watts

THEMES = ("crypt", "cave", "castle")
LEVEL_SLAB = 0.5                 # meters of rock between a level's ceiling and the floor above
DOOR_HEIGHT = 2.2
DOOR_THICKNESS = 0.08
STAIR_WIDTH = 1.8
STAIR_LENGTH = 4.2
STAIR_RISE = 0.25
_STAIR_CLEARANCE = 0.5           # meters of free floor around the stair rect inside both rooms
_STAIR_SEED_ATTEMPTS = 12

_THEME_FILL_COLOR = {
    "crypt": (0.55, 0.65, 0.9),
    "cave": (0.55, 0.75, 0.6),
    "castle": (1.0, 0.8, 0.6),
}


@dataclass(frozen=True)
class DungeonParams:
    """BSP tuning, theme and level count of one dungeon (frozen, hashable)."""
    width: float = 60.0
    height: float = 60.0
    min_room_size: float = 6.0
    max_depth: int = 5
    corridor_width: float = 1.6
    wall_height: float = 3.0
    seed: int = 0
    theme: str = "crypt"
    levels: int = 2

    @property
    def level_drop(self) -> float:
        """Vertical distance between the floors of two neighbouring levels."""
        return self.wall_height + LEVEL_SLAB


@dataclass
class Room:
    """One BSP leaf room: rect, type (start / boss / normal) and level."""
    id: int
    x: float
    y: float
    width: float
    height: float
    level: int = 0

    @property
    def rect(self) -> Tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) of the room."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    @property
    def center(self) -> Tuple[float, float]:
        """The room's centre (x, y)."""
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    @property
    def area(self) -> float:
        """The room's floor area in square meters."""
        return self.width * self.height


@dataclass
class Corridor:
    """An L-shaped corridor between two rooms on one level, as its two straight legs."""
    id: int
    start: Tuple[float, float]
    end: Tuple[float, float]
    width: float
    level: int = 0


@dataclass
class Stairs:
    """A straight flight from the floor of `lower_level` up to the floor
    of `upper_level`, inside the plan-view overlap of the two rooms. The
    rect is the hole in the upper floor / lower ceiling; `direction` is
    the axis the flight climbs along: +x, -x, +y or -y (the top step is
    at the far end in that direction)."""
    id: int
    rect: Tuple[float, float, float, float]
    direction: Tuple[float, float]
    upper_level: int
    lower_level: int
    upper_room_id: int
    lower_room_id: int


@dataclass
class DungeonLayout:
    """The pure-Python plan: rooms, corridors, stairs, features and props per level."""
    rooms: List[Room] = field(default_factory=list)
    corridors: List[Corridor] = field(default_factory=list)
    wall_height: float = 3.0
    stairs: List[Stairs] = field(default_factory=list)
    levels: int = 1
    level_drop: float = 3.5
    theme: str = "crypt"

    def rooms_on(self, level: int) -> List[Room]:
        """The rooms on `level`."""
        return [room for room in self.rooms if room.level == level]

    def corridors_on(self, level: int) -> List[Corridor]:
        """The corridors on `level`."""
        return [corridor for corridor in self.corridors if corridor.level == level]

    def level_z(self, level: int) -> float:
        """The floor height of `level` (0 = top, lower levels are negative)."""
        return -level * self.level_drop

    def stair_rects_in(self, room: Room) -> List[Tuple[float, float, float, float]]:
        """The stair flight rects that lie in `room`."""
        return [s.rect for s in self.stairs if (s.upper_room_id == room.id and s.upper_level == room.level)
                or (s.lower_room_id == room.id and s.lower_level == room.level)]


def generate_dungeon(params: DungeonParams) -> DungeonLayout:
    """`params.levels` stacked BSP levels (see `generate_level`), each
    joined to the next by one staircase. Level 0 uses `params.seed`
    directly; a lower level's seed is derived from it, and re-rolled
    (deterministically) until a staircase fits between the two levels."""
    layout = DungeonLayout(wall_height=params.wall_height, levels=max(1, params.levels),
                           level_drop=params.level_drop, theme=params.theme)
    room_ids = itertools.count()
    corridor_ids = itertools.count()
    rooms, corridors = generate_level(params, params.seed, 0, room_ids, corridor_ids)
    layout.rooms.extend(rooms)
    layout.corridors.extend(corridors)
    for level in range(1, layout.levels):
        upper_rooms = layout.rooms_on(level - 1)
        for attempt in range(_STAIR_SEED_ATTEMPTS):
            seed = (params.seed * 1000003 + level * 7919 + attempt * 104729) % (2 ** 31)
            lower_rooms, lower_corridors = generate_level(params, seed, level, itertools.count(next(room_ids)),
                                                          itertools.count(next(corridor_ids)))
            stairs = plan_stairs(upper_rooms, lower_rooms, level - 1, level, len(layout.stairs))
            if stairs is not None:
                layout.rooms.extend(lower_rooms)
                layout.corridors.extend(lower_corridors)
                layout.stairs.append(stairs)
                # Keep the id counters past what the level used.
                room_ids = itertools.count(max(r.id for r in layout.rooms) + 1)
                corridor_ids = itertools.count(max((c.id for c in layout.corridors), default=-1) + 1)
                break
        else:
            # Twelve re-rolls never failed over the fuzz set (60 x 60 m
            # levels of 10-30 rooms always share a 5 x 3 m patch); if it
            # does, the dungeon simply ends at this level.
            layout.levels = level
            break
    return layout


def generate_level(params: DungeonParams, seed: int, level: int, room_ids=None, corridor_ids=None
                   ) -> Tuple[List[Room], List[Corridor]]:
    """Recursively BSP-split the width x height rectangle (centered on the
    origin), carve one Room per leaf partition, and connect sibling
    partitions' rooms with straight/L-shaped Corridor segments."""
    rng = random.Random(seed)
    rooms: List[Room] = []
    corridors: List[Corridor] = []
    room_ids = itertools.count() if room_ids is None else room_ids
    corridor_ids = itertools.count() if corridor_ids is None else corridor_ids

    def carve_room(x, y, w, h) -> Room:
        margin_x = min(rng.uniform(*_ROOM_MARGIN_RANGE), w * 0.3)
        margin_y = min(rng.uniform(*_ROOM_MARGIN_RANGE), h * 0.3)
        room = Room(next(room_ids), x + margin_x, y + margin_y, w - 2 * margin_x, h - 2 * margin_y, level)
        rooms.append(room)
        return room

    def connect(room_a: Room, room_b: Room):
        ax, ay = room_a.center
        bx, by = room_b.center
        if abs(ax - bx) < 1e-6 or abs(ay - by) < 1e-6:
            corridors.append(Corridor(next(corridor_ids), (ax, ay), (bx, by), params.corridor_width, level))
            return
        elbow = (bx, ay)
        corridors.append(Corridor(next(corridor_ids), (ax, ay), elbow, params.corridor_width, level))
        corridors.append(Corridor(next(corridor_ids), elbow, (bx, by), params.corridor_width, level))

    def split(x, y, w, h, depth) -> Room:
        can_split_x = w >= params.min_room_size * 2
        can_split_y = h >= params.min_room_size * 2
        if depth >= params.max_depth or not (can_split_x or can_split_y):
            return carve_room(x, y, w, h)

        split_along_x = _choose_split_axis(w, h, can_split_x, can_split_y, rng)
        ratio = rng.uniform(*_SPLIT_RATIO_RANGE)

        if split_along_x:
            cut = w * ratio
            first = split(x, y, cut, h, depth + 1)
            second = split(x + cut, y, w - cut, h, depth + 1)
        else:
            cut = h * ratio
            first = split(x, y, w, cut, depth + 1)
            second = split(x, y + cut, w, h - cut, depth + 1)

        connect(first, second)
        return rng.choice((first, second))

    split(-params.width / 2.0, -params.height / 2.0, params.width, params.height, 0)
    return rooms, corridors


def _rect_overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def plan_stairs(upper_rooms: List[Room], lower_rooms: List[Room], upper_level: int, lower_level: int,
                stairs_id: int = 0) -> Optional[Stairs]:
    """The staircase between two levels: among every (upper room, lower
    room) pair whose plan-view overlap fits a STAIR_LENGTH x STAIR_WIDTH
    flight plus _STAIR_CLEARANCE all round (in either orientation), pick
    the pair with the largest overlap and centre the flight in it. The
    flight climbs along the overlap's longer axis, towards +x / +y.
    Returns None when no pair fits. Pure Python, unit-tested."""
    need_long = STAIR_LENGTH + 2 * _STAIR_CLEARANCE
    need_short = STAIR_WIDTH + 2 * _STAIR_CLEARANCE
    best = None
    for upper in upper_rooms:
        for lower in lower_rooms:
            overlap = _rect_overlap(upper.rect, lower.rect)
            if overlap is None:
                continue
            w, h = overlap[2] - overlap[0], overlap[3] - overlap[1]
            along_x = w >= need_long and h >= need_short
            along_y = h >= need_long and w >= need_short
            if not (along_x or along_y):
                continue
            score = (w * h, upper.id, lower.id)
            if best is None or score > best[0]:
                best = (score, upper, lower, overlap, along_x if w >= h else not along_y)
    if best is None:
        return None
    _score, upper, lower, overlap, along_x = best
    cx, cy = (overlap[0] + overlap[2]) / 2.0, (overlap[1] + overlap[3]) / 2.0
    if along_x:
        rect = (cx - STAIR_LENGTH / 2, cy - STAIR_WIDTH / 2, cx + STAIR_LENGTH / 2, cy + STAIR_WIDTH / 2)
        direction = (1.0, 0.0)
    else:
        rect = (cx - STAIR_WIDTH / 2, cy - STAIR_LENGTH / 2, cx + STAIR_WIDTH / 2, cy + STAIR_LENGTH / 2)
        direction = (0.0, 1.0)
    return Stairs(stairs_id, rect, direction, upper_level, lower_level, upper.id, lower.id)


def room_graph_components(rooms: List[Room], corridors: List[Corridor]) -> int:
    """Number of connected components of the rooms-joined-by-corridors
    graph (a corridor joins the rooms whose centers are its endpoints or,
    for an L-shaped pair, via the shared elbow). Pure Python."""
    parent = {}

    def find(key):
        while parent.setdefault(key, key) != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        parent[find(a)] = find(b)

    def key_of(point):
        return (round(point[0], 4), round(point[1], 4))

    for room in rooms:
        find(("room", room.id))
        union(("room", room.id), key_of(room.center))
    for corridor in corridors:
        union(key_of(corridor.start), key_of(corridor.end))
    return len({find(("room", room.id)) for room in rooms})


def validate_dungeon(layout: DungeonLayout) -> List[str]:
    """Structural checks over a layout, as a list of problems (empty =
    valid): rooms must not overlap on a level, every level's rooms must
    be one connected network, every pair of neighbouring levels needs a
    staircase whose rect lies inside both of its rooms (with the
    clearance) and clear of the rooms' pillars/dais, and no door-less
    (unconnected) room may exist. Run by the fuzz tests."""
    problems = []
    for level in range(layout.levels):
        rooms = layout.rooms_on(level)
        corridors = layout.corridors_on(level)
        if not rooms:
            problems.append(f"level {level} has no rooms")
            continue
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                if _rect_overlap(a.rect, b.rect) is not None:
                    problems.append(f"level {level}: rooms {a.id} and {b.id} overlap")
        components = room_graph_components(rooms, corridors)
        if components != 1:
            problems.append(f"level {level}: {components} disconnected room groups")
        if len(rooms) > 1:
            for room in rooms:
                if not _find_doorways(room, corridors):
                    problems.append(f"level {level}: room {room.id} has no doorway")
    for level in range(1, layout.levels):
        joining = [s for s in layout.stairs if s.upper_level == level - 1 and s.lower_level == level]
        if not joining:
            problems.append(f"no stairs between levels {level - 1} and {level}")
    by_id = {(room.level, room.id): room for room in layout.rooms}
    for stairs in layout.stairs:
        for level, room_id in ((stairs.upper_level, stairs.upper_room_id), (stairs.lower_level, stairs.lower_room_id)):
            room = by_id.get((level, room_id))
            if room is None:
                problems.append(f"stairs {stairs.id}: room {room_id} on level {level} missing")
                continue
            r, c = stairs.rect, _STAIR_CLEARANCE - 1e-6
            if not (room.x + c <= r[0] and r[2] <= room.x + room.width - c
                    and room.y + c <= r[1] and r[3] <= room.y + room.height - c):
                problems.append(f"stairs {stairs.id}: rect not inside room {room_id} on level {level}")
            room_type = "boss" if room.id == max(layout.rooms, key=lambda q: q.area).id else "normal"
            for kind, x, y, size in plan_room_features(room, room_type, layout.stair_rects_in(room)):
                half = size / 2.0
                if _rect_overlap((x - half, y - half, x + half, y + half), stairs.rect) is not None:
                    problems.append(f"stairs {stairs.id}: {kind} in room {room_id} stands on the stairs")
    return problems


def _choose_split_axis(w, h, can_split_x, can_split_y, rng) -> bool:
    """True splits along x (side-by-side children); biased toward cutting the
    longer axis first so both children stay roughly square."""
    if can_split_x and not can_split_y:
        return True
    if can_split_y and not can_split_x:
        return False
    if w / h > _SQUARE_BIAS_RATIO:
        return True
    if h / w > _SQUARE_BIAS_RATIO:
        return False
    return rng.random() < 0.5


def _find_doorways(room: Room, corridors: List[Corridor]) -> List[Tuple[str, float, float]]:
    """Every corridor `connect()` creates starts or ends exactly at some
    room's `.center` (see module docstring) - so for each corridor touching
    this room, the wall it exits through and the position along that wall
    are read directly off the *other* endpoint's direction from the room's
    own center, no geometric intersection test needed. Returns a list of
    (wall, position, half_width) tuples; `wall` is one of
    north/south/east/west, `position` is the x (north/south) or y
    (east/west) coordinate of the doorway's center, and `half_width` is
    how wide a gap to leave in that wall."""
    cx, cy = room.center
    doorways = []
    for corridor in corridors:
        sx, sy = corridor.start
        ex, ey = corridor.end
        at_start = abs(sx - cx) < 1e-4 and abs(sy - cy) < 1e-4
        at_end = abs(ex - cx) < 1e-4 and abs(ey - cy) < 1e-4
        if not at_start and not at_end:
            continue
        if at_start:
            dx, dy = ex - sx, ey - sy
        else:
            dx, dy = sx - ex, sy - ey
        half_width = corridor.width / 2.0 + _DOORWAY_MARGIN
        if abs(dy) < abs(dx):
            doorways.append(("east" if dx > 0 else "west", cy, half_width))
        else:
            doorways.append(("north" if dy > 0 else "south", cx, half_width))
    return doorways


def _wall_segments_with_gaps(wall_min: float, wall_max: float,
                              gaps: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Given a wall spanning [wall_min, wall_max] and a list of (position,
    half_width) doorway gaps along it, return the solid wall-segment
    intervals with each gap (clipped to the wall's own span, then merged if
    overlapping) removed."""
    if not gaps:
        return [(wall_min, wall_max)]

    intervals = []
    for position, half_width in gaps:
        lo = max(wall_min, position - half_width)
        hi = min(wall_max, position + half_width)
        if hi > lo:
            intervals.append((lo, hi))
    intervals.sort()

    merged = []
    for lo, hi in intervals:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))

    segments = []
    cursor = wall_min
    for lo, hi in merged:
        if lo - cursor > 1e-3:
            segments.append((cursor, lo))
        cursor = max(cursor, hi)
    if wall_max - cursor > 1e-3:
        segments.append((cursor, wall_max))
    return segments


_PILLAR_MIN_ROOM_SIDE = 7.0      # meters; rooms narrower than this on either axis get no pillars
_PILLAR_INSET = 1.8              # meters from the walls to the outer pillar line
_PILLAR_SPACING = 3.6            # meters between pillars (rounded to fit the room)
_PILLAR_SIZE = 0.6               # meters, square column side
_DAIS_SIZE_FRACTION = 0.3        # boss-room dais side as a fraction of the room's shorter side
_DAIS_HEIGHT = 0.35


def plan_room_features(room: Room, room_type: str, exclude_rects=()) -> List[Tuple[str, float, float, float]]:
    """(kind, x, y, size) decorations for a room, derived purely from its
    own size/type so the result is deterministic and unit-testable:

    - "pillar": a grid of square columns in any room at least
      _PILLAR_MIN_ROOM_SIDE on both sides (the inner ring stays
      _PILLAR_INSET off the walls, so doorways are never blocked and the
      room reads as a hall rather than an empty box);
    - "dais": one raised square platform in the middle of the boss room.

    Pillars are skipped where the dais is, so the boss hall keeps its
    center clear, and both keep off `exclude_rects` (the staircases)."""
    features: List[Tuple[str, float, float, float]] = []
    cx, cy = room.center
    dais_size = 0.0

    def blocked(x, y, half):
        return any(_rect_overlap((x - half - 0.3, y - half - 0.3, x + half + 0.3, y + half + 0.3), rect) is not None
                   for rect in exclude_rects)

    if room_type == "boss":
        dais_size = max(2.0, min(room.width, room.height) * _DAIS_SIZE_FRACTION)
        if not blocked(cx, cy, dais_size / 2.0):
            features.append(("dais", cx, cy, dais_size))
        else:
            dais_size = 0.0

    if room.width >= _PILLAR_MIN_ROOM_SIDE and room.height >= _PILLAR_MIN_ROOM_SIDE:
        span_x = room.width - 2 * _PILLAR_INSET
        span_y = room.height - 2 * _PILLAR_INSET
        count_x = max(2, round(span_x / _PILLAR_SPACING) + 1)
        count_y = max(2, round(span_y / _PILLAR_SPACING) + 1)
        for i in range(count_x):
            for j in range(count_y):
                x = room.x + _PILLAR_INSET + span_x * i / (count_x - 1)
                y = room.y + _PILLAR_INSET + span_y * j / (count_y - 1)
                if dais_size and abs(x - cx) < dais_size / 2 + _PILLAR_SIZE and abs(y - cy) < dais_size / 2 + _PILLAR_SIZE:
                    continue
                if blocked(x, y, _PILLAR_SIZE / 2.0):
                    continue
                features.append(("pillar", x, y, _PILLAR_SIZE))
    return features


_PROP_KIND_BY_THEME = {"crypt": "sarcophagus", "cave": "boulder", "castle": "banner"}
_PROP_SIZE = {"sarcophagus": (2.2, 0.9, 0.8), "boulder": (1.2, 1.0, 0.9), "banner": (0.7, 0.04, 1.5)}


def plan_room_props(room: Room, theme: str, rng: random.Random, features=(), exclude_rects=(),
                    doorways=()) -> List[Tuple[str, float, float, float, float, float, float]]:
    """(kind, x, y, z, size_x, size_y, size_z) theme props for a room:
    crypt sarcophagi in rows along the room's long axis, cave boulders
    scattered near the walls, castle banners hung on the walls - all
    clear of the pillars, the dais, the staircases and the doorways.
    Deterministic for a given rng. Pure Python, unit-tested."""
    kind = _PROP_KIND_BY_THEME.get(theme)
    if kind is None or room.width < 5.0 or room.height < 5.0:
        return []
    sx, sy, sz = _PROP_SIZE[kind]
    blocked_rects = list(exclude_rects)
    for _f_kind, fx, fy, size in features:
        half = size / 2.0 + 0.4
        blocked_rects.append((fx - half, fy - half, fx + half, fy + half))
    for wall, position, half_width in doorways:
        reach = half_width + 1.2
        if wall in ("north", "south"):
            y = room.y + room.height if wall == "north" else room.y
            blocked_rects.append((position - reach, y - 1.5, position + reach, y + 1.5))
        else:
            x = room.x + room.width if wall == "east" else room.x
            blocked_rects.append((x - 1.5, position - reach, x + 1.5, position + reach))
    props = []

    def free(rect):
        return all(_rect_overlap(rect, other) is None for other in blocked_rects)

    def place(x, y, w, d, kind_z=0.0):
        rect = (x - w / 2, y - d / 2, x + w / 2, y + d / 2)
        margin = 0.0 if kind == "banner" else 0.3   # banners hang on the wall itself
        if rect[0] < room.x + margin or rect[2] > room.x + room.width - margin \
                or rect[1] < room.y + margin or rect[3] > room.y + room.height - margin:
            return False
        if not free(rect):
            return False
        blocked_rects.append((rect[0] - 0.4, rect[1] - 0.4, rect[2] + 0.4, rect[3] + 0.4))
        props.append((kind, x, y, kind_z, w, d, sz))
        return True

    if kind == "sarcophagus":
        along_x = room.width >= room.height
        count = int((room.width if along_x else room.height) / 4.0)
        for i in range(count):
            t = (i + 0.5) / count
            if along_x:
                x = room.x + room.width * t
                for y in (room.y + 1.4, room.y + room.height - 1.4):
                    place(x, y, sx, sy)
            else:
                y = room.y + room.height * t
                for x in (room.x + 1.4, room.x + room.width - 1.4):
                    place(x, y, sy, sx)
    elif kind == "boulder":
        count = max(1, int(room.area / 30.0))
        for _ in range(count * 3):
            if len(props) >= count:
                break
            scale = rng.uniform(0.6, 1.4)
            x = room.x + rng.uniform(0.9, room.width - 0.9)
            y = room.y + rng.uniform(0.9, room.height - 0.9)
            place(x, y, sx * scale, sy * scale)
    elif kind == "banner":
        for wall in ("north", "south"):
            y = room.y + room.height - 0.06 if wall == "north" else room.y + 0.06
            count = max(1, int(room.width / 5.0))
            for i in range(count):
                x = room.x + room.width * (i + 0.5) / count
                place(x, y, sx, sy, kind_z=0.9)
    return props


def _torch_position_for_doorway(room: Room, wall: str, position: float, half_width: float
                                 ) -> Tuple[float, float, float]:
    """Mount a torch just to one side of a doorway (not inside the gap
    itself) on the room's own wall, clamped to stay within the room even
    for small rooms."""
    x0, y0 = room.x, room.y
    x1, y1 = room.x + room.width, room.y + room.height
    offset = half_width + 0.5

    if wall in ("west", "east"):
        along = position + offset
        if along > y1 - 0.3:
            along = position - offset
        along = min(max(along, y0 + 0.2), y1 - 0.2)
        x = x0 + _TORCH_INSET if wall == "west" else x1 - _TORCH_INSET
        return (x, along, _TORCH_MOUNT_HEIGHT)

    along = position + offset
    if along > x1 - 0.3:
        along = position - offset
    along = min(max(along, x0 + 0.2), x1 - 0.2)
    y = y0 + _TORCH_INSET if wall == "south" else y1 - _TORCH_INSET
    return (along, y, _TORCH_MOUNT_HEIGHT)


def wall_torch_positions(room: Room, doorways, spacing: float = _WALL_TORCH_SPACING) -> List[Tuple[float, float, float]]:
    """Extra torches along the two longer walls every `spacing` meters,
    skipping the stretch around every doorway (those have their own
    torch). Pure Python, unit-tested."""
    positions = []
    along_x = room.width >= room.height
    length = room.width if along_x else room.height
    count = int(length / spacing)
    if count < 1:
        return positions
    for i in range(count):
        t = room.x + room.width * (i + 0.5) / count if along_x else room.y + room.height * (i + 0.5) / count
        for side in (0, 1):
            if along_x:
                wall = "north" if side else "south"
                near_door = any(w == wall and abs(pos - t) < hw + 1.0 for (w, pos, hw) in doorways)
                if near_door:
                    continue
                y = room.y + room.height - _TORCH_INSET if side else room.y + _TORCH_INSET
                positions.append((t, y, _TORCH_MOUNT_HEIGHT))
            else:
                wall = "east" if side else "west"
                near_door = any(w == wall and abs(pos - t) < hw + 1.0 for (w, pos, hw) in doorways)
                if near_door:
                    continue
                x = room.x + room.width - _TORCH_INSET if side else room.x + _TORCH_INSET
                positions.append((x, t, _TORCH_MOUNT_HEIGHT))
    return positions


def build_dungeon_mesh(layout: DungeonLayout, collection) -> list:
    """Build one enclosed room object (floor+ceiling+walls-with-doorways)
    per Room, one flat floor-strip object per Corridor, and a torch (holder
    + flame + point light) at every doorway, linked into a
    'ProcgenMaps_Dungeon' collection under `collection`. Returns the list
    of all created objects."""
    dungeon_collection = _get_or_create_collection(DUNGEON_ROOT_COLLECTION_NAME, collection)
    theme = layout.theme if layout.theme in THEMES else "crypt"

    room_type_by_id: Dict[int, str] = {}
    if layout.rooms:
        # The boss hall is the largest room of the lowest level, the start
        # room the smallest of the top one: the dungeon is descended.
        boss_room = max(layout.rooms_on(layout.levels - 1) or layout.rooms, key=lambda r: r.area)
        start_room = min(layout.rooms_on(0) or layout.rooms, key=lambda r: r.area)
        if start_room.id != boss_room.id:
            room_type_by_id[start_room.id] = "start"
        room_type_by_id[boss_room.id] = "boss"

    created = []
    for room in layout.rooms:
        z0 = layout.level_z(room.level)
        doorways = _find_doorways(room, layout.corridors_on(room.level))
        room_type = room_type_by_id.get(room.id, "normal")
        floor_holes = [s.rect for s in layout.stairs if s.upper_room_id == room.id and s.upper_level == room.level]
        ceiling_holes = [s.rect for s in layout.stairs if s.lower_room_id == room.id and s.lower_level == room.level]
        obj = _build_room_mesh(room, layout.wall_height, doorways, room_type, theme, z0, floor_holes, ceiling_holes)
        obj["procgen_maps_room_id"] = room.id
        obj["procgen_maps_room_type"] = room_type
        obj["procgen_maps_level"] = room.level
        dungeon_collection.objects.link(obj)
        created.append(obj)

        torch_spots = [_torch_position_for_doorway(room, wall, position, half_width)
                       for (wall, position, half_width) in doorways]
        torch_spots += wall_torch_positions(room, doorways)
        for index, (tx, ty, tz) in enumerate(torch_spots):
            for torch_obj in _build_torch(f"{TORCH_PREFIX}_{room.id}_{index}", (tx, ty, tz + z0), theme):
                dungeon_collection.objects.link(torch_obj)
                created.append(torch_obj)
        fill = _build_fill_light(f"{TORCH_PREFIX}_{room.id}_Fill", room, z0 + layout.wall_height - 0.3, theme)
        dungeon_collection.objects.link(fill)
        created.append(fill)

        if theme != "cave":
            door_obj = _build_doors(f"{ROOM_PREFIX}_{room.id}_Doors", room, doorways, z0, theme)
            if door_obj is not None:
                dungeon_collection.objects.link(door_obj)
                created.append(door_obj)

        stair_rects = layout.stair_rects_in(room)
        features = plan_room_features(room, room_type, stair_rects)
        if features:
            feature_obj = _build_room_features(f"{ROOM_PREFIX}_{room.id}_Features", features, layout.wall_height,
                                               theme, z0)
            dungeon_collection.objects.link(feature_obj)
            created.append(feature_obj)
        room_rng = random.Random(room.id * 7919 + room.level * 31 + len(layout.rooms))
        props = plan_room_props(room, theme, room_rng, features, stair_rects, doorways)
        if props:
            prop_obj = _build_room_props(f"{ROOM_PREFIX}_{room.id}_Props", props, theme, z0)
            dungeon_collection.objects.link(prop_obj)
            created.append(prop_obj)

    for corridor in layout.corridors:
        obj = _build_corridor_mesh(corridor, theme, layout.level_z(corridor.level))
        dungeon_collection.objects.link(obj)
        created.append(obj)

    for stairs in layout.stairs:
        obj = _build_stairs_mesh(stairs, layout, theme)
        dungeon_collection.objects.link(obj)
        created.append(obj)

    return created


def _quads_minus_holes(x0, y0, x1, y1, holes):
    """Axis-aligned rectangle (x0,y0)-(x1,y1) minus the hole rects, as a
    list of rectangles: every hole cuts the strip it lies in into the
    pieces left and right of it plus the bands above and below. Holes
    are assumed not to overlap each other (one staircase per room pair)."""
    pieces = [(x0, y0, x1, y1)]
    for hole in holes:
        next_pieces = []
        for (px0, py0, px1, py1) in pieces:
            cut = _rect_overlap((px0, py0, px1, py1), hole)
            if cut is None:
                next_pieces.append((px0, py0, px1, py1))
                continue
            hx0, hy0, hx1, hy1 = cut
            if hy0 > py0:
                next_pieces.append((px0, py0, px1, hy0))
            if hy1 < py1:
                next_pieces.append((px0, hy1, px1, py1))
            if hx0 > px0:
                next_pieces.append((px0, hy0, hx0, hy1))
            if hx1 < px1:
                next_pieces.append((hx1, hy0, px1, hy1))
        pieces = next_pieces
    return [p for p in pieces if p[2] - p[0] > 1e-4 and p[3] - p[1] > 1e-4]


def _build_room_mesh(room: Room, wall_height: float,
                      doorways: List[Tuple[str, float, float]], room_type: str, theme: str = "crypt",
                      z0: float = 0.0, floor_holes=(), ceiling_holes=()):
    import bpy

    from ..materials import dungeon_mat

    x0, y0 = room.x, room.y
    x1, y1 = room.x + room.width, room.y + room.height
    z1 = z0 + wall_height

    gaps_by_wall: Dict[str, List[Tuple[float, float]]] = {"north": [], "south": [], "east": [], "west": []}
    for wall, position, half_width in doorways:
        gaps_by_wall[wall].append((position, half_width))

    south_segments = _wall_segments_with_gaps(x0, x1, gaps_by_wall["south"])
    north_segments = _wall_segments_with_gaps(x0, x1, gaps_by_wall["north"])
    west_segments = _wall_segments_with_gaps(y0, y1, gaps_by_wall["west"])
    east_segments = _wall_segments_with_gaps(y0, y1, gaps_by_wall["east"])

    verts: list = []
    wall_faces: list = []
    floor_faces: list = []
    ceiling_faces: list = []

    def add_quad(p0, p1, p2, p3, face_list):
        idx = len(verts)
        verts.extend([p0, p1, p2, p3])
        face_list.append((idx, idx + 1, idx + 2, idx + 3))

    for (fx0, fy0, fx1, fy1) in _quads_minus_holes(x0, y0, x1, y1, floor_holes):
        add_quad((fx0, fy0, z0), (fx1, fy0, z0), (fx1, fy1, z0), (fx0, fy1, z0), floor_faces)
    for (fx0, fy0, fx1, fy1) in _quads_minus_holes(x0, y0, x1, y1, ceiling_holes):
        add_quad((fx0, fy1, z1), (fx1, fy1, z1), (fx1, fy0, z1), (fx0, fy0, z1), ceiling_faces)
    # The shaft around a ceiling hole up to the floor above (LEVEL_SLAB).
    for (hx0, hy0, hx1, hy1) in ceiling_holes:
        top = z1 + LEVEL_SLAB
        add_quad((hx0, hy0, z1), (hx1, hy0, z1), (hx1, hy0, top), (hx0, hy0, top), wall_faces)
        add_quad((hx1, hy1, z1), (hx0, hy1, z1), (hx0, hy1, top), (hx1, hy1, top), wall_faces)
        add_quad((hx0, hy1, z1), (hx0, hy0, z1), (hx0, hy0, top), (hx0, hy1, top), wall_faces)
        add_quad((hx1, hy0, z1), (hx1, hy1, z1), (hx1, hy1, top), (hx1, hy0, top), wall_faces)

    door_top = z0 + DOOR_HEIGHT
    for a, b in south_segments:
        add_quad((a, y0, z0), (b, y0, z0), (b, y0, z1), (a, y0, z1), wall_faces)
    for position, half_width in gaps_by_wall["south"]:
        add_quad((position - half_width, y0, door_top), (position + half_width, y0, door_top),
                 (position + half_width, y0, z1), (position - half_width, y0, z1), wall_faces)
    for a, b in north_segments:
        add_quad((b, y1, z0), (a, y1, z0), (a, y1, z1), (b, y1, z1), wall_faces)
    for position, half_width in gaps_by_wall["north"]:
        add_quad((position + half_width, y1, door_top), (position - half_width, y1, door_top),
                 (position - half_width, y1, z1), (position + half_width, y1, z1), wall_faces)
    for a, b in west_segments:
        add_quad((x0, b, z0), (x0, a, z0), (x0, a, z1), (x0, b, z1), wall_faces)
    for position, half_width in gaps_by_wall["west"]:
        add_quad((x0, position + half_width, door_top), (x0, position - half_width, door_top),
                 (x0, position - half_width, z1), (x0, position + half_width, z1), wall_faces)
    for a, b in east_segments:
        add_quad((x1, a, z0), (x1, b, z0), (x1, b, z1), (x1, a, z1), wall_faces)
    for position, half_width in gaps_by_wall["east"]:
        add_quad((x1, position - half_width, door_top), (x1, position + half_width, door_top),
                 (x1, position + half_width, z1), (x1, position - half_width, z1), wall_faces)

    faces = wall_faces + floor_faces + ceiling_faces

    name = f"{ROOM_PREFIX}_{room.id}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)

    floor_key = {"start": "floor_start", "boss": "floor_boss"}.get(room_type, "floor")
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("wall", theme))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material(floor_key, theme))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("ceiling", theme))

    wall_count = len(wall_faces)
    floor_count = len(floor_faces)
    for index, poly in enumerate(mesh.polygons):
        if index < wall_count:
            poly.material_index = 0
        elif index < wall_count + floor_count:
            poly.material_index = 1
        else:
            poly.material_index = 2

    return bpy.data.objects.new(name, mesh)


def _build_room_features(name: str, features, wall_height: float, theme: str = "crypt", z0: float = 0.0):
    """One mesh per room holding all its pillars (floor to ceiling, wall
    material) and dais (boss floor tint) as simple boxes."""
    import bpy

    from ..materials import dungeon_mat

    verts: list = []
    faces: list = []
    material_index: list = []

    def add_box(x0, y0, z0, x1, y1, z1, slot):
        base = len(verts)
        verts.extend([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                      (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)])
        for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
            faces.append(tuple(base + i for i in face))
            material_index.append(slot)

    for kind, x, y, size in features:
        half = size / 2.0
        if kind == "pillar":
            add_box(x - half, y - half, z0, x + half, y + half, z0 + wall_height, 0)
        elif kind == "dais":
            add_box(x - half, y - half, z0, x + half, y + half, z0 + _DAIS_HEIGHT, 1)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("wall", theme))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("floor_boss", theme))
    mesh.polygons.foreach_set("material_index", material_index)
    return bpy.data.objects.new(name, mesh)


def _box_verts_faces(verts, faces, material_index, x0, y0, z0, x1, y1, z1, slot):
    base = len(verts)
    verts.extend([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                  (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)])
    for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        faces.append(tuple(base + i for i in face))
        material_index.append(slot)


def _build_room_props(name: str, props, theme: str, z0: float):
    """Theme props as boxes in one mesh per room: sarcophagi (lid slab on
    a box), boulders (a box rotated in plan, cheaper than a real rock),
    banners (thin cloth slabs on the wall)."""
    import bpy

    from ..materials import dungeon_mat

    verts: list = []
    faces: list = []
    material_index: list = []
    for kind, x, y, z, w, d, h in props:
        if kind == "sarcophagus":
            _box_verts_faces(verts, faces, material_index, x - w / 2, y - d / 2, z0 + z,
                             x + w / 2, y + d / 2, z0 + z + h * 0.8, 0)
            _box_verts_faces(verts, faces, material_index, x - w / 2 - 0.06, y - d / 2 - 0.06, z0 + z + h * 0.8,
                             x + w / 2 + 0.06, y + d / 2 + 0.06, z0 + z + h, 0)
        elif kind == "boulder":
            # Two overlapping boxes, the upper one smaller and shifted, so
            # the silhouette is not a plain crate.
            _box_verts_faces(verts, faces, material_index, x - w / 2, y - d / 2, z0 + z,
                             x + w / 2, y + d / 2, z0 + z + h * 0.6, 0)
            _box_verts_faces(verts, faces, material_index, x - w * 0.3, y - d * 0.35, z0 + z + h * 0.4,
                             x + w * 0.35, y + d * 0.3, z0 + z + h, 0)
        elif kind == "banner":
            _box_verts_faces(verts, faces, material_index, x - w / 2, y - d / 2, z0 + z,
                             x + w / 2, y + d / 2, z0 + z + h, 1)
            _box_verts_faces(verts, faces, material_index, x - w / 2 - 0.1, y - d / 2 - 0.02, z0 + z + h,
                             x + w / 2 + 0.1, y + d / 2 + 0.02, z0 + z + h + 0.06, 2)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("prop", theme))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("banner", theme))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("door", theme))
    mesh.polygons.foreach_set("material_index", material_index)
    return bpy.data.objects.new(name, mesh)


def _build_doors(name: str, room: Room, doorways, z0: float, theme: str):
    """A door leaf standing ajar in every doorway (hinged on one jamb,
    swung ~70 degrees into the room) plus two jamb posts, in the theme's
    door material. One mesh per room."""
    import bpy

    from ..materials import dungeon_mat

    verts: list = []
    faces: list = []
    material_index: list = []
    x0, y0 = room.x, room.y
    x1, y1 = room.x + room.width, room.y + room.height
    angle = math.radians(70.0)
    for wall, position, half_width in doorways:
        leaf = 2 * half_width - 0.3
        post = 0.12
        if wall in ("south", "north"):
            y = y0 if wall == "south" else y1
            inward = 1.0 if wall == "south" else -1.0
            for jx in (position - half_width, position + half_width - post):
                _box_verts_faces(verts, faces, material_index, jx, y - post / 2, z0, jx + post, y + post / 2,
                                 z0 + DOOR_HEIGHT, 1)
            hx = position - half_width + post
            # The leaf as a box in the rotated frame: approximate with a box
            # along the swung direction (thin, so the rotation reads).
            ex, ey = math.cos(angle), math.sin(angle) * inward
            base = len(verts)
            corners = []
            for (s, t) in ((0.0, -DOOR_THICKNESS / 2), (leaf, -DOOR_THICKNESS / 2), (leaf, DOOR_THICKNESS / 2), (0.0, DOOR_THICKNESS / 2)):
                corners.append((hx + ex * s - ey * t, y + ey * s + ex * t))
            for zz in (z0, z0 + DOOR_HEIGHT - 0.05):
                for (cx, cy) in corners:
                    verts.append((cx, cy, zz))
            for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
                faces.append(tuple(base + i for i in face))
                material_index.append(0)
        else:
            x = x0 if wall == "west" else x1
            inward = 1.0 if wall == "west" else -1.0
            for jy in (position - half_width, position + half_width - post):
                _box_verts_faces(verts, faces, material_index, x - post / 2, jy, z0, x + post / 2, jy + post,
                                 z0 + DOOR_HEIGHT, 1)
            hy = position - half_width + post
            ex, ey = math.sin(angle) * inward, math.cos(angle)
            base = len(verts)
            corners = []
            for (s, t) in ((0.0, -DOOR_THICKNESS / 2), (leaf, -DOOR_THICKNESS / 2), (leaf, DOOR_THICKNESS / 2), (0.0, DOOR_THICKNESS / 2)):
                corners.append((x + ex * s + ey * t, hy + ey * s - ex * t))
            for zz in (z0, z0 + DOOR_HEIGHT - 0.05):
                for (cx, cy) in corners:
                    verts.append((cx, cy, zz))
            for face in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
                faces.append(tuple(base + i for i in face))
                material_index.append(0)
    if not faces:
        return None
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("door", theme))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("wall", theme))
    mesh.polygons.foreach_set("material_index", material_index)
    return bpy.data.objects.new(name, mesh)


def _build_stairs_mesh(stairs: Stairs, layout: DungeonLayout, theme: str):
    """The flight: STAIR_RISE-high step boxes from the lower floor up to
    the upper floor, plus a solid stringer block under them and two low
    parapets along the sides, in the theme's wall material."""
    import bpy

    from ..materials import dungeon_mat

    verts: list = []
    faces: list = []
    material_index: list = []
    rx0, ry0, rx1, ry1 = stairs.rect
    z_low = layout.level_z(stairs.lower_level)
    z_high = layout.level_z(stairs.upper_level)
    drop = z_high - z_low
    steps = max(2, int(round(drop / STAIR_RISE)))
    rise = drop / steps
    dx, dy = stairs.direction
    length = (rx1 - rx0) if dx else (ry1 - ry0)
    run = length / steps
    for i in range(steps):
        top = z_low + rise * (i + 1)
        if dx:
            sx0 = rx0 + run * i
            _box_verts_faces(verts, faces, material_index, sx0, ry0, z_low - 0.05, sx0 + run, ry1, top, 0)
        else:
            sy0 = ry0 + run * i
            _box_verts_faces(verts, faces, material_index, rx0, sy0, z_low - 0.05, rx1, sy0 + run, top, 0)
    # Parapets along both sides of the hole in the upper floor so nobody
    # walks into the shaft from the side.
    if dx:
        for (py0, py1) in ((ry0 - 0.15, ry0), (ry1, ry1 + 0.15)):
            _box_verts_faces(verts, faces, material_index, rx0 - 0.15, py0, z_high, rx1, py1, z_high + 0.9, 0)
        _box_verts_faces(verts, faces, material_index, rx0 - 0.15, ry0 - 0.15, z_high, rx0, ry1 + 0.15, z_high + 0.9, 0)
    else:
        for (px0, px1) in ((rx0 - 0.15, rx0), (rx1, rx1 + 0.15)):
            _box_verts_faces(verts, faces, material_index, px0, ry0 - 0.15, z_high, px1, ry1, z_high + 0.9, 0)
        _box_verts_faces(verts, faces, material_index, rx0 - 0.15, ry0 - 0.15, z_high, rx1 + 0.15, ry0, z_high + 0.9, 0)
    name = f"{ROOM_PREFIX}_Stairs_{stairs.id}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("wall", theme))
    mesh.polygons.foreach_set("material_index", material_index)
    obj = bpy.data.objects.new(name, mesh)
    obj["procgen_maps_stairs"] = stairs.id
    return obj


def _build_fill_light(name: str, room: Room, z: float, theme: str):
    """One dim, soft point light under the ceiling of every room: the
    subtle ambient that keeps corners the torches do not reach from going
    pitch black."""
    import bpy

    light_data = bpy.data.lights.new(name, type='POINT')
    light_data.energy = _FILL_LIGHT_ENERGY * max(1.0, room.area / 60.0)
    light_data.color = _THEME_FILL_COLOR.get(theme, (0.7, 0.7, 0.8))
    light_data.shadow_soft_size = 2.5
    obj = bpy.data.objects.new(name, light_data)
    cx, cy = room.center
    obj.location = (cx, cy, z)
    return obj


def _build_torch(name: str, position: Tuple[float, float, float], theme: str = "crypt"):
    """A small wall-mounted torch: a stone holder box, a warm emissive
    "flame" box above it, and an actual POINT light co-located with the
    flame so the doorway is genuinely lit, not just decorated."""
    import bpy

    from ..materials import dungeon_mat

    x, y, z = position
    half = _TORCH_HOLDER_SIZE / 2.0
    box_faces = [
        (0, 1, 2, 3), (4, 5, 6, 7),
        (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ]

    holder_verts = [
        (x - half, y - half, z - half), (x + half, y - half, z - half),
        (x + half, y + half, z - half), (x - half, y + half, z - half),
        (x - half, y - half, z + half), (x + half, y - half, z + half),
        (x + half, y + half, z + half), (x - half, y + half, z + half),
    ]
    holder_mesh = bpy.data.meshes.new(name)
    holder_mesh.from_pydata(holder_verts, [], box_faces)
    holder_mesh.update(calc_edges=True)
    holder_mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("torch_holder", theme))
    holder_obj = bpy.data.objects.new(name, holder_mesh)

    flame_name = f"{name}_Flame"
    fh = (_TORCH_HOLDER_SIZE * 0.6) / 2.0
    flame_z = z + half + fh
    flame_verts = [
        (x - fh, y - fh, flame_z - fh), (x + fh, y - fh, flame_z - fh),
        (x + fh, y + fh, flame_z - fh), (x - fh, y + fh, flame_z - fh),
        (x - fh, y - fh, flame_z + fh), (x + fh, y - fh, flame_z + fh),
        (x + fh, y + fh, flame_z + fh), (x - fh, y + fh, flame_z + fh),
    ]
    flame_mesh = bpy.data.meshes.new(flame_name)
    flame_mesh.from_pydata(flame_verts, [], box_faces)
    flame_mesh.update(calc_edges=True)
    flame_mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("torch_flame", theme))
    flame_obj = bpy.data.objects.new(flame_name, flame_mesh)

    light_name = f"{name}_Light"
    light_data = bpy.data.lights.new(light_name, type='POINT')
    light_data.energy = _TORCH_LIGHT_ENERGY
    light_data.color = (1.0, 0.6, 0.25)
    light_data.shadow_soft_size = 0.15
    light_obj = bpy.data.objects.new(light_name, light_data)
    light_obj.location = (x, y, flame_z)

    return [holder_obj, flame_obj, light_obj]


def _build_corridor_mesh(corridor: Corridor, theme: str = "crypt", z0: float = 0.0):
    import bpy
    import mathutils

    start = mathutils.Vector(corridor.start)
    end = mathutils.Vector(corridor.end)
    direction = end - start
    if direction.length < 1e-6:
        direction = mathutils.Vector((1.0, 0.0))
    direction.normalize()
    side = mathutils.Vector((-direction.y, direction.x)) * (corridor.width / 2.0)

    corners = [
        (start.x + side.x, start.y + side.y),
        (start.x - side.x, start.y - side.y),
        (end.x - side.x, end.y - side.y),
        (end.x + side.x, end.y + side.y),
    ]
    # 1 cm below the room floors: every corridor starts at a room *center*
    # (see connect()), so the part inside the room was coplanar with the
    # room's own floor and z-fought as a black stripe across it; sunk a
    # hair, it is hidden under the floor and only shows between rooms.
    verts = [(px, py, z0 - 0.01) for (px, py) in corners]

    from ..materials import dungeon_mat

    name = f"{CORRIDOR_PREFIX}_{corridor.id}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update(calc_edges=True)
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("floor", theme))   # was unmaterialed (black)
    return bpy.data.objects.new(name, mesh)


def _get_or_create_collection(name, parent):
    import bpy

    existing = bpy.data.collections.get(name)
    if existing is None:
        existing = bpy.data.collections.new(name)
    if parent.children.get(name) is None:
        parent.children.link(existing)
    return existing


def clear_dungeon():
    """Remove any existing ProcgenMaps_Dungeon collection and its objects/meshes, if present."""
    import bpy

    root = bpy.data.collections.get(DUNGEON_ROOT_COLLECTION_NAME)
    if root is not None:
        _remove_collection_recursive(root)


def _remove_collection_recursive(collection):
    import bpy

    for child in list(collection.children):
        _remove_collection_recursive(child)
    for obj in list(collection.objects):
        data = obj.data
        obj_type = obj.type
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is None or data.users > 0:
            continue
        if obj_type == 'MESH':
            bpy.data.meshes.remove(data)
        elif obj_type == 'LIGHT':
            bpy.data.lights.remove(data)
    bpy.data.collections.remove(collection)
