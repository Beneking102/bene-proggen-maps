"""FBX export, wrapping Blender's built-in io_scene_fbx exporter."""


def is_fbx_available() -> bool:
    """Whether Blender's FBX exporter operator is registered (the io_scene_fbx add-on)."""
    import bpy

    return hasattr(bpy.ops.export_scene, "fbx")


def export_fbx(filepath: str) -> None:
    """Export the whole scene to `filepath` with Blender's FBX exporter."""
    import bpy

    bpy.ops.export_scene.fbx(filepath=filepath, use_selection=False,
                              apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL')
