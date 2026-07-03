"""Best-effort USDZ (AR) export, wrapping Blender's native USD exporter.

Blender has packaged native USDZ export since 3.5: pointing
bpy.ops.wm.usd_export at a ".usdz" filepath makes it zip the USD payload and
its textures itself, so no Apple tooling is required to produce the file.
Caveats: UDIM tiled textures are not supported inside a USDZ package, and
passing Apple's own AR Quick Look validation on a real device is stricter
than anything Blender's exporter checks - this only guarantees a well-formed
USDZ, not that Quick Look will accept it.
"""


def export_usdz(filepath: str) -> None:
    import bpy

    if not filepath.endswith(".usdz"):
        raise ValueError("filepath must end with .usdz")

    bpy.ops.wm.usd_export(filepath=filepath, export_textures=True, selected_objects_only=False)
