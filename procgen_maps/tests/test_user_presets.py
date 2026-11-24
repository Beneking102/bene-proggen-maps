"""Pure-Python tests for config/user_presets.py (custom preset files)."""
import json
import os
from types import SimpleNamespace

import pytest

from procgen_maps.config import user_presets


def _settings(**overrides):
    base = dict(preset="KLEINSTADT", seed=42, use_terrain=True, enable_parks=False, enable_commercial=True,
                enable_cars=True, enable_water=False, night_mode=True, dungeon_theme="castle", dungeon_levels=3,
                lighting_preset="GOLDEN_HOUR", sun_elevation=0.12, sun_rotation=3.6, night_sun_elevation=-0.05,
                sun_energy_day=1.0, sun_energy_night=0.05, window_emission_strength=0.35,
                street_lamp_energy=1200.0, haze_strength=0.3, exposure_day=-2.7, exposure_night=1.0,
                showcase_angle="low", showcase_width=1920, showcase_height=1080, showcase_samples=32,
                export_directory="//out/", export_collision=True, export_naming="UNITY", export_lods=False,
                export_walkable=True, stat_objects=999, validation_summary="OK", user_preset_name="x")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_safe_preset_name_strips_unsafe_characters():
    assert user_presets.safe_preset_name("My City / Night?") == "My City  Night"
    assert user_presets.safe_preset_name("  ../../evil  ") == "evil"
    assert user_presets.safe_preset_name("") == "preset"
    assert user_presets.safe_preset_name(None) == "preset"
    assert len(user_presets.safe_preset_name("x" * 200)) == 60


def test_settings_to_dict_only_carries_preset_fields():
    data = user_presets.settings_to_dict(_settings())
    assert set(data) == set(user_presets.PRESET_FIELDS)
    assert "stat_objects" not in data and "validation_summary" not in data and "user_preset_name" not in data
    assert data["seed"] == 42 and data["export_naming"] == "UNITY" and data["night_mode"] is True


def test_save_load_list_delete_round_trip(tmp_path):
    directory = str(tmp_path / "presets")
    data = user_presets.settings_to_dict(_settings())
    path = user_presets.save_preset("Night Town", data, directory)
    assert os.path.isfile(path) and path.endswith("Night Town.json")
    assert user_presets.list_presets(directory) == ["Night Town"]
    loaded = user_presets.load_preset("Night Town", directory)
    assert loaded == data
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["format"] == "procgen_maps_preset" and payload["version"] == user_presets.PRESET_FILE_VERSION
    assert user_presets.delete_preset("Night Town", directory) is True
    assert user_presets.delete_preset("Night Town", directory) is False
    assert user_presets.list_presets(directory) == []
    assert user_presets.list_presets(str(tmp_path / "missing")) == []


def test_dict_to_settings_applies_known_fields_and_skips_bad_values():
    target = _settings(seed=0, preset="DORF")

    class Strict(SimpleNamespace):
        def __setattr__(self, name, value):
            if name == "preset" and value not in ("METROPOLE", "KLEINSTADT", "DORF", "INDUSTRIAL"):
                raise TypeError("enum")
            super().__setattr__(name, value)

    target = Strict(**vars(target))
    applied = user_presets.dict_to_settings(target, {"seed": 7, "preset": "NOT_A_PRESET", "unknown": 1,
                                                     "haze_strength": 0.9})
    assert applied == ["seed", "haze_strength"]
    assert target.seed == 7 and target.preset == "DORF" and target.haze_strength == 0.9


def test_load_preset_rejects_foreign_json(tmp_path):
    directory = str(tmp_path)
    with open(os.path.join(directory, "other.json"), "w", encoding="utf-8") as handle:
        json.dump({"hello": "world"}, handle)
    with pytest.raises(ValueError):
        user_presets.load_preset("other", directory)
    with pytest.raises(FileNotFoundError):
        user_presets.load_preset("nope", directory)
