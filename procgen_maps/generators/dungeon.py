"""BSP dungeon generation: recursive space-partition rooms + corridors -> mesh.

Split per the project's PP/BPY convention: `generate_dungeon` (BSP split,
room carving, corridor routing) is pure Python and safe to unit test outside
Blender; `build_dungeon_mesh` is the thin bpy build step. Fully independent
of generators/city and generators/terrain - no shared state, presets or
collections.
"""
import itertools
import random
from dataclasses import dataclass, field
from typing import List, Tuple

DUNGEON_ROOT_COLLECTION_NAME = "ProcgenMaps_Dungeon"

ROOM_PREFIX = "ProcgenMaps_DungeonRoom"
CORRIDOR_PREFIX = "ProcgenMaps_DungeonCorridor"

_ROOM_MARGIN_RANGE = (0.5, 1.5)   # meters shrunk from each partition edge so rooms don't touch partition walls
_SPLIT_RATIO_RANGE = (0.4, 0.6)  # keeps both children of a split within a comparable size range
_SQUARE_BIAS_RATIO = 1.25        # aspect ratio above which a partition is split along its longer axis first


@dataclass(frozen=True)
class DungeonParams:
    width: float = 60.0
    height: float = 60.0
    min_room_size: float = 6.0
    max_depth: int = 5
    corridor_width: float = 1.6
    wall_height: float = 3.0
    seed: int = 0


@dataclass
class Room:
    id: int
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass
class Corridor:
    id: int
    start: Tuple[float, float]
    end: Tuple[float, float]
    width: float


@dataclass
class DungeonLayout:
    rooms: List[Room] = field(default_factory=list)
    corridors: List[Corridor] = field(default_factory=list)
    wall_height: float = 3.0


def generate_dungeon(params: DungeonParams) -> DungeonLayout:
    """Recursively BSP-split the width x height rectangle (centered on the
    origin), carve one Room per leaf partition, and connect sibling
    partitions' rooms with straight/L-shaped Corridor segments."""
    rng = random.Random(params.seed)
    rooms: List[Room] = []
    corridors: List[Corridor] = []
    room_ids = itertools.count()
    corridor_ids = itertools.count()

    def carve_room(x, y, w, h) -> Room:
        margin_x = min(rng.uniform(*_ROOM_MARGIN_RANGE), w * 0.3)
        margin_y = min(rng.uniform(*_ROOM_MARGIN_RANGE), h * 0.3)
        room = Room(next(room_ids), x + margin_x, y + margin_y, w - 2 * margin_x, h - 2 * margin_y)
        rooms.append(room)
        return room

    def connect(room_a: Room, room_b: Room):
        ax, ay = room_a.center
        bx, by = room_b.center
        if abs(ax - bx) < 1e-6 or abs(ay - by) < 1e-6:
            corridors.append(Corridor(next(corridor_ids), (ax, ay), (bx, by), params.corridor_width))
            return
        elbow = (bx, ay)
        corridors.append(Corridor(next(corridor_ids), (ax, ay), elbow, params.corridor_width))
        corridors.append(Corridor(next(corridor_ids), elbow, (bx, by), params.corridor_width))

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
    return DungeonLayout(rooms=rooms, corridors=corridors, wall_height=params.wall_height)


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


def build_dungeon_mesh(layout: DungeonLayout, collection) -> list:
    """Build one box-room object per Room and one flat floor-strip object per
    Corridor, linked into a 'ProcgenMaps_Dungeon' collection under `collection`.
    Returns the list of all created objects."""
    dungeon_collection = _get_or_create_collection(DUNGEON_ROOT_COLLECTION_NAME, collection)

    created = []
    for room in layout.rooms:
        obj = _build_room_mesh(room, layout.wall_height)
        obj["procgen_maps_room_id"] = room.id
        dungeon_collection.objects.link(obj)
        created.append(obj)

    for corridor in layout.corridors:
        obj = _build_corridor_mesh(corridor)
        dungeon_collection.objects.link(obj)
        created.append(obj)

    return created


def _build_room_mesh(room: Room, wall_height: float):
    import bpy

    x0, y0 = room.x, room.y
    x1, y1 = room.x + room.width, room.y + room.height
    verts = [
        (x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0),
        (x0, y0, wall_height), (x1, y0, wall_height), (x1, y1, wall_height), (x0, y1, wall_height),
    ]
    faces = [
        (0, 1, 2, 3),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]

    name = f"{ROOM_PREFIX}_{room.id}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    return bpy.data.objects.new(name, mesh)


def _build_corridor_mesh(corridor: Corridor):
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
    verts = [(px, py, 0.0) for (px, py) in corners]

    name = f"{CORRIDOR_PREFIX}_{corridor.id}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update(calc_edges=True)
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
        mesh = obj.data if obj.type == 'MESH' else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(collection)
