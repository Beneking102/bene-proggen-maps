"""Procgen Maps - procedural city, terrain and dungeon generator for Blender.

See ARCHITECTURE.md for the registration architecture and the PP/BPY module
split convention followed throughout this addon.
"""
bl_info = {
    "name": "Procgen Maps",
    "author": "Beneking102",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Procgen Maps",
    "description": "Procedural city, terrain and dungeon generator with glTF/FBX/USDZ/SVG/JSON export",
    "warning": "",
    "doc_url": "https://github.com/Beneking102/bene-proggen-maps",
    "tracker_url": "https://github.com/Beneking102/bene-proggen-maps/issues",
    "category": "Scene",
}

# The `ui` subpackage (and, transitively, everything it touches) imports
# `bpy` at module level, as is normal for files defining bpy.types.Operator/
# Panel classes. To keep every non-ui module importable outside Blender (for
# the pure-Python pytest suite - see ARCHITECTURE.md), that import is
# deferred into register()/unregister() rather than done at module level
# here, so merely `import procgen_maps` never requires bpy to exist.


def _submodules():
    from . import assets, config, exporters, generators, materials, rendering, ui, utils

    # Dependency order: utils/config have no bpy.types classes; assets/
    # materials are generator dependencies; generators must exist before ui
    # (operators call into generators); exporters/rendering last since
    # ui/operators.py only imports them lazily, per-operator.
    return (utils, config, assets, materials, generators, exporters, rendering, ui)


def register():
    for module in _submodules():
        module.register()


def unregister():
    for module in reversed(_submodules()):
        module.unregister()
