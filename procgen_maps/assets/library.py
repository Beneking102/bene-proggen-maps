"""Placeholder procedural asset catalog: 12 tree variants, a street lamp, a
bench, a parked car, and a sign.

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
    category: str            # 'tree' | 'lamp' | 'bench' | 'car' | 'sign'
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


def get_asset(asset_id: str) -> AssetDef:
    if asset_id not in ASSET_DEFS:
        raise KeyError(f"Unknown asset id '{asset_id}'. Available: {', '.join(ASSET_DEFS)}")
    return ASSET_DEFS[asset_id]


def assets_in_category(category: str) -> List[AssetDef]:
    return [a for a in ASSET_DEFS.values() if a.category == category]


def tree_asset_ids() -> List[str]:
    return [a.asset_id for a in assets_in_category("tree")]
