"""Pure-Python and Blender-adjacent utility modules for procgen_maps.

No bpy.types classes live in this subpackage, so register()/unregister() are
no-ops kept only for symmetry with the other subpackages' registration
contract (see procgen_maps/__init__.py).
"""


def register():
    pass


def unregister():
    pass
