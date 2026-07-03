"""Shared register/unregister helper for bpy.types classes.

Every subpackage's __init__.py calls these instead of calling
bpy.utils.register_class/unregister_class directly, so a double-registration
during Blender's "Reload Scripts" dev loop logs a warning instead of raising.
"""
from .logger import get_logger

_logger = get_logger("registry")


def register_classes(classes):
    import bpy

    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            _logger.warning("Class already registered, skipping: %s", cls.__name__)


def unregister_classes(classes):
    import bpy

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (ValueError, RuntimeError):
            _logger.warning("Class was not registered, skipping: %s", cls.__name__)
