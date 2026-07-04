"""Scene-level property group + registration entrypoint for the N-panel UI."""
import math

import bpy

from ..config.presets import PRESETS
from ..utils._registry import register_classes, unregister_classes
from . import operators, panels

_PRESET_ITEMS = [(key, preset.name, f"{preset.name} preset") for key, preset in PRESETS.items()]


def _on_night_mode_update(self, context):
    operators.apply_night_mode(context, self.night_mode)


def _on_sun_setting_update(self, context):
    operators.apply_sun_settings(context)


def _on_window_emission_update(self, context):
    operators.apply_window_emission(context, self.window_emission_strength)


def _on_street_lamp_energy_update(self, context):
    operators.apply_street_lamp_energy(context, self.street_lamp_energy)


class ProcgenMapsSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(name="Preset", items=_PRESET_ITEMS, default="METROPOLE")
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0)
    use_terrain: bpy.props.BoolProperty(name="Generate Terrain", default=True)
    enable_parks: bpy.props.BoolProperty(name="Parks", default=True)
    enable_commercial: bpy.props.BoolProperty(name="Commercial Zones", default=True)
    enable_cars: bpy.props.BoolProperty(name="Parked Cars", default=True)
    night_mode: bpy.props.BoolProperty(name="Night Mode", default=False, update=_on_night_mode_update)

    # Lighting: live-tunable sun/night-mode/window-glow/street-lamp values,
    # promoted from the hardcoded constants apply_night_mode used to carry
    # (materials/world_mat.py, materials/city_mat.py, ui/operators.py's
    # _update_lamp_lights) - see ui/panels.py's Lighting subpanel. Each
    # mirrors night_mode's own update-callback pattern instead of a
    # separate "Apply" button, since these are cheap in-place property
    # writes (not a full regenerate) and should preview live while dragging.
    sun_elevation: bpy.props.FloatProperty(name="Sun Elevation", subtype='ANGLE', unit='ROTATION',
                                            default=0.6, min=-math.pi / 2, max=math.pi / 2,
                                            update=_on_sun_setting_update)
    sun_rotation: bpy.props.FloatProperty(name="Sun Rotation", subtype='ANGLE', unit='ROTATION',
                                           default=2.4, min=0.0, max=2 * math.pi,
                                           update=_on_sun_setting_update)
    night_sun_elevation: bpy.props.FloatProperty(name="Night Sun Elevation", subtype='ANGLE', unit='ROTATION',
                                                  default=-0.05, min=-math.pi / 2, max=math.pi / 2,
                                                  update=_on_sun_setting_update)
    sun_energy_day: bpy.props.FloatProperty(name="Sun Energy (Day)", default=1.0, min=0.0, max=10.0,
                                             update=_on_sun_setting_update)
    sun_energy_night: bpy.props.FloatProperty(name="Sun Energy (Night)", default=0.05, min=0.0, max=10.0,
                                               update=_on_sun_setting_update)
    window_emission_strength: bpy.props.FloatProperty(name="Window Glow Strength", default=0.25,
                                                        min=0.0, max=5.0, update=_on_window_emission_update)
    street_lamp_energy: bpy.props.FloatProperty(name="Street Lamp Energy", default=400.0,
                                                  min=0.0, max=2000.0, update=_on_street_lamp_energy_update)

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
