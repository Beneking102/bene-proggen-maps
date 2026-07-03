"""Texture atlas packing.

First-pass materials (terrain_mat.py, city_mat.py) use a shared parametrized
node-group material instead of a true pixel-packed atlas texture - it gets
most of the "few materials, good batching" performance win for Blender's own
viewport/render with far less complexity (see OPTIMIZATION.md).

`pack_rects` is a real, unit-testable shelf-packing algorithm - the piece an
actual pixel atlas (via image.pixels.foreach_get/foreach_set + UV remap)
would need. It is not yet wired into the material pipeline; `bake_atlas_image`
is a documented stub for that follow-up.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class PackedRect:
    index: int
    x: int
    y: int
    width: int
    height: int


def pack_rects(sizes: List[Tuple[int, int]], padding: int = 1) -> Tuple[List[Optional[PackedRect]], int, int]:
    """Shelf-pack a list of (width, height) tiles into a single sheet.

    Returns (placements, sheet_width, sheet_height), with `placements` in the
    same order as `sizes`. Simple and deterministic, good enough for a modest
    number of material tiles (facade types, terrain layers); not a general
    bin-packing optimum.
    """
    if not sizes:
        return [], 0, 0

    order = sorted(range(len(sizes)), key=lambda i: sizes[i][1], reverse=True)
    sheet_width = max(w for w, _ in sizes) * 4 + padding * len(sizes)

    placements: List[Optional[PackedRect]] = [None] * len(sizes)
    cursor_x = padding
    cursor_y = padding
    shelf_height = 0
    sheet_height = padding

    for index in order:
        width, height = sizes[index]
        if cursor_x + width + padding > sheet_width:
            cursor_x = padding
            cursor_y += shelf_height + padding
            shelf_height = 0
        placements[index] = PackedRect(index, cursor_x, cursor_y, width, height)
        cursor_x += width + padding
        shelf_height = max(shelf_height, height)
        sheet_height = max(sheet_height, cursor_y + shelf_height + padding)

    return placements, sheet_width, sheet_height


def bake_atlas_image(name, packed_rects, source_images):
    """Not yet implemented - documented follow-up.

    Would blit each source image's pixels into one shared bpy.data.images
    buffer at its packed rect and return the combined image, for export
    targets that benefit from a single packed texture. See OPTIMIZATION.md.
    """
    raise NotImplementedError(
        "True pixel texture atlas baking is a documented follow-up; materials "
        "currently use a shared parametrized shader instead (see "
        "materials/city_mat.py and materials/terrain_mat.py)."
    )
