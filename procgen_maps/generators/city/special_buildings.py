"""Special buildings: supermarket, police station, hospital, fire station,
and school - unique, zone-targeted building types placed by explicit
selection logic (exactly N per city, on suitable blocks) rather than the
random per-block facade pick generators.city.buildings.plan_buildings uses
for the 12 generic facades.

Reuses buildings.BuildingPlan/FacadeType directly (identical shape, so the
exact same mesh construction applies): each special type is just a
FacadeType with its own material_index (extended range 12-16 in
materials/city_mat.py's facade color ramp) and a fixed floor count/height
instead of one randomly rolled per building.
`build_special_building_meshes` calls buildings.build_building_meshes for
the shared shell/window/roof/interior construction, then adds an
illuminated sign (a real Blender Text object, not a texture) and, for
hospitals, a rooftop helipad marking.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import numpy as np

from . import buildings
from .buildings import BuildingPlan, FacadeType
from .layout import Block

SPECIAL_BUILDINGS_PREFIX = "ProcgenMaps_Special"

_FOOTPRINT_SHRINK_CAP = 0.97  # never quite touch the block's own edge


@dataclass(frozen=True)
class SpecialBuildingSpec:
    facade: FacadeType
    sign_text: str
    sign_color: Tuple[float, float, float, float]
    floors: int
    floor_height: float
    footprint_shrink: float    # fraction of block half_size used as building half_size
    min_blocks_required: int   # skip this type entirely in cities smaller than this
    count_formula: str         # 'one' | 'scaled'


SPECIAL_BUILDING_SPECS: Tuple[SpecialBuildingSpec, ...] = (
    SpecialBuildingSpec(
        facade=FacadeType("supermarket", ("commercial",), 0.35, 4.0, "flat", 12),
        sign_text="SUPERMARKET", sign_color=(1.0, 0.55, 0.05, 1.0),
        floors=1, floor_height=5.5, footprint_shrink=0.92, min_blocks_required=6, count_formula="scaled",
    ),
    SpecialBuildingSpec(
        facade=FacadeType("police_station", ("commercial", "residential"), 0.5, 3.2, "flat", 13),
        sign_text="POLICE", sign_color=(0.1, 0.3, 0.9, 1.0),
        floors=2, floor_height=3.4, footprint_shrink=0.85, min_blocks_required=10, count_formula="one",
    ),
    SpecialBuildingSpec(
        facade=FacadeType("hospital", ("commercial", "residential"), 0.6, 3.0, "flat", 14),
        sign_text="HOSPITAL", sign_color=(0.9, 0.1, 0.1, 1.0),
        floors=5, floor_height=3.4, footprint_shrink=0.9, min_blocks_required=15, count_formula="one",
    ),
    SpecialBuildingSpec(
        facade=FacadeType("fire_station", ("commercial", "industrial"), 0.3, 3.5, "flat", 15),
        sign_text="FIRE STATION", sign_color=(0.95, 0.15, 0.1, 1.0),
        floors=1, floor_height=5.0, footprint_shrink=0.88, min_blocks_required=18, count_formula="one",
    ),
    SpecialBuildingSpec(
        facade=FacadeType("school", ("residential", "commercial"), 0.55, 3.2, "flat", 16),
        sign_text="SCHOOL", sign_color=(0.95, 0.75, 0.15, 1.0),
        floors=2, floor_height=3.2, footprint_shrink=0.9, min_blocks_required=15, count_formula="one",
    ),
)

SPECIAL_SPEC_BY_KEY: Dict[str, SpecialBuildingSpec] = {spec.facade.key: spec for spec in SPECIAL_BUILDING_SPECS}


def plan_special_buildings(blocks: List[Block], zone_by_block: Dict[int, str], preset, seed=None
                            ) -> Tuple[List[BuildingPlan], Set[int]]:
    """Pick specific blocks for each special building type (the largest
    available block in an allowed zone, preferring ones not already claimed
    by an earlier special type), and return (plans, reserved_block_ids) -
    the latter so generators.city.buildings.plan_buildings can skip them
    when placing the 12 generic facades."""
    rng = np.random.default_rng((preset.seed if seed is None else seed) + 9973)
    plans: List[BuildingPlan] = []
    reserved: Set[int] = set()

    non_park_blocks = [b for b in blocks if zone_by_block.get(b.id) != "park"]
    if not non_park_blocks:
        return plans, reserved

    for spec in SPECIAL_BUILDING_SPECS:
        if len(blocks) < spec.min_blocks_required:
            continue
        count = 1 if spec.count_formula == "one" else max(1, len(blocks) // 45)

        candidates = [b for b in non_park_blocks
                      if b.id not in reserved and zone_by_block.get(b.id) in spec.facade.zone_pool]
        if not candidates:
            candidates = [b for b in non_park_blocks if b.id not in reserved]
        if not candidates:
            break

        candidates.sort(key=lambda b: b.area, reverse=True)
        chosen = candidates[:count]

        for block in chosen:
            reserved.add(block.id)
            shrink = min(spec.footprint_shrink, _FOOTPRINT_SHRINK_CAP)
            cx, cy = block.center
            hw = block.half_size[0] * shrink
            hh = block.half_size[1] * shrink
            footprint = [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)]
            tint = float(rng.random())
            plans.append(BuildingPlan(block.id, footprint, spec.floors, spec.floor_height, spec.facade, tint))

    return plans, reserved


def build_special_building_meshes(plans: List[BuildingPlan], terrain_params=None, collection=None):
    """Builds the shells via buildings.build_building_meshes (identical
    construction to regular buildings - same windows/roof/interior/material
    slots), then adds an illuminated sign Text object per building and a
    rooftop helipad marking for hospitals.

    Returns (building_objects, extra_objects) in the same shape as
    buildings.build_building_meshes."""
    from .. import terrain as terrain_gen

    building_objects, extra_objects = buildings.build_building_meshes(plans, terrain_params, collection=collection)

    for index, (plan, obj) in enumerate(zip(plans, building_objects)):
        spec = SPECIAL_SPEC_BY_KEY.get(plan.facade.key)
        if spec is None:
            continue
        obj["procgen_maps_special_type"] = plan.facade.key

        base_z = 0.0
        if terrain_params is not None:
            cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
            cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
            base_z = terrain_gen.sample_world_height(cx, cy, terrain_params)

        sign_obj = _build_sign(f"{SPECIAL_BUILDINGS_PREFIX}_{index}_Sign", plan, spec, base_z)
        if collection is not None:
            collection.objects.link(sign_obj)
        extra_objects.append(sign_obj)

        if plan.facade.key == "hospital":
            for helipad_part in _build_helipad(f"{SPECIAL_BUILDINGS_PREFIX}_{index}_Helipad", plan, base_z):
                if collection is not None:
                    collection.objects.link(helipad_part)
                extra_objects.append(helipad_part)

    return building_objects, extra_objects


def _build_sign(name, plan: BuildingPlan, spec: SpecialBuildingSpec, base_z: float):
    """A real Blender Text object (not a texture) mounted on the entrance
    facade (the -Y-facing side, matching buildings._add_entrance's "front"
    convention), so the building type is actually readable."""
    import bpy

    min_y = min(p[1] for p in plan.footprint)
    cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
    sign_z = base_z + spec.floor_height * 0.65

    curve = bpy.data.curves.new(name, type='FONT')
    curve.body = spec.sign_text
    curve.size = min(1.1, (max(p[0] for p in plan.footprint) - min(p[0] for p in plan.footprint)) / max(6, len(spec.sign_text)))
    curve.align_x = 'CENTER'
    curve.align_y = 'CENTER'
    curve.extrude = 0.04
    curve.materials.append(_get_or_create_sign_material(plan.facade.key, spec.sign_color))

    obj = bpy.data.objects.new(name, curve)
    obj.location = (cx, min_y - 0.2, sign_z)
    # A Font object's local +Z (front, the side you read) starts facing
    # world +Z with +Y as "up the page" - rotating 90 degrees around X
    # points +Z at world -Y (toward someone standing at the entrance
    # looking back at the building) while +Y (up-page) maps to world +Z,
    # so the text ends up upright and readable, not sideways or backwards.
    obj.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
    return obj


def _build_helipad(name, plan: BuildingPlan, base_z: float):
    """A circular landing pad with a flat 'H' marking on the hospital's
    roof - built the same way as generators/city/streets.py's intersection
    fans (a vertex-fan disc)."""
    import bpy

    cx = sum(p[0] for p in plan.footprint) / len(plan.footprint)
    cy = sum(p[1] for p in plan.footprint) / len(plan.footprint)
    roof_z = base_z + plan.floors * plan.floor_height + 0.05
    span_x = max(p[0] for p in plan.footprint) - min(p[0] for p in plan.footprint)
    span_y = max(p[1] for p in plan.footprint) - min(p[1] for p in plan.footprint)
    radius = min(span_x, span_y) * 0.28

    sides = 16
    verts = [(cx, cy, roof_z)]
    for i in range(sides):
        angle = (2 * math.pi * i) / sides
        verts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), roof_z))
    faces = [(0, i + 1, (i + 1) % sides + 1) for i in range(sides)]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    mesh.materials.append(_get_or_create_helipad_material())
    pad_obj = bpy.data.objects.new(name, mesh)

    label_curve = bpy.data.curves.new(name + "_Label", type='FONT')
    label_curve.body = "H"
    label_curve.size = radius * 0.8
    label_curve.align_x = 'CENTER'
    label_curve.align_y = 'CENTER'
    label_curve.extrude = 0.02
    label_curve.materials.append(_get_or_create_sign_material("helipad_h", (1.0, 1.0, 1.0, 1.0)))
    label_obj = bpy.data.objects.new(name + "_Label", label_curve)
    # Lying flat facing up (+Z) is the Font object's default orientation,
    # which is already correct for a marking read from directly above.
    label_obj.location = (cx, cy, roof_z + 0.03)

    return [pad_obj, label_obj]


def _get_or_create_sign_material(key, color):
    import bpy

    name = f"ProcgenMaps_Sign_{key}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Emission Color"].default_value = color
        bsdf.inputs["Emission Strength"].default_value = 1.2
    return mat


def _get_or_create_helipad_material():
    import bpy

    name = "ProcgenMaps_Helipad"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    color = (0.85, 0.15, 0.15, 1.0)
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.6
    return mat
