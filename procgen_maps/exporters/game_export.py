"""Game-engine-ready glTF export: clean hierarchy, collision meshes, LODs
and a navmesh-friendly walkable ground, on top of Blender's glTF exporter.

Everything here is temporary scene surgery wrapped in try/finally: for
the duration of one `bpy.ops.export_scene.gltf` call the generated
objects are parented under a few root Empties (`City`, `Terrain`,
`Dungeon`, each with sub-groups), the collision / LOD / walkable objects
are created next to them, and afterwards every name, parent and extra
datablock is put back exactly as it was - the Blender scene the user
keeps working in is untouched by an export.

Naming conventions (`collision_name`, `lod_name`, `walkable_name` are
pure Python and unit-tested):

- Godot: `<name>-colonly` is imported as a collision shape without a
  visible mesh, `<name>-col` as a visible mesh that is also a trimesh
  collider, `<name>-navmesh` becomes a NavigationRegion3D. The whole
  convention lives in Godot's scene importer, no plugin needed.
- Unity: there is no importer-side naming convention for colliders, so
  every box collider goes under a `Collision` group with a `_col` suffix
  (a one-line editor script turns them into BoxColliders) and large
  meshes keep their own names. LODs follow Unity's own convention
  (`_LOD0`, `_LOD1` siblings under one parent -> an automatic LODGroup).
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

EXPORT_ROOT_NAME = "ProcgenMaps_Export"   # temporary collection holding the hierarchy empties
WALKABLE_GROUND_NAME = "Ground_Walkable"
WALKABLE_DUNGEON_NAME = "Dungeon_Walkable"
LOD1_FACE_LIMIT = 12                       # a LOD1 is one box (plus a hip for gable roofs)
WALKABLE_TERRAIN_TARGET = 160              # vertices per side the walkable terrain is downsampled to

_GODOT_SUFFIX = {"only": "-colonly", "mesh": "-col", "convex": "-convcolonly", "navmesh": "-navmesh"}
_UNITY_SUFFIX = {"only": "_col", "mesh": "", "convex": "_col", "navmesh": "_navmesh"}


@dataclass(frozen=True)
class GameExportOptions:
    collision: bool = True
    naming: str = "GODOT"        # "GODOT" | "UNITY"
    lods: bool = True
    walkable: bool = True
    lights: bool = True


def collision_name(base: str, naming: str, kind: str = "only") -> str:
    """Name a collision object the way the target engine's importer reads
    it. `kind`: "only" (invisible box/hull), "mesh" (the visible mesh is
    also the collider) or "convex"."""
    table = _GODOT_SUFFIX if naming.upper() == "GODOT" else _UNITY_SUFFIX
    return base + table[kind]


def lod_name(base: str, level: int) -> str:
    return f"{base}_LOD{level}"


def walkable_name(base: str, naming: str) -> str:
    table = _GODOT_SUFFIX if naming.upper() == "GODOT" else _UNITY_SUFFIX
    return base + table["navmesh"]


def box_corners(min_corner, max_corner) -> Tuple[list, list]:
    """(verts, faces) of a closed axis-aligned box - the collision / LOD1
    primitive. Pure Python."""
    x0, y0, z0 = min_corner
    x1, y1, z1 = max_corner
    verts = [(x, y, z) for z in (z0, z1) for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return verts, faces


def grid_downsample_stride(resolution: int, target: int = WALKABLE_TERRAIN_TARGET) -> int:
    """Stride that brings a resolution x resolution vertex grid down to
    about `target` per side (never below 1). Pure Python."""
    return max(1, int(math.ceil((resolution - 1) / max(1, target - 1))))


def downsample_grid(coords, resolution: int, stride: int):
    """(verts, faces) of every stride-th row/column of a row-major
    resolution x resolution vertex grid, keeping the last row/column so
    the footprint stays the same. Pure numpy."""
    import numpy as np

    grid = np.asarray(coords, dtype=np.float64).reshape(resolution, resolution, 3)
    index = list(range(0, resolution, stride))
    if index[-1] != resolution - 1:
        index.append(resolution - 1)
    sub = grid[np.ix_(index, index)]
    n = len(index)
    verts = sub.reshape(-1, 3)
    rows, cols = np.mgrid[0:n - 1, 0:n - 1]
    i00 = (rows * n + cols).ravel()
    faces = np.stack((i00, i00 + 1, i00 + n + 1, i00 + n), axis=1)
    return verts, faces


# --- bpy half ----------------------------------------------------------------

class _Surgery:
    """Records everything the export changes in the scene so `restore`
    can put it back."""

    def __init__(self):
        self.created_objects = []
        self.created_meshes = []
        self.created_collections = []
        self.renamed: List[Tuple[object, str]] = []        # (object, original name)
        self.reparented: List[Tuple[object, object]] = []  # (object, original parent)
        self.instance_swaps: List[Tuple[object, object]] = []  # (empty, original instance_collection)
        self.built_sources: List[Tuple[str, str]] = []     # (asset_id, detail) factory sources built just for the export

    def restore(self):
        import bpy

        for empty, original in self.instance_swaps:
            try:
                empty.instance_collection = original
            except ReferenceError:
                pass
        for obj, parent in self.reparented:
            try:
                obj.parent = parent
                obj.matrix_parent_inverse.identity()
            except ReferenceError:
                pass
        for obj in self.created_objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        for mesh in self.created_meshes:
            try:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass
        for obj, name in self.renamed:
            try:
                obj.name = name
            except ReferenceError:
                pass
        for collection in self.created_collections:
            try:
                bpy.data.collections.remove(collection)
            except ReferenceError:
                pass
        # Low-detail prop sources the factory built only for the LOD export
        # go too, so an export never grows the scene's datablocks.
        from ..assets import factory
        for key in self.built_sources:
            source = factory._source_collection_cache.pop(key, None)
            if source is None:
                continue
            try:
                for master in list(source.objects):
                    mesh = master.data
                    bpy.data.objects.remove(master, do_unlink=True)
                    if mesh is not None and mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
                bpy.data.collections.remove(source)
            except ReferenceError:
                pass


def export_game_gltf(filepath: str, options: GameExportOptions = GameExportOptions()) -> Dict[str, int]:
    """Export the generated map as a game-ready GLB. Returns a summary of
    what was added (collision objects, LOD objects, walkable meshes)."""
    import bpy

    surgery = _Surgery()
    summary = {"collision": 0, "lods": 0, "walkable": 0, "roots": 0}
    try:
        _build_hierarchy(surgery, options, summary)
        kwargs = dict(filepath=filepath, export_format='GLB', export_apply=True, use_selection=False,
                      export_extras=True, export_lights=options.lights)
        declared = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
        kwargs = {key: value for key, value in kwargs.items() if key in declared}
        bpy.ops.export_scene.gltf(**kwargs)
    finally:
        surgery.restore()
    return summary


def _build_hierarchy(surgery: _Surgery, options: GameExportOptions, summary: Dict[str, int]):
    import bpy

    from ..generators import terrain as terrain_gen
    from ..generators.city import CITY_ROOT_COLLECTION_NAME
    from ..generators.dungeon import DUNGEON_ROOT_COLLECTION_NAME

    scene = bpy.context.scene
    root_coll = bpy.data.collections.new(EXPORT_ROOT_NAME)
    scene.collection.children.link(root_coll)
    surgery.created_collections.append(root_coll)

    def empty(name, parent=None):
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_size = 1.0
        root_coll.objects.link(obj)
        if parent is not None:
            obj.parent = parent
        surgery.created_objects.append(obj)
        return obj

    def adopt(obj, parent):
        if obj.parent is not None:
            return
        surgery.reparented.append((obj, obj.parent))
        obj.parent = parent
        obj.matrix_parent_inverse.identity()

    def rename(obj, new_name):
        surgery.renamed.append((obj, obj.name))
        obj.name = new_name

    # Terrain
    terrain_obj = bpy.data.objects.get(terrain_gen.TERRAIN_OBJECT_NAME)
    walkable_parts = []
    terrain_root = None
    if terrain_obj is not None:
        terrain_root = empty("Terrain")
        summary["roots"] += 1
        adopt(terrain_obj, terrain_root)
        if options.collision and options.naming.upper() == "GODOT":
            rename(terrain_obj, collision_name("Ground", options.naming, "mesh"))
            summary["collision"] += 1
        else:
            rename(terrain_obj, "Ground")
        if options.walkable:
            walkable_parts.append(("terrain", terrain_obj))

    # City
    city_coll = bpy.data.collections.get(CITY_ROOT_COLLECTION_NAME)
    if city_coll is not None:
        city_root = empty("City")
        summary["roots"] += 1
        collision_root = empty("Collision", city_root) if options.collision else None
        for child in city_coll.children:
            group_name = child.name.replace(f"{CITY_ROOT_COLLECTION_NAME}_", "")
            group = empty(group_name, city_root)
            for obj in list(child.objects):
                adopt(obj, group)
                _export_name_for_city_object(obj, group_name, options, rename, walkable_parts, summary)
                if obj.type == 'MESH' and obj.get("procgen_maps_facade_type") is not None:
                    if options.lods:
                        _add_building_lod(surgery, obj, group, summary)
                    if options.collision:
                        _add_box_collider(surgery, obj, collision_root, options, summary)
                elif obj.type == 'EMPTY' and obj.instance_collection is not None and options.collision:
                    _add_instance_collider(surgery, obj, collision_root, options, summary)
        if options.lods:
            _add_prop_lods(surgery, city_coll, summary)

    # Dungeon
    dungeon_coll = bpy.data.collections.get(DUNGEON_ROOT_COLLECTION_NAME)
    if dungeon_coll is not None:
        dungeon_root = empty("Dungeon")
        summary["roots"] += 1
        groups = {"Rooms": empty("Rooms", dungeon_root), "Corridors": empty("Corridors", dungeon_root),
                  "Torches": empty("Torches", dungeon_root), "Props": empty("Props", dungeon_root),
                  "Stairs": empty("Stairs", dungeon_root)}
        for obj in list(dungeon_coll.objects):
            name = obj.name
            if "Torch" in name:
                group = "Torches"
            elif "Corridor" in name:
                group = "Corridors"
            elif "Stairs" in name:
                group = "Stairs"
            elif "Features" in name or "Prop" in name or "Door" in name:
                group = "Props"
            else:
                group = "Rooms"
            adopt(obj, groups[group])
            if options.collision and options.naming.upper() == "GODOT" and obj.type == 'MESH' \
                    and group in ("Rooms", "Corridors", "Props", "Stairs"):
                rename(obj, collision_name(obj.name, options.naming, "mesh"))
                summary["collision"] += 1
        if options.walkable:
            floors = [obj for obj in dungeon_coll.objects if obj.type == 'MESH'
                      and ("Room" in obj.name or "Corridor" in obj.name) and "Features" not in obj.name]
            if floors:
                _add_dungeon_walkable(surgery, floors, dungeon_root, options, summary)

    if walkable_parts:
        _add_ground_walkable(surgery, walkable_parts, terrain_root, options, summary)


def _export_name_for_city_object(obj, group_name, options, rename, walkable_parts, summary):
    """Readable names for the big single meshes, with the Godot trimesh
    collider suffix where the mesh itself is the collider."""
    name = obj.name
    pretty = {"ProcgenMaps_Street_Network": "Streets", "ProcgenMaps_Water": "Water",
              "ProcgenMaps_Bridges": "Bridges", "ProcgenMaps_Parks": "ParkPaths"}.get(name)
    if pretty is None:
        return
    walkable = pretty in ("Streets", "ParkPaths", "Bridges")
    if walkable and options.walkable:
        walkable_parts.append((pretty, obj))
    if options.collision and options.naming.upper() == "GODOT" and pretty != "Water":
        rename(obj, collision_name(pretty, options.naming, "mesh"))
        summary["collision"] += 1
    else:
        rename(obj, pretty)


def _world_bounds(obj):
    import mathutils

    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    return lo, hi


def _new_box_object(surgery, name, lo, hi, materials=()):
    import bpy

    verts, faces = box_corners(lo, hi)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    for material in materials:
        mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    surgery.created_meshes.append(mesh)
    surgery.created_objects.append(obj)
    return obj


def _add_building_lod(surgery, building, group, summary):
    """Unity LODGroup layout: `<name>` (empty) with `<name>_LOD0` (the full
    facade mesh) and `<name>_LOD1` (one box in the facade material, with
    the building's own shading properties so it matches in color)."""
    import bpy

    base = building.name
    surgery.renamed.append((building, base))
    building.name = lod_name(base, 0)
    holder = bpy.data.objects.new(base, None)
    bpy.data.collections[EXPORT_ROOT_NAME].objects.link(holder)
    holder.parent = group
    surgery.created_objects.append(holder)
    building.parent = holder
    building.matrix_parent_inverse.identity()

    lo, hi = _world_bounds(building)
    materials = [building.data.materials[0]] if building.data.materials else []
    lod1 = _new_box_object(surgery, lod_name(base, 1), lo, hi, materials)
    for key in ("procgen_maps_material_index", "procgen_maps_tint", "procgen_maps_base_z",
                "procgen_maps_floor_height", "procgen_maps_facade_type"):
        value = building.get(key)
        if value is not None:
            lod1[key] = value
    bpy.data.collections[EXPORT_ROOT_NAME].objects.link(lod1)
    lod1.parent = holder
    summary["lods"] += 1


def _add_box_collider(surgery, obj, collision_root, options, summary):
    lo, hi = _world_bounds(obj)
    base = obj.name[:-5] if obj.name.endswith("_LOD0") else obj.name
    collider = _new_box_object(surgery, collision_name(base, options.naming), lo, hi)
    collider.display_type = 'WIRE'
    import bpy
    bpy.data.collections[EXPORT_ROOT_NAME].objects.link(collider)
    collider.parent = collision_root
    summary["collision"] += 1


_instance_bounds_cache = {}


def _add_instance_collider(surgery, empty, collision_root, options, summary):
    """One box per placed prop, in the prop's own local frame (so the
    collider carries the instance's rotation/scale), sized from the
    instanced collection's master mesh bounds. Trees collide with their
    trunk only, so a navmesh/agent can walk under the canopy."""
    import bpy
    import mathutils

    source = empty.instance_collection
    key = source.name
    bounds = _instance_bounds_cache.get(key)
    if bounds is None or bounds[2] != len(source.objects):
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for master in source.objects:
            for corner in master.bound_box:
                world = master.matrix_world @ mathutils.Vector(corner)
                for i in range(3):
                    lo[i] = min(lo[i], world[i])
                    hi[i] = max(hi[i], world[i])
        if math.isinf(lo[0]):
            return
        bounds = (lo, hi, len(source.objects))
        _instance_bounds_cache[key] = bounds
    lo, hi = list(bounds[0]), list(bounds[1])
    asset_id = empty.get("procgen_maps_asset_id", "")
    if asset_id.startswith("tree"):
        radius = max(0.15, (hi[0] - lo[0]) * 0.08)
        lo[0], lo[1], hi[0], hi[1] = -radius, -radius, radius, radius
    collider = _new_box_object(surgery, collision_name(empty.name, options.naming), lo, hi)
    collider.display_type = 'WIRE'
    collider.matrix_world = empty.matrix_world.copy()
    bpy.data.collections[EXPORT_ROOT_NAME].objects.link(collider)
    collider.parent = collision_root
    collider.matrix_parent_inverse.identity()
    collider.matrix_world = empty.matrix_world.copy()
    summary["collision"] += 1


def _add_prop_lods(surgery, city_coll, summary):
    """Props are collection instances: for the export every instance points
    at a per-asset LOD collection holding `<asset>_LOD0` (the high-detail
    master mesh) and `<asset>_LOD1` (the low one) - the instance node
    then expands to a Unity-style LOD group in the glTF, with every
    instance still sharing the two meshes."""
    import bpy

    from ..assets import factory

    lod_collections = {}
    for coll in [city_coll] + _descendants(city_coll):
        for obj in list(coll.objects):
            if obj.type != 'EMPTY' or obj.instance_collection is None:
                continue
            asset_id = obj.get("procgen_maps_asset_id")
            if not asset_id:
                continue
            lod_coll = lod_collections.get(asset_id)
            if lod_coll is None:
                lod_coll = bpy.data.collections.new(f"ProcgenMaps_ExportLOD_{asset_id}")
                surgery.created_collections.append(lod_coll)
                for level, detail in ((0, "high"), (1, "low")):
                    existed = (asset_id, detail) in factory._source_collection_cache
                    source = factory._get_or_build_instance_source(asset_id, detail)
                    if not existed:
                        surgery.built_sources.append((asset_id, detail))
                    master = source.objects[0] if source.objects else None
                    if master is None:
                        continue
                    lod_obj = bpy.data.objects.new(lod_name(asset_id, level), master.data)
                    lod_coll.objects.link(lod_obj)
                    surgery.created_objects.append(lod_obj)
                lod_collections[asset_id] = lod_coll
                summary["lods"] += 1
            surgery.instance_swaps.append((obj, obj.instance_collection))
            obj.instance_collection = lod_coll


def _descendants(collection):
    result = []
    for child in collection.children:
        result.append(child)
        result.extend(_descendants(child))
    return result


def _add_ground_walkable(surgery, parts, parent, options, summary):
    """One navmesh-friendly mesh: the terrain downsampled to ~160 vertices
    per side plus the street network and park paving (which already span
    the river on the bridges), joined with world transforms applied."""
    import bpy
    import numpy as np

    verts_all = []
    faces_all = []
    offset = 0
    for kind, obj in parts:
        mesh = obj.data
        count = len(mesh.vertices)
        coords = np.empty(count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coords)
        if kind == "terrain":
            res = int(round(math.sqrt(count)))
            if res * res != count:
                continue
            stride = grid_downsample_stride(res)
            verts, faces = downsample_grid(coords, res, stride)
            verts_all.append(verts)
            faces_all.extend((tuple(int(i) + offset for i in face) for face in faces))
            offset += len(verts)
        else:
            verts_all.append(coords.reshape(-1, 3).astype(np.float64))
            faces_all.extend(tuple(v + offset for v in poly.vertices) for poly in mesh.polygons)
            offset += count
    if not verts_all:
        return
    name = walkable_name(WALKABLE_GROUND_NAME, options.naming)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in np.concatenate(verts_all)], [], faces_all)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    obj.display_type = 'WIRE'
    surgery.created_meshes.append(mesh)
    surgery.created_objects.append(obj)
    bpy.data.collections[EXPORT_ROOT_NAME].objects.link(obj)
    if parent is not None:
        obj.parent = parent
    summary["walkable"] += 1


def _add_dungeon_walkable(surgery, floor_objects, parent, options, summary):
    """The dungeon's floor faces only (room floors = material slot 1,
    corridor strips), as one mesh for a navmesh bake."""
    import bpy

    verts = []
    faces = []
    for obj in floor_objects:
        mesh = obj.data
        for poly in mesh.polygons:
            if "Corridor" not in obj.name and poly.material_index != 1:
                continue
            base = len(verts)
            verts.extend(tuple(obj.matrix_world @ mesh.vertices[i].co) for i in poly.vertices)
            faces.append(tuple(range(base, base + len(poly.vertices))))
    if not faces:
        return
    name = walkable_name(WALKABLE_DUNGEON_NAME, options.naming)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    obj = bpy.data.objects.new(name, mesh)
    obj.display_type = 'WIRE'
    surgery.created_meshes.append(mesh)
    surgery.created_objects.append(obj)
    bpy.data.collections[EXPORT_ROOT_NAME].objects.link(obj)
    obj.parent = parent
    summary["walkable"] += 1


def verify_glb(filepath: str) -> Dict[str, int]:
    """Re-import a GLB headlessly and count what came back (object total,
    collision objects, LOD objects, walkable meshes, root nodes); every
    imported datablock is removed again. Used by the smoke test."""
    import bpy

    before = {obj.name for obj in bpy.data.objects}
    materials_before = {m.name for m in bpy.data.materials}
    images_before = {i.name for i in bpy.data.images}
    collections_before = {c.name for c in bpy.data.collections}
    bpy.ops.import_scene.gltf(filepath=filepath)
    imported = [obj for obj in bpy.data.objects if obj.name not in before]
    names = [obj.name for obj in imported]
    counts = {
        "objects": len(imported),
        "collision": sum(1 for n in names if "-col" in n or "_col" in n),
        "lod0": sum(1 for n in names if "_LOD0" in n),
        "lod1": sum(1 for n in names if "_LOD1" in n),
        "walkable": sum(1 for n in names if "Walkable" in n),
        "roots": sum(1 for obj in imported if obj.parent is None and obj.name.split(".")[0] in ("City", "Terrain", "Dungeon")),
        "meshes": sum(1 for obj in imported if obj.type == 'MESH'),
    }
    # Remove what the import added, by hand: orphans_purge would also take
    # the addon's own unlinked instance-source collections with it.
    for obj in imported:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0 and getattr(data, "bl_rna", None) is not None:
            if data.bl_rna.identifier == "Mesh":
                bpy.data.meshes.remove(data)
            elif data.bl_rna.identifier in ("PointLight", "SpotLight", "SunLight", "AreaLight", "Light"):
                bpy.data.lights.remove(data)
    for material in [m for m in bpy.data.materials if m.users == 0 and m.name not in materials_before]:
        bpy.data.materials.remove(material)
    for image in [i for i in bpy.data.images if i.users == 0 and i.name not in images_before]:
        bpy.data.images.remove(image)
    for collection in [c for c in bpy.data.collections if c.name not in collections_before and not c.users]:
        bpy.data.collections.remove(collection)
    return counts
