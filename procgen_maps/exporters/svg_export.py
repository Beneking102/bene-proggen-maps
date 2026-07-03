"""Top-down 2D SVG map exporter.

`project_top_down` and `build_svg_document` are pure Python - they work on
plain (x, y[, z]) tuple lists, so they're pytest-testable with no bpy.
`export_svg` is the thin bpy wrapper: it reduces each object's mesh to its
world-space axis-aligned footprint rectangle before handing off to the pure
functions, which keeps the map readable at city scale.
"""
from typing import Iterable, List, Sequence, Tuple

Point2 = Tuple[float, float]
Point3 = Tuple[float, float, float]


def project_top_down(polygons_world: Iterable[Sequence[Point3]]) -> List[List[Point2]]:
    return [[(x, y) for (x, y, z) in polygon] for polygon in polygons_world]


def build_svg_document(polygons_2d: List[List[Point2]], width=1000, height=1000, padding=20,
                        fill_color="#cccccc", stroke_color="#333333") -> str:
    header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if not polygons_2d:
        return (
            header
            + f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            + f'viewBox="0 0 {width} {height}"></svg>\n'
        )

    all_points = [point for polygon in polygons_2d for point in polygon]
    min_x = min(p[0] for p in all_points)
    max_x = max(p[0] for p in all_points)
    min_y = min(p[1] for p in all_points)
    max_y = max(p[1] for p in all_points)

    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0
    drawable_w = width - 2 * padding
    drawable_h = height - 2 * padding

    def to_pixel(point: Point2) -> Point2:
        x, y = point
        px = padding + (x - min_x) / span_x * drawable_w
        py = padding + (1.0 - (y - min_y) / span_y) * drawable_h
        return px, py

    polygon_elements = []
    for polygon in polygons_2d:
        pixel_points = [to_pixel(point) for point in polygon]
        points_attr = " ".join(f"{px:.2f},{py:.2f}" for (px, py) in pixel_points)
        polygon_elements.append(
            f'<polygon points="{points_attr}" fill="{fill_color}" stroke="{stroke_color}" />'
        )

    body = "\n  ".join(polygon_elements)
    return (
        header
        + f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        + f'viewBox="0 0 {width} {height}">\n  {body}\n</svg>\n'
    )


def export_svg(filepath: str, objects) -> None:
    import bpy

    footprints: List[List[Point3]] = []
    for obj in objects:
        mesh = obj.data
        matrix = obj.matrix_world
        world_verts = [matrix @ v.co for v in mesh.vertices]
        if not world_verts:
            continue
        min_x = min(v.x for v in world_verts)
        max_x = max(v.x for v in world_verts)
        min_y = min(v.y for v in world_verts)
        max_y = max(v.y for v in world_verts)
        z = world_verts[0].z
        footprints.append([
            (min_x, min_y, z), (max_x, min_y, z), (max_x, max_y, z), (min_x, max_y, z),
        ])

    polygons_2d = project_top_down(footprints)
    svg_string = build_svg_document(polygons_2d)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(svg_string)
