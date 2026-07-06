"""Central asset spawning and LOD.

Builds each (asset_id, detail) primitive mesh exactly once, storing it in an
unlinked "source" Collection referenced by Blender's Instance Collection
mechanism - so placing thousands of props costs one lightweight Empty object
each, not thousands of independent mesh datablocks. `update_lod` cheaply
re-points already-spawned Empties at a different detail's source collection
based on distance from the camera; the geometry for every detail level is
built once and cached, so LOD switching never rebuilds meshes.
"""
import random

from . import library
from ..materials import prop_mat

_source_collection_cache = {}  # (asset_id, detail) -> bpy.types.Collection (never linked into the scene)

_TREE_SWAY_FREQ_RANGE = (0.02, 0.04)       # radians/frame; a full cycle every ~150-300 frames
_TREE_SWAY_AMPLITUDE_RANGE = (0.012, 0.027)  # radians; ~0.7-1.5 degrees, subtle rather than cartoonish


def spawn(asset_id: str, location, rotation_z: float = 0.0, scale: float = 1.0,
          detail: str = "high", collection=None):
    """Create one Instance-Collection Empty of `asset_id` at `location`. Returns the Empty object."""
    import bpy

    source_collection = _get_or_build_instance_source(asset_id, detail)

    empty = bpy.data.objects.new(f"{asset_id}_inst", None)
    empty.location = location
    empty.rotation_euler = (0.0, 0.0, rotation_z)
    empty.scale = (scale, scale, scale)
    empty.instance_type = 'COLLECTION'
    empty.instance_collection = source_collection
    empty["procgen_maps_asset_id"] = asset_id
    empty["procgen_maps_lod_detail"] = detail

    target = collection if collection is not None else bpy.context.scene.collection
    target.objects.link(empty)

    if library.get_asset(asset_id).category == "tree":
        _add_tree_sway_drivers(empty)

    return empty


def _add_tree_sway_drivers(empty):
    """A subtle, driver-based back-and-forth sway on the Empty's own X/Y
    rotation (Z is left alone - that's the placement yaw set in `spawn`) -
    no keyframes, so it costs nothing to generate for thousands of trees.
    Each tree's own (rounded, so still deterministic for a given layout)
    location seeds its own frequency/phase/amplitude, so a row of trees
    sways out of sync with its neighbors instead of like one rigid grid."""
    seed = hash((round(empty.location[0], 3), round(empty.location[1], 3))) & 0xFFFFFFFF
    rng = random.Random(seed)

    for axis_index in (0, 1):
        freq = rng.uniform(*_TREE_SWAY_FREQ_RANGE)
        amplitude = rng.uniform(*_TREE_SWAY_AMPLITUDE_RANGE)
        phase = rng.uniform(0.0, 6.2831853)
        fcurve = empty.driver_add("rotation_euler", axis_index)
        fcurve.driver.type = 'SCRIPTED'
        fcurve.driver.expression = f"sin(frame*{freq:.5f}+{phase:.5f})*{amplitude:.5f}"


def update_lod(empties, camera_location, high_distance, medium_distance):
    """Re-point each previously-spawned Empty's `instance_collection` to the
    detail level appropriate for its distance from `camera_location`."""
    import mathutils

    cam_co = mathutils.Vector(camera_location)
    for empty in empties:
        asset_id = empty.get("procgen_maps_asset_id")
        if not asset_id:
            continue
        distance = (empty.location - cam_co).length
        if distance <= high_distance:
            detail = "high"
        elif distance <= medium_distance:
            detail = "medium"
        else:
            detail = "low"

        if empty.get("procgen_maps_lod_detail") == detail:
            continue
        empty.instance_collection = _get_or_build_instance_source(asset_id, detail)
        empty["procgen_maps_lod_detail"] = detail


def _get_or_build_instance_source(asset_id: str, detail: str):
    """Return the (lazily built) source Collection containing exactly one
    master mesh object for (asset_id, detail). Intentionally never linked
    into the scene - it exists only to be referenced by Empties'
    `instance_collection`, so the geometry is stored once no matter how many
    instances are placed."""
    import bpy

    key = (asset_id, detail)
    cached = _source_collection_cache.get(key)
    if cached is not None and cached.name in bpy.data.collections:
        return cached

    asset_def = library.get_asset(asset_id)
    params = asset_def.builder(detail)
    mesh = _build_primitive_mesh(f"ProcgenMaps_Asset_{asset_id}_{detail}", params)
    master_obj = bpy.data.objects.new(f"ProcgenMaps_AssetMaster_{asset_id}_{detail}", mesh)

    source_collection = bpy.data.collections.new(f"ProcgenMaps_AssetSrc_{asset_id}_{detail}")
    source_collection.objects.link(master_obj)

    _source_collection_cache[key] = source_collection
    return source_collection


_PART_MATERIALS_BY_KIND = {
    "tree": ("tree_trunk", "tree_canopy"),
    "lamp": ("lamp_pole", "lamp_head"),
    "bench": ("bench",),
    "car": ("car",),
    "sign": ("sign_pole", "sign_board"),
    "rooftop_unit": ("rooftop_unit",),
    "fountain": ("fountain_stone", "fountain_water"),
    "parking_lot": ("parking_asphalt", "parking_line"),
    "bed": ("furniture_soft",),
    "table": ("furniture_wood",),
    "chair": ("furniture_wood",),
    "shelf": ("furniture_wood",),
    "desk": ("furniture_wood",),
    "counter": ("furniture_wood",),
    "crate": ("furniture_crate",),
    "machinery": ("furniture_metal",),
}


def _build_primitive_mesh(name: str, params: dict):
    import bmesh
    import bpy

    bm = bmesh.new()
    kind = params["kind"]
    part_faces = []  # list of (material_index, [BMFace, ...]) in build order

    if kind == "tree":
        part_faces.append((0, _add_cone(bm, params["trunk_radius"], params["trunk_radius"], params["height"] * 0.4,
                                         max(4, params["segments"] // 2), z_offset=params["height"] * 0.2)))
        canopy_z = params["height"] * 0.55
        canopy_shape = params["canopy_shape"]
        if canopy_shape == "sphere":
            part_faces.append((1, _add_icosphere(bm, params["canopy_radius"], 1,
                                                  z_offset=canopy_z + params["canopy_radius"] * 0.6)))
        elif canopy_shape == "cluster":
            # 3 overlapping icospheres of different size/offset instead of one
            # perfect sphere - a cheap way to break up the silhouette so a
            # row of trees doesn't read as identical balls-on-sticks.
            r = params["canopy_radius"]
            lobes = [
                (0.0, 0.0, r * 0.85, canopy_z + r * 0.55),
                (r * 0.5, r * 0.35, r * 0.6, canopy_z + r * 0.35),
                (-r * 0.45, -r * 0.4, r * 0.55, canopy_z + r * 0.3),
            ]
            faces = []
            for dx, dy, lobe_radius, lobe_z in lobes:
                faces.extend(_add_icosphere(bm, lobe_radius, 1, x_offset=dx, y_offset=dy, z_offset=lobe_z))
            part_faces.append((1, faces))
        else:
            part_faces.append((1, _add_cone(bm, params["canopy_radius"], 0.0, params["height"] * 0.6,
                                             params["segments"], z_offset=canopy_z)))

    elif kind == "lamp":
        part_faces.append((0, _add_cone(bm, params["pole_radius"], params["pole_radius"], params["height"],
                                         params["segments"], z_offset=params["height"] / 2.0)))
        part_faces.append((1, _add_icosphere(bm, params["head_radius"], 1, z_offset=params["height"])))

    elif kind == "bench":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], 0.08),
                                         z_offset=params["height"] * 0.5)))
        if params["detailed"]:
            part_faces.append((0, _add_cube(bm, (params["width"], 0.06, params["height"]),
                                             z_offset=params["height"], y_offset=-params["depth"] / 2.0)))

    elif kind == "car":
        part_faces.append((0, _add_cube(bm, (params["length"], params["width"], params["height"] * 0.55),
                                         z_offset=params["height"] * 0.3)))
        part_faces.append((0, _add_cube(bm, (params["length"] * 0.55, params["width"] * 0.9, params["height"] * 0.5),
                                         z_offset=params["height"] * 0.75)))

    elif kind == "sign":
        part_faces.append((0, _add_cone(bm, 0.04, 0.04, params["pole_height"], 6,
                                         z_offset=params["pole_height"] / 2.0)))
        part_faces.append((1, _add_cube(bm, (params["board_width"], 0.04, params["board_height"]),
                                         z_offset=params["pole_height"])))

    elif kind == "rooftop_unit":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], params["height"]),
                                         z_offset=params["height"] / 2.0)))

    elif kind == "fountain":
        base_r = params["base_radius"]
        base_h = params["base_height"]
        segments = params["segments"]
        part_faces.append((0, _add_cone(bm, base_r, base_r, base_h, segments, z_offset=base_h / 2.0)))
        part_faces.append((1, _add_cone(bm, base_r * 0.82, base_r * 0.82, 0.06, segments,
                                         z_offset=base_h + 0.03)))
        part_faces.append((0, _add_cone(bm, params["pillar_radius"], params["pillar_radius"] * 0.7,
                                         params["pillar_height"], max(6, segments),
                                         z_offset=base_h + params["pillar_height"] / 2.0)))
        part_faces.append((0, _add_icosphere(bm, params["pillar_radius"] * 1.3, 1,
                                              z_offset=base_h + params["pillar_height"])))

    elif kind == "bed":
        part_faces.append((0, _add_cube(bm, (params["width"], params["length"], params["height"] * 0.4),
                                         z_offset=params["height"] * 0.2)))
        part_faces.append((0, _add_cube(bm, (params["width"] * 0.9, params["length"] * 0.25, params["height"] * 0.3),
                                         z_offset=params["height"] * 0.55, y_offset=-params["length"] * 0.35)))

    elif kind == "table":
        part_faces.append((0, _add_cube(bm, (params["width"], params["length"], 0.05),
                                         z_offset=params["height"])))
        part_faces.append((0, _add_cube(bm, (params["width"] * 0.08, params["length"] * 0.08, params["height"]),
                                         z_offset=params["height"] / 2.0)))

    elif kind == "chair":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], params["height"] * 0.5),
                                         z_offset=params["height"] * 0.25)))
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"] * 0.12, params["height"] * 0.5),
                                         z_offset=params["height"] * 0.75, y_offset=-params["depth"] * 0.44)))

    elif kind == "shelf":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], params["height"]),
                                         z_offset=params["height"] / 2.0)))

    elif kind == "desk":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], 0.05), z_offset=params["height"])))
        part_faces.append((0, _add_cube(bm, (params["width"] * 0.08, params["depth"] * 0.08, params["height"]),
                                         z_offset=params["height"] / 2.0, y_offset=params["depth"] * 0.4)))

    elif kind == "counter":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], params["height"]),
                                         z_offset=params["height"] / 2.0)))

    elif kind == "crate":
        part_faces.append((0, _add_cube(bm, (params["size"], params["size"], params["size"]),
                                         z_offset=params["size"] / 2.0)))

    elif kind == "machinery":
        part_faces.append((0, _add_cube(bm, (params["width"], params["depth"], params["height"] * 0.7),
                                         z_offset=params["height"] * 0.35)))
        part_faces.append((0, _add_cone(bm, params["width"] * 0.25, params["width"] * 0.25, params["height"] * 0.4,
                                         max(4, params["segments"] // 2), z_offset=params["height"] * 0.9)))

    else:
        part_faces.append((0, _add_cube(bm, (1.0, 1.0, 1.0), z_offset=0.5)))

    for material_index, faces in part_faces:
        for face in faces:
            face.material_index = material_index

    bm.normal_update()
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    for material_key in _PART_MATERIALS_BY_KIND.get(kind, ("car",)):
        mesh.materials.append(prop_mat.get_or_create_prop_material(material_key))

    return mesh


def _add_cone(bm, radius1, radius2, depth, segments, z_offset=0.0):
    import bmesh
    from mathutils import Matrix

    before = len(bm.faces)
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=max(3, segments),
                           radius1=radius1, radius2=radius2, depth=depth,
                           matrix=Matrix.Translation((0, 0, z_offset)), calc_uvs=True)
    bm.faces.ensure_lookup_table()
    return list(bm.faces[before:])


def _add_icosphere(bm, radius, subdivisions, z_offset=0.0, x_offset=0.0, y_offset=0.0):
    import bmesh
    from mathutils import Matrix

    before = len(bm.faces)
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius,
                                matrix=Matrix.Translation((x_offset, y_offset, z_offset)), calc_uvs=True)
    bm.faces.ensure_lookup_table()
    return list(bm.faces[before:])


def _add_cube(bm, size, z_offset=0.0, y_offset=0.0):
    import bmesh
    from mathutils import Matrix

    sx, sy, sz = size
    matrix = Matrix.Translation((0, y_offset, z_offset)) @ Matrix.Diagonal((sx, sy, sz, 1.0))
    before = len(bm.faces)
    bmesh.ops.create_cube(bm, size=1.0, matrix=matrix, calc_uvs=True)
    bm.faces.ensure_lookup_table()
    return list(bm.faces[before:])
