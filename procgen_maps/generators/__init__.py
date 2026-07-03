"""Terrain, city and dungeon generators."""
from . import city


def register():
    city.register()


def unregister():
    city.unregister()
