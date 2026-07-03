"""Pure-Python tests for materials.atlas shelf-packing."""
import pytest

from procgen_maps.materials import atlas


def _overlaps(a, b):
    return (a.x < b.x + b.width and b.x < a.x + a.width
            and a.y < b.y + b.height and b.y < a.y + a.height)


def test_pack_rects_empty_input():
    assert atlas.pack_rects([]) == ([], 0, 0)


def test_pack_rects_places_every_rect():
    sizes = [(64, 32), (16, 16), (128, 64), (8, 40), (32, 32)]
    placements, sheet_width, sheet_height = atlas.pack_rects(sizes)
    assert len(placements) == len(sizes)
    assert all(p is not None for p in placements)


def test_pack_rects_indices_match_input_order():
    sizes = [(64, 32), (16, 16), (128, 64)]
    placements, _, _ = atlas.pack_rects(sizes)
    for i, (width, height) in enumerate(sizes):
        assert placements[i].index == i
        assert placements[i].width == width
        assert placements[i].height == height


def test_pack_rects_no_overlaps():
    sizes = [(64, 32), (16, 16), (128, 64), (8, 40), (32, 32), (50, 20), (10, 10)]
    placements, _, _ = atlas.pack_rects(sizes)
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            assert not _overlaps(placements[i], placements[j])


def test_pack_rects_sheet_covers_largest_rect():
    sizes = [(64, 32), (16, 16), (128, 64), (8, 40)]
    _, sheet_width, sheet_height = atlas.pack_rects(sizes)
    max_width = max(w for w, _ in sizes)
    max_height = max(h for _, h in sizes)
    assert sheet_width >= max_width
    assert sheet_height >= max_height


def test_bake_atlas_image_not_implemented():
    with pytest.raises(NotImplementedError):
        atlas.bake_atlas_image("atlas", [], [])
