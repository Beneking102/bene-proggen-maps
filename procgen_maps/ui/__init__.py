"""Scene-level property group + registration entrypoint for the N-panel UI."""
import bpy

from ..config.presets import PRESETS
from ..utils._registry import register_classes, unregister_classes
from . import operators, panels

_PRESET_ITEMS = [(key, preset.name, f"{preset.name} preset") for key, preset in PRESETS.items()]


def _on_night_mode_update(self, context):
    operators.apply_night_mode(context, self.night_mode)


class ProcgenMapsSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(name="Preset", items=_PRESET_ITEMS, default="METROPOLE")
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0)
    use_terrain: bpy.props.BoolProperty(name="Generate Terrain", default=True)
    enable_parks: bpy.props.BoolProperty(name="Parks", default=True)
    enable_commercial: bpy.props.BoolProperty(name="Commercial Zones", default=True)
    enable_cars: bpy.props.BoolProperty(name="Parked Cars", default=True)
    night_mode: bpy.props.BoolProperty(name="Night Mode", default=False, update=_on_night_mode_update)
    export_directory: bpy.props.StringProperty(name="Export Directory", subtype='DIR_PATH',
                                                 default="//procgen_maps_export/")

    showcase_angle: bpy.props.EnumProperty(
        name="Angle",
        items=(
            ("overview", "Overview", "Wide, elevated three-quarter view of the whole generated area"),
            ("close", "Close-up", "Zoomed in on one corner/building cluster"),
            ("low", "Low Angle", "Low, more dramatic street-level-ish angle"),
        ),
        default="overview",
    )
    showcase_width: bpy.props.IntProperty(name="Width", default=1280, min=64, max=8192)
    showcase_height: bpy.props.IntProperty(name="Height", default=800, min=64, max=8192)

    stat_objects: bpy.props.IntProperty(name="Objects", default=0)
    stat_vertices: bpy.props.IntProperty(name="Vertices", default=0)
    stat_faces: bpy.props.IntProperty(name="Faces", default=0)
    stat_generate_seconds: bpy.props.FloatProperty(name="Last Generate Seconds", default=0.0)


_CLASSES = (ProcgenMapsSettings,)


def register():
    register_classes(_CLASSES)
    bpy.types.Scene.procgen_maps = bpy.props.PointerProperty(type=ProcgenMapsSettings)
    operators.register()
    panels.register()


def unregister():
    panels.unregister()
    operators.unregister()
    del bpy.types.Scene.procgen_maps
    unregister_classes(_CLASSES)
