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
"""
import itertools
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

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

    @property
    def area(self) -> float:
        return self.width * self.height


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


def build_dungeon_mesh(layout: DungeonLayout, collection) -> list:
    """Build one enclosed room object (floor+ceiling+walls-with-doorways)
    per Room, one flat floor-strip object per Corridor, and a torch (holder
    + flame + point light) at every doorway, linked into a
    'ProcgenMaps_Dungeon' collection under `collection`. Returns the list
    of all created objects."""
    dungeon_collection = _get_or_create_collection(DUNGEON_ROOT_COLLECTION_NAME, collection)

    room_type_by_id: Dict[int, str] = {}
    if layout.rooms:
        boss_room = max(layout.rooms, key=lambda r: r.area)
        start_room = min(layout.rooms, key=lambda r: r.area)
        if start_room.id != boss_room.id:
            room_type_by_id[start_room.id] = "start"
        room_type_by_id[boss_room.id] = "boss"

    created = []
    for room in layout.rooms:
        doorways = _find_doorways(room, layout.corridors)
        room_type = room_type_by_id.get(room.id, "normal")
        obj = _build_room_mesh(room, layout.wall_height, doorways, room_type)
        obj["procgen_maps_room_id"] = room.id
        obj["procgen_maps_room_type"] = room_type
        dungeon_collection.objects.link(obj)
        created.append(obj)

        for index, (wall, position, half_width) in enumerate(doorways):
            torch_pos = _torch_position_for_doorway(room, wall, position, half_width)
            for torch_obj in _build_torch(f"{TORCH_PREFIX}_{room.id}_{index}", torch_pos):
                dungeon_collection.objects.link(torch_obj)
                created.append(torch_obj)

    for corridor in layout.corridors:
        obj = _build_corridor_mesh(corridor)
        dungeon_collection.objects.link(obj)
        created.append(obj)

    return created


def _build_room_mesh(room: Room, wall_height: float,
                      doorways: List[Tuple[str, float, float]], room_type: str):
    import bpy

    from ..materials import dungeon_mat

    x0, y0 = room.x, room.y
    x1, y1 = room.x + room.width, room.y + room.height

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

    add_quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0), floor_faces)
    add_quad((x0, y1, wall_height), (x1, y1, wall_height), (x1, y0, wall_height), (x0, y0, wall_height),
              ceiling_faces)

    for a, b in south_segments:
        add_quad((a, y0, 0.0), (b, y0, 0.0), (b, y0, wall_height), (a, y0, wall_height), wall_faces)
    for a, b in north_segments:
        add_quad((b, y1, 0.0), (a, y1, 0.0), (a, y1, wall_height), (b, y1, wall_height), wall_faces)
    for a, b in west_segments:
        add_quad((x0, b, 0.0), (x0, a, 0.0), (x0, a, wall_height), (x0, b, wall_height), wall_faces)
    for a, b in east_segments:
        add_quad((x1, a, 0.0), (x1, b, 0.0), (x1, b, wall_height), (x1, a, wall_height), wall_faces)

    faces = wall_faces + floor_faces + ceiling_faces

    name = f"{ROOM_PREFIX}_{room.id}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)

    floor_key = {"start": "floor_start", "boss": "floor_boss"}.get(room_type, "floor")
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("wall"))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material(floor_key))
    mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("ceiling"))

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


def _build_torch(name: str, position: Tuple[float, float, float]):
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
    holder_mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("torch_holder"))
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
    flame_mesh.materials.append(dungeon_mat.get_or_create_dungeon_material("torch_flame"))
    flame_obj = bpy.data.objects.new(flame_name, flame_mesh)

    light_name = f"{name}_Light"
    light_data = bpy.data.lights.new(light_name, type='POINT')
    light_data.energy = _TORCH_LIGHT_ENERGY
    light_data.color = (1.0, 0.6, 0.25)
    light_data.shadow_soft_size = 0.15
    light_obj = bpy.data.objects.new(light_name, light_data)
    light_obj.location = (x, y, flame_z)

    return [holder_obj, flame_obj, light_obj]


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
