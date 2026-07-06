"""City generator: orchestrates layout -> zones -> streets -> buildings -> props.

No bpy.types classes live in this package (operators live in ui/operators.py);
register()/unregister() are no-ops kept for the subpackage registration
contract used by procgen_maps/generators/__init__.py.
"""
from . import buildings, layout, parking, props, signage, special_buildings, streets, zones  # noqa: F401

CITY_ROOT_COLLECTION_NAME = "ProcgenMaps_City"


def register():
    pass


def unregister():
    pass


def generate_city(preset, terrain_params=None, seed=None, parent_collection=None):
    """Run the full layout -> zones -> streets -> buildings -> props pipeline
    and build all resulting meshes into a fresh 'ProcgenMaps_City' collection
    hierarchy, nested under `parent_collection` (defaults to the scene's root
    collection if not given). Returns a dict of the created collections and
    intermediate data, useful for the profiler and for tests."""
    import bpy

    city_layout = layout.generate_layout(preset, seed=seed)
    zone_by_block = zones.classify_zones(city_layout.blocks, preset, seed=seed)
    street_graph = streets.build_street_graph(city_layout.streets)

    special_plans, reserved_block_ids = special_buildings.plan_special_buildings(
        city_layout.blocks, zone_by_block, preset, seed=seed)
    regular_blocks = [b for b in city_layout.blocks if b.id not in reserved_block_ids]
    building_plans = buildings.plan_buildings(regular_blocks, zone_by_block, preset, seed=seed)
    parking_lot_specs = parking.plan_parking_lots_for_supermarkets(city_layout.blocks, special_plans)

    prop_placements = props.plan_props(street_graph, city_layout.blocks, zone_by_block, preset, seed=seed,
                                        terrain_params=terrain_params,
                                        building_plans=building_plans + special_plans)

    sign_placements = signage.plan_signage(street_graph, preset, seed=seed, terrain_params=terrain_params,
                                            building_plans=building_plans + special_plans,
                                            prop_placements=prop_placements)

    _flatten_terrain_under_buildings(terrain_params, building_plans + special_plans)

    if parent_collection is None:
        parent_collection = bpy.context.scene.collection
    root = _get_or_create_collection(CITY_ROOT_COLLECTION_NAME, parent_collection)
    streets_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Streets", root)
    buildings_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Buildings", root)
    props_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Props", root)
    signage_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Signage", root)
    parking_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Parking", root)

    street_objects = streets.build_street_meshes(street_graph, preset, terrain_params, collection=streets_coll)
    building_objects, building_extra_objects = buildings.build_building_meshes(
        building_plans, terrain_params, collection=buildings_coll)
    special_objects, special_extra_objects = special_buildings.build_special_building_meshes(
        special_plans, terrain_params, collection=buildings_coll)
    prop_objects = props.build_props(prop_placements, collection=props_coll)
    sign_objects = signage.build_signage_meshes(sign_placements, collection=signage_coll)
    parking_objects = []
    for spec in parking_lot_specs:
        parking_objects.extend(parking.build_parking_lot(spec, terrain_params, collection=parking_coll))

    return {
        "root_collection": root,
        "layout": city_layout,
        "zone_by_block": zone_by_block,
        "street_graph": street_graph,
        "building_plans": building_plans,
        "special_building_plans": special_plans,
        "prop_placements": prop_placements,
        "sign_placements": sign_placements,
        "parking_lot_specs": parking_lot_specs,
        "street_objects": street_objects,
        # Special buildings merged into the same keys as the 12 generic
        # facades: they share the exact same mesh/material shape (see
        # special_buildings.py), so every existing caller (material
        # assignment, stats counting) already handles both without change.
        "building_objects": building_objects + special_objects,
        "building_extra_objects": building_extra_objects + special_extra_objects,
        "prop_objects": prop_objects,
        "sign_objects": sign_objects,
        "parking_objects": parking_objects,
    }


def _flatten_terrain_under_buildings(terrain_params, plans):
    """Reshape the visible terrain mesh so every building's flat foundation
    sits flush on the ground, using the exact same base_z formula
    buildings.build_building_meshes uses (footprint-centroid height
    sample) - see generators/terrain.py's flatten_terrain_for_footprints
    for why the mesh needs this at all (a coarse grid can't otherwise
    match the precise continuous height every building/street/prop/sign
    samples directly)."""
    import bpy

    from .. import terrain as terrain_gen
    from ...config import settings as global_settings

    if terrain_params is None or not plans:
        return
    terrain_obj = bpy.data.objects.get(terrain_gen.TERRAIN_OBJECT_NAME)
    if terrain_obj is None:
        return

    footprints_with_target_z = []
    for plan in plans:
        cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
        cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
        base_z = terrain_gen.sample_world_height(cx, cy, terrain_params)
        footprints_with_target_z.append((plan.footprint, base_z))

    terrain_gen.flatten_terrain_for_footprints(terrain_obj, terrain_params, footprints_with_target_z,
                                                margin=global_settings.TERRAIN_FLATTEN_MARGIN)


def clear_city():
    """Remove any existing ProcgenMaps_City collection hierarchy and its objects/meshes, if present."""
    import bpy

    root = bpy.data.collections.get(CITY_ROOT_COLLECTION_NAME)
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
        elif obj_type == 'FONT':
            bpy.data.curves.remove(data)
    bpy.data.collections.remove(collection)


def _get_or_create_collection(name, parent):
    import bpy

    existing = bpy.data.collections.get(name)
    if existing is None:
        existing = bpy.data.collections.new(name)
    if parent.children.get(name) is None:
        parent.children.link(existing)
    return existing
