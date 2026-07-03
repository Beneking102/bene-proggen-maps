"""Spatial queries used for collision-free prop/building placement.

`SpatialHashGrid` is the primary, portable structure (pure Python, unit
testable outside Blender). `build_kdtree` is an optional exact-nearest-
neighbor path using `mathutils.kdtree.KDTree`, which ships with Blender's
`mathutils` module - no scipy required either way (see ARCHITECTURE.md).
"""
import math


class SpatialHashGrid:
    """A 2D spatial hash grid mapping (x, y) cells to the items placed in them."""

    def __init__(self, cell_size=5.0):
        self.cell_size = cell_size
        self._cells = {}

    def _cell_coord(self, x, y):
        return (math.floor(x / self.cell_size), math.floor(y / self.cell_size))

    def insert(self, item_id, x, y, radius=0.0):
        cell = self._cell_coord(x, y)
        self._cells.setdefault(cell, []).append((item_id, x, y, radius))

    def remove(self, item_id, x, y):
        cell = self._cell_coord(x, y)
        bucket = self._cells.get(cell)
        if not bucket:
            return
        self._cells[cell] = [entry for entry in bucket if entry[0] != item_id]

    def query_radius(self, x, y, radius):
        """Return the ids of all items whose center lies within `radius` of (x, y)."""
        result = []
        span = int(math.ceil(radius / self.cell_size)) + 1
        cx0, cy0 = self._cell_coord(x, y)
        for cx in range(cx0 - span, cx0 + span + 1):
            for cy in range(cy0 - span, cy0 + span + 1):
                for item_id, ix, iy, _ in self._cells.get((cx, cy), ()):
                    if (ix - x) ** 2 + (iy - y) ** 2 <= radius * radius:
                        result.append(item_id)
        return result

    def has_collision(self, x, y, radius):
        """True if a circle of `radius` at (x, y) overlaps any existing item's own footprint radius."""
        span = int(math.ceil((radius + self.cell_size) / self.cell_size)) + 1
        cx0, cy0 = self._cell_coord(x, y)
        for cx in range(cx0 - span, cx0 + span + 1):
            for cy in range(cy0 - span, cy0 + span + 1):
                for _, ix, iy, item_radius in self._cells.get((cx, cy), ()):
                    if math.hypot(ix - x, iy - y) < radius + item_radius:
                        return True
        return False


def build_kdtree(points):
    """Build a `mathutils.kdtree.KDTree` from an iterable of (x, y, z) points.

    Requires `mathutils` (bundled with Blender). Kept separate from
    `SpatialHashGrid` so pure-python code paths never need it.
    """
    from mathutils.kdtree import KDTree

    points = list(points)
    kd = KDTree(len(points))
    for index, co in enumerate(points):
        kd.insert(co, index)
    kd.balance()
    return kd
