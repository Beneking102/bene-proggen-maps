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


def _on_lighting_preset_update(self, context):
    operators.apply_lighting_preset(context, self.lighting_preset)


# Lighting presets: (sun elevation rad, sun rotation rad, world strength, film exposure EV).
# The procedural sky is physically bright, so every daytime preset carries a
# negative exposure; the showcase render and the viewport both use it.
LIGHTING_PRESETS = {
    "MIDDAY": (1.05, 2.4, 1.0, -2.6),
    "AFTERNOON": (0.6, 2.4, 1.0, -2.4),
    "GOLDEN_HOUR": (0.12, 3.6, 1.0, -1.6),
    "OVERCAST_SOFT": (0.75, 2.4, 0.35, -1.4),
}


class ProcgenMapsSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(name="Preset", items=_PRESET_ITEMS, default="METROPOLE")
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0)
    validation_summary: bpy.props.StringProperty(
        name="Validation", default="",
        description="Result of the last Validate Map run (full report in the system console)")
    use_terrain: bpy.props.BoolProperty(name="Generate Terrain", default=True)
    enable_parks: bpy.props.BoolProperty(name="Parks", default=True)
    enable_commercial: bpy.props.BoolProperty(name="Commercial Zones", default=True)
    enable_cars: bpy.props.BoolProperty(name="Parked Cars", default=True)
    enable_water: bpy.props.BoolProperty(
        name="River", default=True,
        description="Run a river through the city: blocks in its path become water, streets crossing it become bridges")
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
    # Night defaults retuned for v0.10's AgX/exposure pipeline: +1 EV at
    # night, a lower window glow (0.25 clipped every lit pane to white) and
    # stronger lamps so their pools actually read on the ground.
    window_emission_strength: bpy.props.FloatProperty(name="Window Glow Strength", default=0.35,
                                                        min=0.0, max=5.0, update=_on_window_emission_update)
    street_lamp_energy: bpy.props.FloatProperty(name="Street Lamp Energy", default=1200.0,
                                                  min=0.0, max=5000.0, update=_on_street_lamp_energy_update)
    lighting_preset: bpy.props.EnumProperty(
        name="Lighting Preset",
        description="Sets sun elevation/rotation, world strength and film exposure together; "
                    "the fields below stay editable afterwards",
        items=(
            ("AFTERNOON", "Afternoon", "Mid-height sun, neutral daylight (the default look)"),
            ("MIDDAY", "Midday", "High sun, short shadows"),
            ("GOLDEN_HOUR", "Golden Hour", "Low warm sun, long shadows, warm haze"),
            ("OVERCAST_SOFT", "Soft Overcast", "Dimmer sky, lower contrast"),
        ),
        default="AFTERNOON", update=_on_lighting_preset_update)
    haze_strength: bpy.props.FloatProperty(
        name="Haze", default=0.4, min=0.0, max=1.0,
        description="Aerial perspective: how strongly distant geometry fades toward the sky color",
        update=_on_sun_setting_update)
    exposure_day: bpy.props.FloatProperty(name="Exposure (Day)", default=-2.4, min=-8.0, max=4.0,
                                           description="Film exposure (EV) applied in day mode",
                                           update=_on_sun_setting_update)
    exposure_night: bpy.props.FloatProperty(name="Exposure (Night)", default=1.0, min=-8.0, max=4.0,
                                             description="Film exposure (EV) applied in night mode",
                                             update=_on_sun_setting_update)

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
    showcase_width: bpy.props.IntProperty(name="Width", default=3840, min=64, max=8192)
    showcase_height: bpy.props.IntProperty(name="Height", default=2160, min=64, max=8192)
    showcase_samples: bpy.props.IntProperty(
        name="Samples", default=128, min=1, max=4096,
        description="Cycles samples per pixel for the showcase/interior renders (denoised, so 64-256 "
                    "is plenty; lower it for quick previews)")

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
