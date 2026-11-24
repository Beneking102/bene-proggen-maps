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
    """Export the scene to `filepath` with Blender's USD exporter (best effort: .usdz needs a USD build)."""
    import bpy

    if not filepath.endswith(".usdz"):
        raise ValueError("filepath must end with .usdz")

    # Blender 5.0 renamed the exporter's texture option (`export_textures`
    # became `export_textures_mode`, an enum) - passing an unknown keyword
    # raises TypeError before the operator even runs. Only pass what this
    # Blender's operator actually declares.
    kwargs = {"filepath": filepath, "selected_objects_only": False}
    declared = bpy.ops.wm.usd_export.get_rna_type().properties.keys()
    if "export_textures" in declared:
        kwargs["export_textures"] = True
    elif "export_textures_mode" in declared:
        kwargs["export_textures_mode"] = 'NEW'
    bpy.ops.wm.usd_export(**kwargs)
