"""City generator: orchestrates layout -> zones -> streets -> buildings -> props.

No bpy.types classes live in this package (operators live in ui/operators.py);
register()/unregister() are no-ops kept for the subpackage registration
contract used by procgen_maps/generators/__init__.py.
"""
from . import buildings, layout, props, streets, zones  # noqa: F401  (re-exported for callers)

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
    building_plans = buildings.plan_buildings(city_layout.blocks, zone_by_block, preset, seed=seed)
    prop_placements = props.plan_props(street_graph, city_layout.blocks, zone_by_block, preset, seed=seed,
                                        terrain_params=terrain_params)

    if parent_collection is None:
        parent_collection = bpy.context.scene.collection
    root = _get_or_create_collection(CITY_ROOT_COLLECTION_NAME, parent_collection)
    streets_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Streets", root)
    buildings_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Buildings", root)
    props_coll = _get_or_create_collection(f"{CITY_ROOT_COLLECTION_NAME}_Props", root)

    street_objects = streets.build_street_meshes(street_graph, preset, terrain_params, collection=streets_coll)
    building_objects = buildings.build_building_meshes(building_plans, terrain_params, collection=buildings_coll)
    prop_objects = props.build_props(prop_placements, collection=props_coll)

    return {
        "root_collection": root,
        "layout": city_layout,
        "zone_by_block": zone_by_block,
        "street_graph": street_graph,
        "building_plans": building_plans,
        "prop_placements": prop_placements,
        "street_objects": street_objects,
        "building_objects": building_objects,
        "prop_objects": prop_objects,
    }


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
        mesh = obj.data if obj.type == 'MESH' else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(collection)


def _get_or_create_collection(name, parent):
    import bpy

    existing = bpy.data.collections.get(name)
    if existing is None:
        existing = bpy.data.collections.new(name)
    if parent.children.get(name) is None:
        parent.children.link(existing)
    return existing
