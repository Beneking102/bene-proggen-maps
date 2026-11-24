"""Custom (user) presets: the panel's generation and lighting settings saved
as JSON files in Blender's per-user config directory, so a setup survives
the .blend file and can be shared.

The file handling (`save_preset`, `load_preset`, `list_presets`,
`delete_preset`, the name sanitising and the settings <-> dict mapping) is
pure Python with an explicit `directory` argument, so the test suite
exercises it in a temp dir; only `user_presets_dir` touches bpy to find
`bpy.utils.user_resource('CONFIG')/procgen_maps/presets`.
"""
import json
import os
import re
from typing import Dict, List

PRESET_FILE_VERSION = 1
PRESET_EXTENSION = ".json"
_SUBDIRECTORY = os.path.join("procgen_maps", "presets")

# Every ProcgenMapsSettings field a custom preset carries. Stats, the
# validation summary and the user-preset name/choice fields are left out
# on purpose: they describe the last run, not a setup.
PRESET_FIELDS = (
    "preset", "seed", "use_terrain", "enable_parks", "enable_commercial", "enable_cars", "enable_water",
    "night_mode", "dungeon_theme", "dungeon_levels",
    "lighting_preset", "sun_elevation", "sun_rotation", "night_sun_elevation", "sun_energy_day",
    "sun_energy_night", "window_emission_strength", "street_lamp_energy", "haze_strength", "exposure_day",
    "exposure_night",
    "showcase_angle", "showcase_width", "showcase_height", "showcase_samples",
    "export_directory", "export_collision", "export_naming", "export_lods", "export_walkable",
)

_UNSAFE = re.compile(r"[^A-Za-z0-9 _\-\.]+")


def user_presets_dir(create: bool = True) -> str:
    """The per-user presets folder (`.../config/procgen_maps/presets` under
    Blender's user resource path)."""
    import bpy

    base = bpy.utils.user_resource('CONFIG', path=_SUBDIRECTORY, create=create)
    return base


def safe_preset_name(name: str) -> str:
    """A preset name reduced to a file-system-safe stem: letters, digits,
    space, underscore, dash and dot, trimmed; empty names become
    'preset'. Pure Python."""
    cleaned = _UNSAFE.sub("", str(name or "")).strip().strip(".")
    return cleaned[:60] if cleaned else "preset"


def preset_path(name: str, directory: str) -> str:
    """The JSON file path a preset name maps to inside `directory`."""
    return os.path.join(directory, safe_preset_name(name) + PRESET_EXTENSION)


def settings_to_dict(settings, fields=PRESET_FIELDS) -> Dict[str, object]:
    """Read the preset fields off a settings object (a PropertyGroup or any
    object with those attributes) into plain JSON-able values."""
    data = {}
    for field in fields:
        if not hasattr(settings, field):
            continue
        value = getattr(settings, field)
        if isinstance(value, (bool, int, float, str)):
            data[field] = value
        else:
            data[field] = str(value)
    return data


def dict_to_settings(settings, data: Dict[str, object], fields=PRESET_FIELDS) -> List[str]:
    """Write the known fields of `data` onto `settings`; unknown keys and
    values the property rejects (a renamed enum item, a type mismatch) are
    skipped. Returns the names of the fields that were applied."""
    applied = []
    for field in fields:
        if field not in data or not hasattr(settings, field):
            continue
        try:
            setattr(settings, field, data[field])
        except (TypeError, ValueError, AttributeError):
            continue
        applied.append(field)
    return applied


def save_preset(name: str, data: Dict[str, object], directory: str) -> str:
    """Write `data` as `<directory>/<safe name>.json`; returns the path."""
    os.makedirs(directory, exist_ok=True)
    path = preset_path(name, directory)
    payload = {"format": "procgen_maps_preset", "version": PRESET_FILE_VERSION, "name": str(name),
               "settings": dict(data)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return path


def load_preset(name: str, directory: str) -> Dict[str, object]:
    """Read a preset's settings dict. Raises FileNotFoundError for a
    missing preset and ValueError for a file that is not a preset."""
    path = preset_path(name, directory)
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("format") != "procgen_maps_preset":
        raise ValueError(f"{path} is not a Procgen Maps preset file")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError(f"{path} has no settings")
    return settings


def list_presets(directory: str) -> List[str]:
    """Sorted preset names (file stems) in `directory`; [] if it does not exist."""
    if not directory or not os.path.isdir(directory):
        return []
    names = [entry[:-len(PRESET_EXTENSION)] for entry in os.listdir(directory)
             if entry.lower().endswith(PRESET_EXTENSION) and os.path.isfile(os.path.join(directory, entry))]
    return sorted(names, key=str.lower)


def delete_preset(name: str, directory: str) -> bool:
    """Remove a preset file; False if there was none."""
    path = preset_path(name, directory)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True
