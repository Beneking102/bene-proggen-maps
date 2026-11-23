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
_PARAPET_CHANCE = 0.7       # flat-roofed buildings (>= 2 floors) that get a parapet wall around a recessed roof
_PARAPET_WIDTH = 0.4        # meters, parapet wall thickness
_PARAPET_DROP = 0.5         # meters the roof deck sits below the parapet top

BUILDINGS_PREFIX = "ProcgenMaps_Building"

# Per-face float attributes on every building mesh, written for the window
# panes (materials/city_mat.py's window shader reads them in GEOMETRY mode):
# how brightly a pane glows at night (0 = dark flat) and how warm its light is.
WINDOW_LIT_ATTRIBUTE = "procgen_maps_window_lit"
WINDOW_WARM_ATTRIBUTE = "procgen_maps_window_warm"

# Material slots of a building mesh (see _build_single_building).
WALL_SLOT, WINDOW_SLOT, ROOF_SLOT, TRIM_SLOT, AWNING_SLOT = 0, 1, 2, 3, 4

_BALCONY_CHANCE = 0.75      # residential facades that get balcony ribbons
_BALCONY_DEPTH = 1.3        # meters the balcony slab protrudes
_BALCONY_SLAB = 0.18        # slab thickness
_BALCONY_RAIL_HEIGHT = 1.05
_BALCONY_END_MARGIN = 0.6   # meters kept free at both ends of a ribbon
_AWNING_DEPTH = 1.5
_AWNING_DROP = 0.35
_ROOFTOP_TANK_MIN_FLOORS = 6
_ROOFTOP_TANK_CHANCE = 0.5
_HIP_ROOF_CHANCE = 0.4      # share of townhouses/cottages with a hip instead of a gable
_SAWTOOTH_CHANCE = 0.7      # share of factory halls with a sawtooth roof


@dataclass(frozen=True)
class FacadeType:
    key: str
    zone_pool: Tuple[str, ...]     # zones allowed to use this facade
    window_frequency: float        # 0..1, chance each window bay gets a recessed window
    width_pitch: float             # meters per window bay/column
    roof_style: str                # 'flat' | 'peaked'
    material_index: int
    lighting: str = "office"       # night-window pattern: 'residential' | 'office' | 'industrial'
    balconies: bool = False        # balcony ribbons on the upper floors (front/back)
    shopfront: bool = False        # awning + sign board over the ground-floor entrance


FACADE_TYPES: Tuple[FacadeType, ...] = (
    FacadeType("glass_tower", ("commercial",), 0.9, 2.5, "flat", 0, "office"),
    FacadeType("office_block", ("commercial",), 0.7, 3.0, "flat", 1, "office"),
    FacadeType("brick_commercial", ("commercial", "residential"), 0.6, 3.5, "flat", 2, "office", False, True),
    FacadeType("apartment_slab", ("residential",), 0.55, 3.2, "flat", 3, "residential", True),
    FacadeType("apartment_tower", ("residential",), 0.5, 3.0, "flat", 4, "residential", True),
    FacadeType("townhouse", ("residential",), 0.4, 3.5, "peaked", 5, "residential"),
    FacadeType("cottage", ("residential",), 0.3, 3.0, "peaked", 6, "residential"),
    FacadeType("shopfront", ("commercial",), 0.8, 2.8, "flat", 7, "office", False, True),
    FacadeType("warehouse", ("industrial",), 0.2, 5.0, "flat", 8, "industrial"),
    FacadeType("factory_hall", ("industrial",), 0.15, 6.0, "peaked", 9, "industrial"),
    FacadeType("industrial_tower", ("industrial",), 0.25, 4.0, "flat", 10, "industrial"),
    FacadeType("mixed_use", ("commercial", "residential"), 0.65, 3.2, "peaked", 11, "residential", True, True),
)


# --- night-window planning (pure Python, unit-tested) ------------------------

_DARK_FLOOR_CHANCE = {"residential": 0.15, "office": 0.3, "industrial": 0.65}
_OFFICE_BAND_CHANCE = 0.55          # lit office floors that are lit wall to wall
_APARTMENT_LIT_CHANCE = 0.55        # apartments (2-3 bays) with the lights on
_APARTMENT_WARM_CHANCE = 0.85       # ... with warm (incandescent) rather than cool light


def plan_floor_lighting(rng: random.Random, lighting: str) -> str:
    """One state per floor: 'dark' (nobody home / unused floor), 'band'
    (every window lit - a working office floor), or 'mixed' (lit and dark
    apartments / scattered offices). Drawn once per floor so the pattern
    is coherent across all four sides instead of per-window noise."""
    if rng.random() < _DARK_FLOOR_CHANCE.get(lighting, 0.3):
        return "dark"
    if lighting == "office" and rng.random() < _OFFICE_BAND_CHANCE:
        return "band"
    if lighting == "industrial":
        return "band" if rng.random() < 0.5 else "dark"
    return "mixed"


def window_lighting(rng: random.Random, columns: int, lighting: str, floor_state: str) -> List[Tuple[float, float]]:
    """(lit, warm) per window column of one floor face. Residential floors
    are split into apartments of 2-3 neighbouring bays that are lit or dark
    *together* (and mostly warm); office floors are lit in runs of bays
    with cool light, or wall to wall for a 'band' floor; industrial floors
    glow dimly and cool or not at all. Returns a list of `columns` pairs."""
    if columns <= 0:
        return []
    if floor_state == "dark":
        return [(0.0, 0.0)] * columns
    if lighting == "industrial":
        return [(0.45, 0.15)] * columns
    if lighting == "office":
        if floor_state == "band":
            return [(1.0, 0.2)] * columns
        result = []
        while len(result) < columns:
            run = rng.randint(2, 6)
            lit = rng.random() < 0.5
            result.extend([(1.0 if lit else 0.0, 0.25)] * run)
        return result[:columns]
    result = []
    while len(result) < columns:
        run = rng.randint(2, 3)
        lit = rng.random() < _APARTMENT_LIT_CHANCE
        warm = 1.0 if rng.random() < _APARTMENT_WARM_CHANCE else 0.0
        level = rng.uniform(0.6, 1.0) if lit else 0.0
        result.extend([(level, warm)] * run)
    return result[:columns]


@dataclass
class BuildingPlan:
    block_id: int
    footprint: List[Tuple[float, float]]
    floors: int
    floor_height: float
    facade: FacadeType
    tint: float = 0.5  # 0..1, per-building brightness variation (see materials/city_mat.py)


def plan_buildings(blocks: List[Block], zone_by_block: Dict[int, str], preset, seed=None,
                   street_graph=None) -> List[BuildingPlan]:
    """Decide which blocks get a building, how tall, and which facade type.

    With `street_graph` (a streets.StreetGraph), every footprint is also
    pulled back from the surrounding roads so a sidewalk plus a strip of
    ground fits between the asphalt and the wall - see setback.py for why
    the block-fraction shrink alone left most buildings standing in the
    road."""
    from . import setback

    rng = np.random.default_rng(preset.seed if seed is None else seed)
    plans: List[BuildingPlan] = []
    roads = setback.roads_from_graph(street_graph, preset) if street_graph is not None else []

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
        if roads:
            limit_w, limit_h = setback.shrink_footprint_for_setback(
                block.center, block.half_size, setback.roads_near(block.center, block.half_size, roads))
            hw, hh = min(hw, limit_w), min(hh, limit_h)
        footprint = [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)]
        tint = float(rng.random())

        plans.append(BuildingPlan(block.id, footprint, floors, floor_height, facade, tint))

    return plans


def detail_rng(block_id: int, purpose: str) -> random.Random:
    """A per-building, per-purpose random stream that is stable across
    processes. The previous `hash((block_id, "detail"))` looked
    deterministic but isn't: Python randomizes str hashing per interpreter
    process (PYTHONHASHSEED), so a tuple containing a string hashes
    differently on every Blender launch - the same seed produced different
    rooftop units and furniture rotations on every run. Seeding
    random.Random with a *string* instead uses its own stable SHA-512
    based seeding, which is reproducible everywhere."""
    return random.Random(f"procgen_maps:{block_id}:{purpose}")


def window_mask(rng: random.Random, columns: int, frequency: float) -> List[bool]:
    """Which of `columns` window bays on one floor face get a recessed
    window (True) - one draw per column in column order so a facade's
    pattern is a pure function of its own rng stream. Pulled out of the
    bmesh code so it is unit-testable, and seeded per building rather than
    drawn from the global `random` module as before (which made window
    layouts differ from run to run for the same seed)."""
    return [rng.random() <= frequency for _ in range(columns)]


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

        roof_rng = detail_rng(plan.block_id, "roof")
        parapet = plan.facade.roof_style == "flat" and plan.floors >= 2 and roof_rng.random() < _PARAPET_CHANCE
        roof_index = 0 if plan.facade.roof_style == "flat" else roof_rng.randint(1, 3)
        roof_shape = pick_roof_shape(roof_rng, plan.facade)

        obj = _build_single_building(f"{BUILDINGS_PREFIX}_{index}", plan, base_z, parapet=parapet,
                                     roof_shape=roof_shape)
        obj["procgen_maps_facade_type"] = plan.facade.key
        obj["procgen_maps_material_index"] = plan.facade.material_index
        obj["procgen_maps_tint"] = plan.tint
        obj["procgen_maps_block_id"] = plan.block_id
        # Read by materials/city_mat.py: ground-level grime and the floor-slab
        # lines need the building's own base height and floor pitch, and the
        # roof shader picks its gravel/tile color by roof_index.
        obj["procgen_maps_base_z"] = float(base_z)
        obj["procgen_maps_floor_height"] = float(plan.floor_height)
        # Where base_z was sampled: balconies/awnings shift the mesh's
        # bounding box, so validation.py reads this instead of the bbox.
        obj["procgen_maps_center_x"] = float(cx)
        obj["procgen_maps_center_y"] = float(cy)
        obj["procgen_maps_roof_index"] = roof_index
        if collection is not None:
            collection.objects.link(obj)
        building_objects.append(obj)

        rng = detail_rng(plan.block_id, "detail")
        if plan.facade.roof_style == "flat" and plan.floors >= 2:
            roof_z = base_z + plan.floors * plan.floor_height - (_PARAPET_DROP if parapet else 0.0)
            if rng.random() < _ROOFTOP_UNIT_CHANCE:
                unit = factory.spawn("rooftop_unit", (cx + rng.uniform(-1.0, 1.0), cy + rng.uniform(-1.0, 1.0), roof_z),
                                      rotation_z=rng.uniform(0, math.tau), collection=collection)
                extra_objects.append(unit)
            # Taller flat roofs get a water tank and/or an antenna mast in a
            # corner, so skylines stop reading as a row of plain boxes.
            if plan.floors >= _ROOFTOP_TANK_MIN_FLOORS and rng.random() < _ROOFTOP_TANK_CHANCE:
                half_w = (max(p[0] for p in plan.footprint) - min(p[0] for p in plan.footprint)) / 2.0
                half_h = (max(p[1] for p in plan.footprint) - min(p[1] for p in plan.footprint)) / 2.0
                if half_w > 4.0 and half_h > 4.0:
                    sx, sy = rng.choice((-1.0, 1.0)), rng.choice((-1.0, 1.0))
                    extra_objects.append(factory.spawn(
                        "rooftop_tank", (cx + sx * (half_w - 2.2), cy + sy * (half_h - 2.2), roof_z),
                        rotation_z=rng.uniform(0, math.tau), collection=collection))
                    if rng.random() < 0.5:
                        extra_objects.append(factory.spawn(
                            "antenna_mast", (cx - sx * (half_w - 1.5), cy - sy * (half_h - 1.5), roof_z),
                            collection=collection))

        extra_objects.extend(_build_ground_floor_interior(plan, base_z, index, collection))

    return building_objects, extra_objects


def pick_roof_shape(rng: random.Random, facade: FacadeType) -> str:
    """'flat' | 'gable' | 'hip' | 'sawtooth' for one building, drawn from the
    building's roof rng: factory halls mostly get a sawtooth, houses a hip
    roof now and then instead of the gable. Pure Python, unit-tested."""
    if facade.roof_style == "flat":
        return "flat"
    if facade.key == "factory_hall":
        return "sawtooth" if rng.random() < _SAWTOOTH_CHANCE else "gable"
    if facade.key in ("townhouse", "cottage"):
        return "hip" if rng.random() < _HIP_ROOF_CHANCE else "gable"
    return "gable"


def _build_single_building(name, plan: BuildingPlan, base_z: float, parapet: bool = False, roof_shape=None):
    import bmesh
    import bpy

    if roof_shape is None:
        roof_shape = "flat" if plan.facade.roof_style == "flat" else "gable"
    bm = bmesh.new()
    lit_layer = bm.faces.layers.float.new(WINDOW_LIT_ATTRIBUTE)
    warm_layer = bm.faces.layers.float.new(WINDOW_WARM_ATTRIBUTE)
    base_verts = [bm.verts.new((x, y, base_z)) for (x, y) in plan.footprint]
    base_face = bm.faces.new(base_verts)
    bm.faces.ensure_lookup_table()

    window_rng = detail_rng(plan.block_id, "windows")
    light_rng = detail_rng(plan.block_id, "lights")
    top_face = base_face
    for floor_index in range(plan.floors):
        floor_state = plan_floor_lighting(light_rng, plan.facade.lighting)
        # extrude_face_region's own "geom" return only ever reports the
        # single moved top face plus the new verts/edges, never the newly
        # created side faces (a real bmesh quirk, confirmed empirically).
        # Every face created by the extrude - the 4+ sides and the top cap -
        # touches at least one of the new verts, and no pre-existing face
        # does, so the new verts' link_faces are exactly the new faces.
        # (This replaced a before/after set diff over *all* of bm.faces,
        # which was O(total faces) per floor - quadratic for tall towers.)
        extruded = bmesh.ops.extrude_face_region(bm, geom=[top_face])
        new_verts = [g for g in extruded["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=new_verts, vec=(0.0, 0.0, plan.floor_height))
        # Sets iterate in memory-address order, which differs from run to
        # run - and since the window rng is drawn in face order, the same
        # seed produced different window layouts on every launch. Sort
        # geometrically so the order is reproducible.
        new_faces = sorted({face for vert in new_verts for face in vert.link_faces}, key=_face_sort_key)

        # Picking by largest area (not just "first match") matters here:
        # `new_faces` comes from an unordered set diff, and a small window
        # inset/frame fragment can incidentally also have normal.z > 0.9,
        # so the *only* reliable way to find the true roof cap - by far the
        # largest near-horizontal face - is by area, not list order.
        top_candidates = [f for f in new_faces if abs(f.normal.z) > 0.9]
        top_face = max(top_candidates, key=lambda f: f.calc_area()) if top_candidates else new_faces[-1]

        side_faces = [f for f in new_faces if abs(f.normal.z) < 0.1]
        if floor_index == 0:
            entrance_face = min(side_faces, key=lambda f: f.calc_center_median().y) if side_faces else None
            if entrance_face is not None:
                _add_entrance(bm, entrance_face, lit_layer, warm_layer)
            side_faces = [f for f in side_faces if f is not entrance_face]

        if plan.facade.window_frequency > 0:
            _carve_floor_windows(bm, side_faces, plan.facade, window_rng, light_rng, floor_state,
                                 (lit_layer, warm_layer))

    # Attachments are plain boxes added after the floors (so the extrude
    # loop's "new faces = faces touching the new verts" rule stays true).
    detail = detail_rng(plan.block_id, "attachments")
    if plan.facade.balconies and plan.floors >= 2 and detail.random() < _BALCONY_CHANCE:
        _add_balconies(bm, plan, base_z, detail)
    if plan.facade.shopfront:
        _add_shopfront(bm, plan, base_z, detail)

    if roof_shape == "sawtooth":
        bm.faces.ensure_lookup_table()
        roof_faces = _build_sawtooth_roof(bm, top_face, plan.footprint)
    elif roof_shape == "hip":
        bm.faces.ensure_lookup_table()
        roof_faces = _build_hip_roof(bm, top_face, plan.footprint)
    elif roof_shape == "gable":
        bm.faces.ensure_lookup_table()
        roof_faces = _build_gable_roof(bm, top_face, plan.footprint)
    elif parapet:
        roof_faces = _build_parapet_roof(bm, top_face)
    else:
        roof_faces = [top_face]
    for face in roof_faces:
        face.material_index = ROOF_SLOT

    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    # Slot 0 (wall) is left empty here - ui/operators.py assigns the shared
    # city facade material into it after building every object, so material
    # count stays flat regardless of city size. Slot 1 (window panes, tagged
    # via face.material_index = 1 in _carve_floor_windows), slot 2 (roof
    # faces), slot 3 (railings/trim) and slot 4 (awnings + shop signs) are
    # filled immediately since every building shares those.
    from ...materials import city_mat
    mesh.materials.append(None)
    mesh.materials.append(city_mat.get_or_create_window_material())
    mesh.materials.append(city_mat.get_or_create_roof_material())
    mesh.materials.append(city_mat.get_or_create_trim_material())
    mesh.materials.append(city_mat.get_or_create_awning_material())

    return bpy.data.objects.new(name, mesh)


def _add_box(bm, x0, x1, y0, y1, z0, z1, material_index):
    """An axis-aligned closed box from plain BMesh calls (no operator
    overhead): 8 verts, 6 faces, outward-facing."""
    v = [bm.verts.new((x, y, z)) for z in (z0, z1) for (x, y) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    faces = [
        bm.faces.new((v[0], v[3], v[2], v[1])),   # bottom
        bm.faces.new((v[4], v[5], v[6], v[7])),   # top
        bm.faces.new((v[0], v[1], v[5], v[4])),   # -y
        bm.faces.new((v[1], v[2], v[6], v[5])),   # +x
        bm.faces.new((v[2], v[3], v[7], v[6])),   # +y
        bm.faces.new((v[3], v[0], v[4], v[7])),   # -x
    ]
    for face in faces:
        face.material_index = material_index
    return faces


def _footprint_bounds(footprint):
    xs = [p[0] for p in footprint]
    ys = [p[1] for p in footprint]
    return min(xs), max(xs), min(ys), max(ys)


def _add_balconies(bm, plan: BuildingPlan, base_z: float, rng: random.Random):
    """Balcony ribbons on the upper floors: one slab plus a railing the
    whole width of the face, on the front (-Y) face and, for some
    buildings, the back as well; roughly every floor or every other one.
    Two boxes per floor and side - cheap, and a slab tower reads as a
    residential block from across the street instead of an office."""
    min_x, max_x, min_y, max_y = _footprint_bounds(plan.footprint)
    if max_x - min_x < 2.0 * _BALCONY_END_MARGIN + 2.0:
        return
    sides = [-1.0] if rng.random() < 0.5 else [-1.0, 1.0]
    every = 1 if rng.random() < 0.6 else 2
    x0, x1 = min_x + _BALCONY_END_MARGIN, max_x - _BALCONY_END_MARGIN
    for floor_index in range(1, plan.floors, every):
        z = base_z + floor_index * plan.floor_height
        for side in sides:
            wall_y = min_y if side < 0 else max_y
            y_out = wall_y + side * _BALCONY_DEPTH
            y_lo, y_hi = (y_out, wall_y) if side < 0 else (wall_y, y_out)
            _add_box(bm, x0, x1, y_lo, y_hi, z - _BALCONY_SLAB, z, WALL_SLOT)
            # A light, slim railing: a thin panel to two thirds of the
            # height plus a top rail (v0.13's full-height dark box read as
            # a black band across the facade at night).
            rail_lo, rail_hi = (y_out, y_out + 0.05) if side < 0 else (y_out - 0.05, y_out)
            _add_box(bm, x0, x1, rail_lo, rail_hi, z, z + _BALCONY_RAIL_HEIGHT * 0.66, TRIM_SLOT)
            _add_box(bm, x0, x1, rail_lo - 0.02, rail_hi + 0.02, z + _BALCONY_RAIL_HEIGHT - 0.06,
                     z + _BALCONY_RAIL_HEIGHT, TRIM_SLOT)
            # Short side rails close the ribbon's ends.
            for ex0, ex1 in ((x0, x0 + 0.05), (x1 - 0.05, x1)):
                _add_box(bm, ex0, ex1, y_lo, y_hi, z, z + _BALCONY_RAIL_HEIGHT * 0.66, TRIM_SLOT)


def _add_shopfront(bm, plan: BuildingPlan, base_z: float, rng: random.Random):
    """Ground-floor shop frontage over the entrance (-Y) face: a sloped
    awning box that sticks out over the sidewalk and a sign board above
    it, both in the per-building awning color (materials/city_mat.py)."""
    min_x, max_x, min_y, _max_y = _footprint_bounds(plan.footprint)
    width = max_x - min_x
    if width < 4.0:
        return
    margin = max(0.5, width * 0.12)
    x0, x1 = min_x + margin, max_x - margin
    top = base_z + plan.floor_height * 0.72
    outer = min_y - _AWNING_DEPTH
    # Awning: a thin slab, lower at its outer edge than at the wall.
    v = [bm.verts.new(co) for co in (
        (x0, min_y, top), (x1, min_y, top), (x1, outer, top - _AWNING_DROP), (x0, outer, top - _AWNING_DROP),
        (x0, min_y, top - 0.08), (x1, min_y, top - 0.08), (x1, outer, top - _AWNING_DROP - 0.08),
        (x0, outer, top - _AWNING_DROP - 0.08))]
    for indices in ((0, 1, 2, 3), (7, 6, 5, 4), (0, 3, 7, 4), (1, 5, 6, 2), (3, 2, 6, 7), (0, 4, 5, 1)):
        bm.faces.new([v[i] for i in indices]).material_index = AWNING_SLOT
    # Sign board: a shallow box on the wall above the awning.
    sign_w = (x1 - x0) * rng.uniform(0.45, 0.8)
    sign_x0 = (x0 + x1) / 2.0 - sign_w / 2.0
    sign_z0 = top + 0.15
    sign_z1 = min(base_z + plan.floor_height - 0.12, sign_z0 + 0.6)
    if sign_z1 > sign_z0 + 0.2:
        _add_box(bm, sign_x0, sign_x0 + sign_w, min_y - 0.12, min_y + 0.02, sign_z0, sign_z1, AWNING_SLOT)


def _build_parapet_roof(bm, top_face):
    """Turn a flat roof cap into a recessed deck surrounded by a parapet
    wall: inset the cap by the parapet thickness (the ring stays at roof
    height as the parapet's top), extrude the inner face and drop it by
    _PARAPET_DROP, which creates the vertical inner parapet faces. Returns
    the roof-deck face (the only face that gets the roof material; the
    parapet top and its inner faces keep the wall material)."""
    import bmesh

    bmesh.ops.inset_individual(bm, faces=[top_face], thickness=_PARAPET_WIDTH, depth=0.0)
    result = bmesh.ops.extrude_discrete_faces(bm, faces=[top_face])
    deck = result["faces"][0]
    bmesh.ops.translate(bm, verts=list(deck.verts), vec=(0.0, 0.0, -_PARAPET_DROP))
    return [deck]


def _face_sort_key(face):
    center = face.calc_center_median()
    return (round(center.x, 4), round(center.y, 4), round(center.z, 4))


def _horizontal_edges(face):
    """The pair of a floor side-face's edges that run along the building
    perimeter (both endpoints at the same Z) - subdividing these adds
    vertical column cuts across the face, as opposed to the two edges that
    run vertically between floors."""
    return [edge for edge in face.edges if abs(edge.verts[0].co.z - edge.verts[1].co.z) < 1e-4]


def _add_entrance(bm, face, lit_layer=None, warm_layer=None):
    """Ground-floor entrance: one wide recessed glass frontage instead of the
    regular window grid, on the side face facing -Y (an arbitrary but
    consistent "front" convention, since axis-aligned footprints have no
    inherent facing direction). Always lit warm at night (a lobby/shop)."""
    import bmesh

    thickness = min(0.5, face.calc_area() ** 0.5 * 0.18)
    bmesh.ops.inset_individual(bm, faces=[face], thickness=thickness, depth=-0.15)
    face.material_index = WINDOW_SLOT
    if lit_layer is not None:
        face[lit_layer] = 0.8
        face[warm_layer] = 1.0


_WINDOW_INSET_DEPTH = -0.12


def _split_face_into_columns(face, columns: int):
    """Split a vertical quad side face into `columns` equal vertical strips,
    returned in order along the face's bottom edge.

    Uses bmesh.utils.edge_split / face_split (plain BMesh API calls with no
    operator overhead) rather than bmesh.ops.subdivide_edges, which this
    used before: subdivide_edges(use_grid_fill=True) on just the two
    horizontal edges of a quad does *not* grid-fill the quad (a face is
    only filled when all of its edges are selected) - it inserted the
    verts on the edges and returned, as "new faces", the neighbouring
    faces that gained a vertex: the building's base face, the roof cap,
    and the side face itself, unsplit. Downstream that meant one window
    per ground-floor side, random nested insets on the roof tagged with
    the window-glass material (every flat roof rendered as dark glass),
    and ~70k single-face bmesh.ops calls per Metropole.

    edge_split(edge, v, fac) places the new vert `fac` along from `v` and
    returns (new_edge, new_vert) with `new_edge` the piece attached to
    `v`; cutting from the far end inward keeps the `v`-side piece as the
    edge to cut next. face_split(face, a, b) returns the half containing
    the loop that starts at `a`... in practice the piece on the `v` side,
    so the strips are collected and sorted geometrically to be safe."""
    import bmesh

    bottom, top = sorted(_horizontal_edges(face), key=lambda e: e.verts[0].co.z)
    b0, b1 = bottom.verts
    t0, t1 = top.verts
    if (t0.co.xy - b0.co.xy).length > (t1.co.xy - b0.co.xy).length:
        t0, t1 = t1, t0
    direction = (b1.co - b0.co).normalized()
    origin = b0.co.copy()

    bottom_cuts = []
    top_cuts = []
    bottom_edge, top_edge = bottom, top
    for k in range(columns - 1, 0, -1):
        fac = k / (k + 1)
        bottom_edge, vert = bmesh.utils.edge_split(bottom_edge, b0, fac)
        bottom_cuts.append(vert)
        top_edge, vert = bmesh.utils.edge_split(top_edge, t0, fac)
        top_cuts.append(vert)

    strips = [face]
    current = face
    for vb, vt in zip(bottom_cuts, top_cuts):
        new_face, _loop = bmesh.utils.face_split(current, vb, vt)
        strips.append(new_face)
        # Continue with whichever half still contains b0 (the un-cut remainder).
        current = new_face if b0 in new_face.verts else current
    return sorted(strips, key=lambda f: (f.calc_center_median() - origin).dot(direction))


def _plan_window_row(bm, face, facade: "FacadeType", rng: random.Random):
    """Split one floor's side face into window-pitch-wide columns and decide
    which columns get a recessed window (frequency-controlled, drawn from
    the building's own `rng` - see window_mask). Returns the chosen column
    faces; the actual inset is done by _carve_floor_windows so a whole
    floor's windows go through as few bmesh.ops calls as possible."""
    horizontal = _horizontal_edges(face)
    if len(horizontal) != 2:
        return [], []
    face_width = horizontal[0].calc_length()
    columns = max(1, round(face_width / facade.width_pitch))
    column_faces = _split_face_into_columns(face, columns) if columns > 1 else [face]
    return column_faces, window_mask(rng, len(column_faces), facade.window_frequency)


def _carve_floor_windows(bm, side_faces, facade: "FacadeType", rng: random.Random, light_rng=None,
                         floor_state="mixed", layers=None):
    """Window-carve every side face of one floor.

    Every bmesh.ops call costs a fixed overhead proportional to the mesh's
    current element count (operator slot setup + flag clearing), so one
    inset_individual call *per window* was the single biggest cost in city
    generation (~24 s of a 32 s Metropole build). inset_individual treats
    each face independently, so all windows of a floor that share an inset
    thickness (every column of one side has the same area) are carved in
    one call. Batching across *floors* would change the result: the next
    floor's column split adds a vertex to this floor's not-yet-inset faces'
    top edges. The window rng is drawn face by face, in sorted face order,
    before any inset happens.

    With `light_rng`/`layers`, every pane also gets its night-light values
    (window_lighting: lit apartments / office bands per face, the floor's
    `floor_state` shared by all four sides) written to the per-face
    attribute layers that the window shader reads."""
    import bmesh

    pending = {}
    lit_values = {}
    for face in side_faces:
        column_faces, mask = _plan_window_row(bm, face, facade, rng)
        lighting = (window_lighting(light_rng, len(column_faces), facade.lighting, floor_state)
                    if light_rng is not None else None)
        for column_index, (column_face, has_window) in enumerate(zip(column_faces, mask)):
            if not has_window:
                continue
            thickness = min(0.35, column_face.calc_area() ** 0.5 * 0.25)
            pending.setdefault(round(thickness, 6), []).append(column_face)
            if lighting is not None:
                lit_values[column_face] = lighting[column_index]

    for thickness, faces in pending.items():
        # inset_individual keeps each input face itself as the recessed
        # inner pane (only the new surrounding frame faces come back in
        # result["faces"]) - tag those surviving references as glass so
        # the window reads as a distinct material, not just a depth cue.
        bmesh.ops.inset_individual(bm, faces=faces, thickness=thickness, depth=_WINDOW_INSET_DEPTH)
        for face in faces:
            face.material_index = WINDOW_SLOT
            if layers is not None and face in lit_values:
                lit, warm = lit_values[face]
                face[layers[0]] = lit
                face[layers[1]] = warm


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
    return [
        bm.faces.new((v0, v1, ridge_a, ridge_b)),
        bm.faces.new((v2, v3, ridge_b, ridge_a)),
        bm.faces.new((v1, v2, ridge_a)),
        bm.faces.new((v3, v0, ridge_b)),
    ]


def _roof_corners(top_face, footprint):
    """The top face's 4 real corner verts (see _build_gable_roof for why it
    cannot assume len(verts) == 4), ordered so v0->v1 is a long side."""
    corner_verts = []
    for (fx, fy) in footprint:
        corner_verts.append(min(top_face.verts, key=lambda v: (v.co.x - fx) ** 2 + (v.co.y - fy) ** 2))
    v0, v1, v2, v3 = corner_verts
    if (v1.co - v0.co).length < (v2.co - v1.co).length:
        v0, v1, v2, v3 = v1, v2, v3, v0
    return v0, v1, v2, v3


def _build_hip_roof(bm, top_face, footprint):
    """A hip roof: like the gable, but the ridge is shortened by the short
    span's half on both ends so all four sides slope - two trapezoids
    along the long sides, two triangles at the ends."""
    v0, v1, v2, v3 = _roof_corners(top_face, footprint)
    short_span = (v2.co - v1.co).length
    long_span = (v1.co - v0.co).length
    ridge_height = short_span * 0.42
    inset = min(short_span * 0.5, long_span * 0.45)
    along = (v1.co - v0.co).normalized()
    ridge_a_co = (v1.co + v2.co) / 2.0 - along * inset
    ridge_b_co = (v3.co + v0.co) / 2.0 + along * inset
    ridge_a_co.z += ridge_height
    ridge_b_co.z += ridge_height
    ridge_a = bm.verts.new(ridge_a_co)
    ridge_b = bm.verts.new(ridge_b_co)
    bm.faces.remove(top_face)
    return [
        bm.faces.new((v0, v1, ridge_a, ridge_b)),
        bm.faces.new((v2, v3, ridge_b, ridge_a)),
        bm.faces.new((v1, v2, ridge_a)),
        bm.faces.new((v3, v0, ridge_b)),
    ]


def _build_sawtooth_roof(bm, top_face, footprint, tooth_width=6.0):
    """A factory sawtooth roof: N teeth along the long axis, each a slope
    rising along the axis and a vertical (north-light) face dropping back
    down. Built from the 4 corner verts; the extra collinear verts the
    column splits left on the top edges stay on the wall faces."""
    v0, v1, v2, v3 = _roof_corners(top_face, footprint)
    long_span = (v1.co - v0.co).length
    teeth = max(1, int(round(long_span / tooth_width)))
    if teeth < 2:
        return _build_gable_roof(bm, top_face, footprint)
    along = (v1.co - v0.co) / teeth
    height = min(2.4, (v2.co - v1.co).length * 0.25)
    base_z = v0.co.z
    rows = []   # per tooth boundary: (near vert, far vert) at the eave height
    for k in range(teeth + 1):
        if k == 0:
            rows.append((v0, v3))
        elif k == teeth:
            rows.append((v1, v2))
        else:
            near = bm.verts.new(v0.co + along * k)
            far = bm.verts.new(v3.co + along * k)
            rows.append((near, far))
    bm.faces.remove(top_face)
    faces = []
    for k in range(teeth):
        (n0, f0), (n1, f1) = rows[k], rows[k + 1]
        peak_n = bm.verts.new((n1.co.x, n1.co.y, base_z + height))
        peak_f = bm.verts.new((f1.co.x, f1.co.y, base_z + height))
        faces.append(bm.faces.new((n0, n1, peak_n)))           # near-side triangle (wall)
        faces.append(bm.faces.new((f1, f0, peak_f)))           # far-side triangle (wall)
        faces[-1].material_index = WALL_SLOT
        faces[-2].material_index = WALL_SLOT
        slope = bm.faces.new((n0, peak_n, peak_f, f0))         # the sloped roof pane
        glazing = bm.faces.new((n1, f1, peak_f, peak_n))       # vertical north-light glazing
        glazing.material_index = WINDOW_SLOT
        faces.append(glazing)
        faces.append(slope)
    # Only the slopes get the roof material; the caller tags what we return.
    return [face for face in faces if face.material_index == 0 and len(face.verts) == 4]


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

    rng = detail_rng(plan.block_id, "furniture")
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
