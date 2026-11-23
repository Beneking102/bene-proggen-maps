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


# --- game export (exporters/game_export.py), the pure half --------------------

from procgen_maps.exporters.game_export import (GameExportOptions, box_corners, collision_name, downsample_grid,
                                                 grid_downsample_stride, lod_name, walkable_name)


def test_collision_names_follow_godot_importer_suffixes():
    assert collision_name("Building_3", "GODOT") == "Building_3-colonly"
    assert collision_name("Streets", "GODOT", "mesh") == "Streets-col"
    assert collision_name("Rock", "GODOT", "convex") == "Rock-convcolonly"
    assert walkable_name("Ground_Walkable", "GODOT") == "Ground_Walkable-navmesh"


def test_collision_names_unity_use_col_suffix_and_keep_mesh_names():
    assert collision_name("Building_3", "UNITY") == "Building_3_col"
    assert collision_name("Streets", "UNITY", "mesh") == "Streets"
    assert walkable_name("Ground_Walkable", "unity") == "Ground_Walkable_navmesh"


def test_lod_names_follow_unity_lodgroup_convention():
    assert lod_name("ProcgenMaps_Building_7", 0) == "ProcgenMaps_Building_7_LOD0"
    assert lod_name("tree_03", 1) == "tree_03_LOD1"


def test_box_corners_is_a_closed_box():
    verts, faces = box_corners((0.0, 0.0, 0.0), (2.0, 3.0, 4.0))
    assert len(verts) == 8 and len(faces) == 6
    assert (2.0, 3.0, 4.0) in verts and (0.0, 0.0, 0.0) in verts
    # Every edge is shared by exactly two faces.
    edges = {}
    for face in faces:
        for i in range(4):
            a, b = face[i], face[(i + 1) % 4]
            edges[(min(a, b), max(a, b))] = edges.get((min(a, b), max(a, b)), 0) + 1
    assert set(edges.values()) == {2} and len(edges) == 12


def test_grid_downsample_stride_targets_about_160_per_side():
    assert grid_downsample_stride(512) == 4
    assert grid_downsample_stride(1024) == 7
    assert grid_downsample_stride(100) == 1
    assert grid_downsample_stride(2) == 1


def test_downsample_grid_keeps_footprint_and_corners():
    import numpy as np

    res = 9
    xs = np.linspace(-4.0, 4.0, res)
    grid = np.stack(list(np.meshgrid(xs, xs, indexing="xy")) + [np.zeros((res, res))], axis=-1)   # (row=y, col=x)
    grid[..., 2] = grid[..., 0] * 0.5
    verts, faces = downsample_grid(grid.reshape(-1), res, 4)
    assert len(verts) == 9 and len(faces) == 4      # 3 x 3 vertices, 2 x 2 quads
    assert verts[:, 0].min() == -4.0 and verts[:, 0].max() == 4.0
    assert verts[:, 1].min() == -4.0 and verts[:, 1].max() == 4.0
    # A stride that does not divide the grid still keeps the last row/column.
    verts, faces = downsample_grid(grid.reshape(-1), res, 5)
    assert verts[:, 0].max() == 4.0 and len(verts) == 9
    for face in faces:
        assert len(set(int(i) for i in face)) == 4


def test_game_export_options_defaults_are_everything_on_godot():
    options = GameExportOptions()
    assert options.collision and options.lods and options.walkable and options.naming == "GODOT"
