"""Pure-Python tests for the SVG and JSON exporters.

`exporters.svg_export.project_top_down`/`build_svg_document` are the pure
projection/serialization steps of that module's PP/BPY split; `export_svg`
itself (bpy mesh extraction) is out of scope here, covered by the headless
Blender smoke test instead.
"""
import json

from procgen_maps.exporters.json_export import export_json
from procgen_maps.exporters.svg_export import build_svg_document, project_top_down


def test_project_top_down_drops_z_and_preserves_order():
    polygons = [
        [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (-1.5, 7.25, 100.0)],
        [(10.0, 20.0, 30.0), (40.0, 50.0, 60.0)],
    ]
    projected = project_top_down(polygons)
    assert len(projected) == len(polygons)
    for polygon, projected_polygon in zip(polygons, projected):
        assert len(projected_polygon) == len(polygon)
        for (x, y, _z), (px, py) in zip(polygon, projected_polygon):
            assert px == x
            assert py == y


def test_project_top_down_empty():
    assert project_top_down([]) == []


def test_build_svg_document_empty_input():
    doc = build_svg_document([])
    assert isinstance(doc, str)
    assert len(doc) > 0
    assert "<svg" in doc


def test_build_svg_document_one_polygon_per_input():
    polygons = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        [(20.0, 20.0), (25.0, 20.0), (22.5, 25.0)],
        [(30.0, 30.0), (40.0, 30.0), (40.0, 40.0), (30.0, 40.0)],
    ]
    doc = build_svg_document(polygons)
    assert doc.count("<polygon") == len(polygons)


def test_build_svg_document_is_valid_enough_xml():
    doc = build_svg_document([[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]])
    stripped = doc.lstrip()
    assert stripped.startswith("<?xml") or stripped.startswith("<svg")


def test_export_json_round_trips(tmp_path):
    data = {
        "preset": "METROPOLE",
        "seed": 7,
        "objects": 128,
        "vertices": 40000,
        "faces": 20000,
        "generate_seconds": 1.234,
        "nested": {"a": [1, 2, 3], "b": None, "c": True},
    }
    filepath = tmp_path / "procgen_maps.json"
    export_json(str(filepath), data)

    with open(filepath, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded == data
