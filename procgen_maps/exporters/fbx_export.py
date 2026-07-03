"""FBX export, wrapping Blender's built-in io_scene_fbx exporter."""


def is_fbx_available() -> bool:
    import bpy

    return hasattr(bpy.ops.export_scene, "fbx")


def export_fbx(filepath: str) -> None:
    import bpy

    bpy.ops.export_scene.fbx(filepath=filepath, use_selection=False,
                              apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL')
