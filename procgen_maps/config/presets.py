"""City archetype presets: Metropole, Kleinstadt, Dorf, Industrial."""
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class CityPreset:
    """Static tuning parameters for one city archetype."""

    name: str
    radius: float                        # city footprint radius, meters
    block_size: float                    # average block edge length, meters
    layout_mode: str                     # 'grid' or 'raster' (see generators.city.layout)
    building_height_range: Tuple[float, float]  # (min, max) meters
    building_floor_height: float
    density: float                       # 0..1, fraction of blocks that get a building
    zone_ratios: Dict[str, float]        # residential/commercial/industrial/park, sums to ~1.0
    street_width_arterial: float
    street_width_local: float
    prop_density: float                  # 0..1, relative density of lamps/benches/trees/signs
    cars_enabled: bool
    night_mode_default: bool
    seed: int = 0


PRESETS: Dict[str, CityPreset] = {
    "METROPOLE": CityPreset(
        name="Metropole",
        radius=400.0,
        block_size=40.0,
        layout_mode="raster",
        building_height_range=(15.0, 120.0),
        building_floor_height=3.5,
        density=0.85,
        zone_ratios={"residential": 0.35, "commercial": 0.4, "industrial": 0.1, "park": 0.15},
        street_width_arterial=14.0,
        street_width_local=7.0,
        prop_density=0.9,
        cars_enabled=True,
        night_mode_default=True,
        seed=1,
    ),
    "KLEINSTADT": CityPreset(
        name="Kleinstadt",
        radius=220.0,
        block_size=30.0,
        layout_mode="grid",
        building_height_range=(4.0, 20.0),
        building_floor_height=3.0,
        density=0.6,
        zone_ratios={"residential": 0.55, "commercial": 0.2, "industrial": 0.1, "park": 0.15},
        street_width_arterial=10.0,
        street_width_local=6.0,
        prop_density=0.6,
        cars_enabled=True,
        night_mode_default=False,
        seed=2,
    ),
    "DORF": CityPreset(
        name="Dorf",
        radius=120.0,
        block_size=25.0,
        layout_mode="grid",
        building_height_range=(3.0, 8.0),
        building_floor_height=2.8,
        density=0.35,
        zone_ratios={"residential": 0.7, "commercial": 0.1, "industrial": 0.05, "park": 0.15},
        street_width_arterial=7.0,
        street_width_local=4.5,
        prop_density=0.35,
        cars_enabled=False,
        night_mode_default=False,
        seed=3,
    ),
    "INDUSTRIAL": CityPreset(
        name="Industrial",
        radius=260.0,
        block_size=50.0,
        layout_mode="raster",
        building_height_range=(6.0, 25.0),
        building_floor_height=4.5,
        density=0.7,
        zone_ratios={"residential": 0.1, "commercial": 0.1, "industrial": 0.7, "park": 0.1},
        street_width_arterial=12.0,
        street_width_local=8.0,
        prop_density=0.3,
        cars_enabled=True,
        night_mode_default=True,
        seed=4,
    ),
}


def get_preset(key: str) -> CityPreset:
    """Look up a preset by key (case-insensitive)."""
    normalized = key.upper()
    if normalized not in PRESETS:
        raise KeyError(f"Unknown preset '{key}'. Available: {', '.join(PRESETS)}")
    return PRESETS[normalized]
