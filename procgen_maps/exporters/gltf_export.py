"""glTF/GLB export, wrapping Blender's built-in glTF exporter.

Since v0.14 the export goes through `game_export.export_game_gltf`, which
adds the game-engine hierarchy, collision meshes, LODs and the walkable
ground around the same `bpy.ops.export_scene.gltf` call; this module is
kept as the plain entry point (every option off = the v0.13 export plus
the City/Terrain/Dungeon hierarchy).
"""


def export_gltf(filepath: str, options=None) -> None:
    """Plain glTF/GLB export: the game export with every extra off unless `options` says otherwise."""
    from . import game_export

    if options is None:
        options = game_export.GameExportOptions(collision=False, lods=False, walkable=False)
    game_export.export_game_gltf(filepath, options)
