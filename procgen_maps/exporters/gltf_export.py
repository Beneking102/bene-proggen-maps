"""glTF/GLB export, wrapping Blender's built-in glTF exporter.

procgen_maps generates everything into collections directly under the scene,
so exporting the whole scene captures the full generated map.
"""


def export_gltf(filepath: str) -> None:
    import bpy

    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format='GLB',
        export_apply=True,
        use_selection=False,
    )
