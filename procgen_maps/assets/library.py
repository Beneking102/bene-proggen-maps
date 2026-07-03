"""Placeholder procedural asset catalog: 12 tree variants, a street lamp, a
bench, a parked car, a sign, a rooftop utility unit, and interior furniture
(bed, table, chair, shelf, desk, counter, crate, machinery).

Each entry is a primitive-based mesh builder (cones/spheres/boxes assembled
in assets/factory.py) exposed behind the exact same `factory.spawn()` API a
future bundled `.blend` asset library would use - swapping placeholders for
real modeled assets later requires no generator-code changes. See
ARCHITECTURE.md for the rationale (real 3D modeling is art-content work, out
of scope for this codebase).
"""
from dataclasses import dataclass
from typing import Callable, Dict, List

LOD_LEVELS = ("high", "medium", "low")

_SEGMENTS_BY_DETAIL = {"high": 10, "medium": 6, "low": 4}


@dataclass(frozen=True)
class AssetDef:
    asset_id: str
    category: str            # 'tree' | 'lamp' | 'bench' | 'car' | 'sign' | 'rooftop' | 'furniture'
    footprint_radius: float  # meters, used for collision placement
    builder: Callable        # (detail: str) -> dict of primitive params (see assets/factory.py)


def _tree_builder(trunk_radius, canopy_radius, height, canopy_shape):
    def builder(detail: str):
        return {
            "kind": "tree",
            "trunk_radius": trunk_radius,
            "canopy_radius": canopy_radius,
            "height": height,
            "canopy_shape": canopy_shape,
            "segments": _SEGMENTS_BY_DETAIL[detail],
        }
    return builder


def _lamp_builder(detail: str):
    return {"kind": "lamp", "pole_radius": 0.06, "height": 4.0, "head_radius": 0.25,
            "segments": _SEGMENTS_BY_DETAIL[detail]}


def _bench_builder(detail: str):
    return {"kind": "bench", "width": 1.4, "depth": 0.5, "height": 0.45, "detailed": detail == "high"}


def _car_builder(detail: str):
    return {"kind": "car", "length": 4.3, "width": 1.8, "height": 1.4, "segments": _SEGMENTS_BY_DETAIL[detail]}


def _sign_builder(detail: str):
    return {"kind": "sign", "pole_height": 2.2, "board_width": 0.8, "board_height": 0.5}


def _rooftop_unit_builder(detail: str):
    return {"kind": "rooftop_unit", "width": 1.6, "depth": 1.2, "height": 0.9}


def _bed_builder(detail: str):
    return {"kind": "bed", "width": 1.4, "length": 2.0, "height": 0.55}


def _table_builder(detail: str):
    return {"kind": "table", "width": 1.2, "length": 0.8, "height": 0.75}


def _chair_builder(detail: str):
    return {"kind": "chair", "width": 0.45, "depth": 0.45, "height": 0.9}


def _shelf_builder(detail: str):
    return {"kind": "shelf", "width": 0.8, "depth": 0.35, "height": 1.8}


def _desk_builder(detail: str):
    return {"kind": "desk", "width": 1.3, "depth": 0.65, "height": 0.75}


def _counter_builder(detail: str):
    return {"kind": "counter", "width": 2.0, "depth": 0.6, "height": 1.0}


def _crate_builder(detail: str):
    return {"kind": "crate", "size": 0.6}


def _machinery_builder(detail: str):
    return {"kind": "machinery", "width": 1.0, "depth": 1.0, "height": 1.4,
            "segments": _SEGMENTS_BY_DETAIL[detail]}


ASSET_DEFS: Dict[str, AssetDef] = {}

for _i in range(1, 13):
    _asset_id = f"tree_{_i:02d}"
    ASSET_DEFS[_asset_id] = AssetDef(
        asset_id=_asset_id,
        category="tree",
        footprint_radius=1.2,
        builder=_tree_builder(
            trunk_radius=0.15 + (_i % 4) * 0.03,
            canopy_radius=1.0 + (_i % 5) * 0.25,
            height=3.5 + (_i % 6) * 0.8,
            canopy_shape="sphere" if _i % 3 == 0 else "cone",
        ),
    )

ASSET_DEFS["street_lamp"] = AssetDef("street_lamp", "lamp", footprint_radius=0.4, builder=_lamp_builder)
ASSET_DEFS["bench"] = AssetDef("bench", "bench", footprint_radius=0.9, builder=_bench_builder)
ASSET_DEFS["parked_car"] = AssetDef("parked_car", "car", footprint_radius=2.4, builder=_car_builder)
ASSET_DEFS["sign"] = AssetDef("sign", "sign", footprint_radius=0.5, builder=_sign_builder)
ASSET_DEFS["rooftop_unit"] = AssetDef("rooftop_unit", "rooftop", footprint_radius=1.0, builder=_rooftop_unit_builder)

ASSET_DEFS["furniture_bed"] = AssetDef("furniture_bed", "furniture", footprint_radius=1.2, builder=_bed_builder)
ASSET_DEFS["furniture_table"] = AssetDef("furniture_table", "furniture", footprint_radius=0.8, builder=_table_builder)
ASSET_DEFS["furniture_chair"] = AssetDef("furniture_chair", "furniture", footprint_radius=0.35, builder=_chair_builder)
ASSET_DEFS["furniture_shelf"] = AssetDef("furniture_shelf", "furniture", footprint_radius=0.5, builder=_shelf_builder)
ASSET_DEFS["furniture_desk"] = AssetDef("furniture_desk", "furniture", footprint_radius=0.7, builder=_desk_builder)
ASSET_DEFS["furniture_counter"] = AssetDef("furniture_counter", "furniture", footprint_radius=1.1,
                                           builder=_counter_builder)
ASSET_DEFS["furniture_crate"] = AssetDef("furniture_crate", "furniture", footprint_radius=0.45,
                                          builder=_crate_builder)
ASSET_DEFS["furniture_machinery"] = AssetDef("furniture_machinery", "furniture", footprint_radius=0.75,
                                              builder=_machinery_builder)

# Which furniture pieces get placed in a ground-floor interior, keyed by
# facade type (see generators/city/buildings.py's FACADE_TYPES) - not by
# raw zone name, since e.g. "shopfront" and "brick_commercial" both live in
# the commercial zone but should furnish very differently.
FURNITURE_BY_FACADE: Dict[str, List[str]] = {
    "glass_tower": ["furniture_desk", "furniture_chair", "furniture_shelf"],
    "office_block": ["furniture_desk", "furniture_chair", "furniture_shelf"],
    "brick_commercial": ["furniture_counter", "furniture_shelf"],
    "apartment_slab": ["furniture_bed", "furniture_table", "furniture_chair"],
    "apartment_tower": ["furniture_bed", "furniture_table", "furniture_chair"],
    "townhouse": ["furniture_bed", "furniture_table", "furniture_chair"],
    "cottage": ["furniture_bed", "furniture_table"],
    "shopfront": ["furniture_counter", "furniture_shelf"],
    "warehouse": ["furniture_crate", "furniture_crate", "furniture_machinery"],
    "factory_hall": ["furniture_machinery", "furniture_crate"],
    "industrial_tower": ["furniture_machinery", "furniture_crate"],
    "mixed_use": ["furniture_desk", "furniture_chair", "furniture_shelf"],
    # generators/city/special_buildings.py's unique building types
    "supermarket": ["furniture_shelf", "furniture_shelf", "furniture_counter"],
    "police_station": ["furniture_desk", "furniture_chair", "furniture_shelf"],
    "hospital": ["furniture_bed", "furniture_desk", "furniture_chair"],
    "fire_station": ["furniture_machinery", "furniture_crate"],
    "school": ["furniture_desk", "furniture_chair", "furniture_shelf"],
}


def get_asset(asset_id: str) -> AssetDef:
    if asset_id not in ASSET_DEFS:
        raise KeyError(f"Unknown asset id '{asset_id}'. Available: {', '.join(ASSET_DEFS)}")
    return ASSET_DEFS[asset_id]


def assets_in_category(category: str) -> List[AssetDef]:
    return [a for a in ASSET_DEFS.values() if a.category == category]


def tree_asset_ids() -> List[str]:
    return [a.asset_id for a in assets_in_category("tree")]
