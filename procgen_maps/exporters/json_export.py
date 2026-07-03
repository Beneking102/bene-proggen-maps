"""Sidecar JSON metadata exporter.

Pure Python - no bpy. Callers (ui/operators.py) build the plain dict of
preset/seed/objects/vertices/faces/generate_seconds and hand it off here.
"""
import json


def export_json(filepath: str, data: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
