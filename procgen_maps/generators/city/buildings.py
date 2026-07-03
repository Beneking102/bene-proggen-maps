"""Procedural building generation: 12 facade archetypes over block footprints.

`plan_buildings` is pure Python: decides which blocks get a building, how
tall, and which facade type, from a data table of facade parameters, with
per-building jitter on window frequency/pitch/floor height so buildings
sharing a facade type don't look identical. `build_building_meshes` is the
bpy build step: extrudes each footprint upward floor-by-floor with bmesh,
splitting each floor's side faces into width_pitch-wide window columns and
punching recessed windows into a frequency-controlled subset of them,
carving a wide entrance recess on the ground floor's -Y-facing side, adding
a rooftop utility unit on some flat-roofed buildings, and building a proper
gable (not a single-point pyramid poke) for 'peaked' roof styles. It also
builds a simple furnished ground-floor interior (see
_build_ground_floor_interior) visible through the transmissive window glass
(materials/city_mat.py's get_or_create_window_material). The facade type,
material index, and per-building tint are stored as custom object
properties so materials/city_mat.py can key a shared parametrized shader
off them.
"""
import dataclasses
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from .layout import Block

_WINDOW_FREQUENCY_JITTER = 0.15
_WIDTH_PITCH_JITTER = 0.15
_FLOOR_HEIGHT_JITTER = 0.08
_ROOFTOP_UNIT_CHANCE = 0.4

BUILDINGS_PREFIX = "ProcgenMaps_Building"


@dataclass(frozen=True)
class FacadeType:
    key: str
    zone_pool: Tuple[str, ...]     # zones allowed to use this facade
    window_frequency: float        # 0..1, chance each window bay gets a recessed window
    width_pitch: float             # meters per window bay/column
    roof_style: str                # 'flat' | 'peaked'
    material_index: int


FACADE_TYPES: Tuple[FacadeType, ...] = (
    FacadeType("glass_tower", ("commercial",), 0.9, 2.5, "flat", 0),
    FacadeType("office_block", ("commercial",), 0.7, 3.0, "flat", 1),
    FacadeType("brick_commercial", ("commercial", "residential"), 0.6, 3.5, "flat", 2),
    FacadeType("apartment_slab", ("residential",), 0.55, 3.2, "flat", 3),
    FacadeType("apartment_tower", ("residential",), 0.5, 3.0, "flat", 4),
    FacadeType("townhouse", ("residential",), 0.4, 3.5, "peaked", 5),
    FacadeType("cottage", ("residential",), 0.3, 3.0, "peaked", 6),
    FacadeType("shopfront", ("commercial",), 0.8, 2.8, "flat", 7),
    FacadeType("warehouse", ("industrial",), 0.2, 5.0, "flat", 8),
    FacadeType("factory_hall", ("industrial",), 0.15, 6.0, "peaked", 9),
    FacadeType("industrial_tower", ("industrial",), 0.25, 4.0, "flat", 10),
    FacadeType("mixed_use", ("commercial", "residential"), 0.65, 3.2, "peaked", 11),
)


@dataclass
class BuildingPlan:
    block_id: int
    footprint: List[Tuple[float, float]]
    floors: int
    floor_height: float
    facade: FacadeType
    tint: float = 0.5  # 0..1, per-building brightness variation (see materials/city_mat.py)


def plan_buildings(blocks: List[Block], zone_by_block: Dict[int, str], preset, seed=None) -> List[BuildingPlan]:
    """Decide which blocks get a building, how tall, and which facade type."""
    rng = np.random.default_rng(preset.seed if seed is None else seed)
    plans: List[BuildingPlan] = []

    for block in blocks:
        zone = zone_by_block.get(block.id, "residential")
        if zone == "park":
            continue
        if rng.random() > preset.density:
            continue

        pool = [f for f in FACADE_TYPES if zone in f.zone_pool] or list(FACADE_TYPES)
        facade = pool[rng.integers(0, len(pool))]

        # Per-building jitter on the facade's own tuning so buildings sharing
        # a facade type don't look like identical copies.
        jittered_frequency = float(np.clip(
            facade.window_frequency + rng.uniform(-_WINDOW_FREQUENCY_JITTER, _WINDOW_FREQUENCY_JITTER), 0.05, 1.0))
        jittered_pitch = facade.width_pitch * float(rng.uniform(1 - _WIDTH_PITCH_JITTER, 1 + _WIDTH_PITCH_JITTER))
        facade = dataclasses.replace(facade, window_frequency=jittered_frequency, width_pitch=jittered_pitch)

        min_h, max_h = preset.building_height_range
        height = rng.uniform(min_h, max_h)
        floors = max(1, round(height / preset.building_floor_height))
        floor_height = preset.building_floor_height * float(
            rng.uniform(1 - _FLOOR_HEIGHT_JITTER, 1 + _FLOOR_HEIGHT_JITTER))

        shrink = rng.uniform(0.75, 0.95)
        cx, cy = block.center
        hw = block.half_size[0] * shrink
        hh = block.half_size[1] * shrink
        footprint = [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)]
        tint = float(rng.random())

        plans.append(BuildingPlan(block.id, footprint, floors, floor_height, facade, tint))

    return plans


def build_building_meshes(plans: List[BuildingPlan], terrain_params=None, collection=None):
    """Extrude each BuildingPlan's footprint into a mesh object, plus an
    optional rooftop unit and a furnished ground-floor interior.

    Returns (building_objects, extra_objects): `building_objects` are the
    actual building-shell meshes only (every one has the same 2 material
    slots, safe for callers like ui/operators.py's _assign_city_material to
    bulk-assign into slot 0); `extra_objects` are the rooftop units, interior
    floor/ceiling slabs, interior lights, and furniture Empties - each of
    those already carries its own material (or, for lights/Empties, none at
    all), so mixing them into `building_objects` would break any caller that
    assumes every entry there is a building mesh."""
    from .. import terrain as terrain_gen
    from ...assets import factory

    building_objects = []
    extra_objects = []
    for index, plan in enumerate(plans):
        base_z = 0.0
        cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
        cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
        if terrain_params is not None:
            base_z = terrain_gen.sample_world_height(cx, cy, terrain_params)

        obj = _build_single_building(f"{BUILDINGS_PREFIX}_{index}", plan, base_z)
        obj["procgen_maps_facade_type"] = plan.facade.key
        obj["procgen_maps_material_index"] = plan.facade.material_index
        obj["procgen_maps_tint"] = plan.tint
        obj["procgen_maps_block_id"] = plan.block_id
        if collection is not None:
            collection.objects.link(obj)
        building_objects.append(obj)

        rng = random.Random(hash((plan.block_id, "detail")) & 0xFFFFFFFF)
        if plan.facade.roof_style == "flat" and plan.floors >= 2 and rng.random() < _ROOFTOP_UNIT_CHANCE:
            roof_z = base_z + plan.floors * plan.floor_height
            unit = factory.spawn("rooftop_unit", (cx + rng.uniform(-1.0, 1.0), cy + rng.uniform(-1.0, 1.0), roof_z),
                                  rotation_z=rng.uniform(0, math.tau), collection=collection)
            extra_objects.append(unit)

        extra_objects.extend(_build_ground_floor_interior(plan, base_z, index, collection))

    return building_objects, extra_objects


def _build_single_building(name, plan: BuildingPlan, base_z: float):
    import bmesh
    import bpy

    bm = bmesh.new()
    base_verts = [bm.verts.new((x, y, base_z)) for (x, y) in plan.footprint]
    base_face = bm.faces.new(base_verts)
    bm.faces.ensure_lookup_table()

    top_face = base_face
    for floor_index in range(plan.floors):
        # extrude_face_region's own "geom" return only ever reports the
        # single moved top face, never the newly created side faces (a real
        # bmesh quirk, confirmed empirically) - a before/after set diff over
        # bm.faces is the only reliable way to get all 5 new faces (4 sides
        # + 1 top) per floor.
        faces_before = set(bm.faces)
        extruded = bmesh.ops.extrude_face_region(bm, geom=[top_face])
        new_verts = [g for g in extruded["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=new_verts, vec=(0.0, 0.0, plan.floor_height))
        bm.faces.ensure_lookup_table()
        new_faces = list(set(bm.faces) - faces_before)

        # Picking by largest area (not just "first match") matters here:
        # `new_faces` comes from an unordered set diff, and a small window
        # inset/frame fragment can incidentally also have normal.z > 0.9,
        # so the *only* reliable way to find the true roof cap - by far the
        # largest near-horizontal face - is by area, not list order.
        top_candidates = [f for f in new_faces if abs(f.normal.z) > 0.9]
        top_face = max(top_candidates, key=lambda f: f.calc_area()) if top_candidates else new_faces[-1]

        if floor_index == 0:
            side_faces = [f for f in new_faces if abs(f.normal.z) < 0.1]
            entrance_face = min(side_faces, key=lambda f: f.calc_center_median().y) if side_faces else None
            for face in side_faces:
                if face is entrance_face:
                    _add_entrance(bm, face)
                elif plan.facade.window_frequency > 0:
                    _add_window_row(bm, face, plan.facade)
            continue

        if plan.facade.window_frequency > 0:
            side_faces = [f for f in new_faces if abs(f.normal.z) < 0.1]
            for face in side_faces:
                _add_window_row(bm, face, plan.facade)

    if plan.facade.roof_style != "flat":
        bm.faces.ensure_lookup_table()
        _build_gable_roof(bm, top_face, plan.footprint)

    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    # Slot 0 (wall) is left empty here - ui/operators.py assigns the shared
    # city facade material into it after building every object, so material
    # count stays flat regardless of city size. Slot 1 (window panes, tagged
    # via face.material_index = 1 in _add_window_row) is filled immediately
    # since every building shares the exact same window material too.
    from ...materials import city_mat
    mesh.materials.append(None)
    mesh.materials.append(city_mat.get_or_create_window_material())

    return bpy.data.objects.new(name, mesh)


def _horizontal_edges(face):
    """The pair of a floor side-face's edges that run along the building
    perimeter (both endpoints at the same Z) - subdividing these adds
    vertical column cuts across the face, as opposed to the two edges that
    run vertically between floors."""
    return [edge for edge in face.edges if abs(edge.verts[0].co.z - edge.verts[1].co.z) < 1e-4]


def _add_entrance(bm, face):
    """Ground-floor entrance: one wide recessed glass frontage instead of the
    regular window grid, on the side face facing -Y (an arbitrary but
    consistent "front" convention, since axis-aligned footprints have no
    inherent facing direction)."""
    import bmesh

    thickness = min(0.5, face.calc_area() ** 0.5 * 0.18)
    bmesh.ops.inset_individual(bm, faces=[face], thickness=thickness, depth=-0.15)
    face.material_index = 1


def _add_window_row(bm, face, facade: "FacadeType"):
    """Split one floor's side face into window-pitch-wide columns and punch
    a recessed window into a random subset of them (frequency-controlled),
    instead of insetting the whole face as one big panel."""
    import bmesh

    horizontal = _horizontal_edges(face)
    if len(horizontal) != 2:
        return
    face_width = horizontal[0].calc_length()
    columns = max(1, round(face_width / facade.width_pitch))

    if columns > 1:
        # bmesh.ops.subdivide_edges puts the newly created column faces in
        # "geom" (all new geometry), not "geom_inner" (which is empty here -
        # that key holds strictly-interior geometry with no boundary edges).
        result = bmesh.ops.subdivide_edges(bm, edges=horizontal, cuts=columns - 1, use_grid_fill=True)
        column_faces = [g for g in result["geom"] if isinstance(g, bmesh.types.BMFace)]
    else:
        column_faces = [face]

    for column_face in column_faces:
        if random.random() > facade.window_frequency:
            continue
        thickness = min(0.35, column_face.calc_area() ** 0.5 * 0.25)
        # inset_individual keeps `column_face` itself as the recessed inner
        # pane (only the 4 new surrounding frame faces come back in
        # result["faces"]) - tag that surviving reference as glass so the
        # window actually reads as a distinct material, not just a subtle
        # depth cue in the same wall color.
        bmesh.ops.inset_individual(bm, faces=[column_face], thickness=thickness, depth=-0.12)
        column_face.material_index = 1


def _build_gable_roof(bm, top_face, footprint):
    """Replace a flat rectangular top face with a proper gable: a ridge
    line raised along the longer axis, two sloped rectangular faces, and
    two triangular gable-end faces - instead of a single raised apex point.

    `top_face` may have picked up extra collinear vertices along its
    boundary (its edges are shared with the top floor's side faces, which
    `_add_window_row` subdivides for window columns) - so its 4 real
    corners are found by matching against the known footprint corners
    rather than assuming `len(top_face.verts) == 4`."""
    corner_verts = []
    for (fx, fy) in footprint:
        corner_verts.append(min(top_face.verts, key=lambda v: (v.co.x - fx) ** 2 + (v.co.y - fy) ** 2))
    v0, v1, v2, v3 = corner_verts
    if (v1.co - v0.co).length < (v2.co - v1.co).length:
        v0, v1, v2, v3 = v1, v2, v3, v0

    # Pitch the ridge relative to the (now-confirmed) short span rather than
    # a fixed height, so the roof reads as a clearly gabled shape (~40
    # degree pitch) regardless of building footprint size - a fixed height
    # tied only to floor_height looked nearly flat on wider buildings.
    short_span = (v2.co - v1.co).length
    ridge_height = short_span * 0.42

    ridge_a_co = (v1.co + v2.co) / 2.0
    ridge_b_co = (v3.co + v0.co) / 2.0
    ridge_a_co.z += ridge_height
    ridge_b_co.z += ridge_height
    ridge_a = bm.verts.new(ridge_a_co)
    ridge_b = bm.verts.new(ridge_b_co)

    bm.faces.remove(top_face)
    bm.faces.new((v0, v1, ridge_a, ridge_b))
    bm.faces.new((v2, v3, ridge_b, ridge_a))
    bm.faces.new((v1, v2, ridge_a))
    bm.faces.new((v3, v0, ridge_b))


_INTERIOR_WALL_MARGIN = 0.3     # meters inset from the exterior shell
_INTERIOR_FURNITURE_SLOTS = ((-0.5, -0.5), (0.5, 0.5), (0.5, -0.5), (-0.5, 0.5))


def _build_ground_floor_interior(plan: BuildingPlan, base_z: float, index: int, collection):
    """Build a simple furnished ground-floor room: a floor slab, a ceiling
    slab, a warm interior point light, and 2-3 facade-appropriate furniture
    pieces (assets/library.py's FURNITURE_BY_FACADE) - visible from outside
    through the transmissive window/entrance glass. Upper floors are left as
    shell-only for performance (a real ceiling-to-ceiling interior per floor
    on a 30-story Metropole tower would add tens of thousands of objects for
    almost no visible payoff at normal camera distances)."""
    import bpy

    from ...assets import factory, library
    from ...materials import prop_mat

    min_x = min(p[0] for p in plan.footprint)
    max_x = max(p[0] for p in plan.footprint)
    min_y = min(p[1] for p in plan.footprint)
    max_y = max(p[1] for p in plan.footprint)
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    half_w = (max_x - min_x) / 2.0 - _INTERIOR_WALL_MARGIN
    half_h = (max_y - min_y) / 2.0 - _INTERIOR_WALL_MARGIN
    if half_w <= 0.6 or half_h <= 0.6:
        return []

    floor_z = base_z + 0.05
    ceiling_z = base_z + plan.floor_height - 0.15

    floor_obj = _build_flat_quad(f"{BUILDINGS_PREFIX}_{index}_InteriorFloor", cx, cy, half_w, half_h, floor_z,
                                  prop_mat.get_or_create_prop_material("interior_floor"))
    ceiling_obj = _build_flat_quad(f"{BUILDINGS_PREFIX}_{index}_InteriorCeiling", cx, cy, half_w, half_h, ceiling_z,
                                   prop_mat.get_or_create_prop_material("interior_ceiling"))

    light_data = bpy.data.lights.new(f"{BUILDINGS_PREFIX}_{index}_InteriorLight", type='POINT')
    light_data.energy = 20.0
    light_data.color = (1.0, 0.92, 0.75)
    light_obj = bpy.data.objects.new(f"{BUILDINGS_PREFIX}_{index}_InteriorLight", light_data)
    light_obj.location = (cx, cy, base_z + plan.floor_height * 0.7)

    created = [floor_obj, ceiling_obj, light_obj]
    if collection is not None:
        for obj in created:
            collection.objects.link(obj)

    rng = random.Random(hash((plan.block_id, "furniture")) & 0xFFFFFFFF)
    furniture_ids = library.FURNITURE_BY_FACADE.get(plan.facade.key, [])
    for slot_index, asset_id in enumerate(furniture_ids):
        slot_x, slot_y = _INTERIOR_FURNITURE_SLOTS[slot_index % len(_INTERIOR_FURNITURE_SLOTS)]
        x = cx + slot_x * half_w * 0.6
        y = cy + slot_y * half_h * 0.6
        empty = factory.spawn(asset_id, (x, y, floor_z), rotation_z=rng.uniform(0, math.tau), collection=collection)
        created.append(empty)

    return created


def _build_flat_quad(name, cx, cy, half_w, half_h, z, material):
    import bpy

    verts = [(cx - half_w, cy - half_h, z), (cx + half_w, cy - half_h, z),
             (cx + half_w, cy + half_h, z), (cx - half_w, cy + half_h, z)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update(calc_edges=True)
    mesh.materials.append(material)
    return bpy.data.objects.new(name, mesh)
