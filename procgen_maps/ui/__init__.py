"""Scene-level property group + registration entrypoint for the N-panel UI.

Every property carries a `description` - it is the tooltip the panels
show - and the ones that retune the scene live (lighting, night mode)
do so through an `update=` callback into ui/operators.py.
"""
import math

import bpy

from ..config.presets import PRESETS
from ..utils._registry import register_classes, unregister_classes
from . import operators, panels

_PRESET_ITEMS = [(key, preset.name, f"{preset.name}: {preset.radius:.0f} m radius, {preset.layout_mode} layout, "
                                     f"{preset.building_height_range[0]:.0f}-{preset.building_height_range[1]:.0f} m buildings")
                 for key, preset in PRESETS.items()]


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
    "MIDDAY": (1.05, 2.4, 1.0, -2.9),
    "AFTERNOON": (0.6, 2.4, 1.0, -2.7),
    "GOLDEN_HOUR": (0.12, 3.6, 1.0, -1.6),
    "OVERCAST_SOFT": (0.75, 2.4, 0.35, -1.4),
}

# The dynamic enum's item tuples must outlive the callback (Blender keeps
# only references to the strings), hence this module-level cache.
_USER_PRESET_ITEMS_CACHE = []
_NO_USER_PRESETS = ("", "(no saved presets)", "Save the current settings first")


def _user_preset_items(self, context):
    from ..config import user_presets

    _USER_PRESET_ITEMS_CACHE.clear()
    try:
        names = user_presets.list_presets(user_presets.user_presets_dir(create=False))
    except (OSError, RuntimeError):
        names = []
    for name in names:
        _USER_PRESET_ITEMS_CACHE.append((name, name, f"Custom preset '{name}'"))
    if not _USER_PRESET_ITEMS_CACHE:
        _USER_PRESET_ITEMS_CACHE.append(_NO_USER_PRESETS)
    return _USER_PRESET_ITEMS_CACHE


class ProcgenMapsSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(
        name="Preset", items=_PRESET_ITEMS, default="METROPOLE",
        description="City archetype: size, block size, layout mode, building heights, zone mix and prop density")
    seed: bpy.props.IntProperty(
        name="Seed", default=0, min=0,
        description="Random seed shared by terrain, city and dungeon; the same seed and preset always give the same map")
    validation_summary: bpy.props.StringProperty(
        name="Validation", default="",
        description="Result of the last Validate Map run (full report in the system console)")
    last_message: bpy.props.StringProperty(
        name="Last Message", default="",
        description="What the last generate / export / render did")
    use_terrain: bpy.props.BoolProperty(
        name="Terrain", default=True,
        description="Generate All builds the terrain first and the city follows its surface; off, the city sits on z = 0")
    enable_parks: bpy.props.BoolProperty(
        name="Parks", default=True,
        description="Keep the preset's park blocks (paths, fountain, benches, trees); off, those blocks get buildings")
    enable_commercial: bpy.props.BoolProperty(
        name="Commercial", default=True,
        description="Keep the preset's commercial zones (shopfronts, offices); off, they become residential/industrial")
    enable_cars: bpy.props.BoolProperty(
        name="Parked Cars", default=True,
        description="Parked cars in the kerbside lane of local streets (never in the Dorf preset)")
    enable_water: bpy.props.BoolProperty(
        name="River", default=True,
        description="Run a river through the city: blocks in its path become water, streets crossing it become bridges")
    night_mode: bpy.props.BoolProperty(
        name="Night Mode", default=False, update=_on_night_mode_update,
        description="Lit windows (apartments and office floors), street lamp lights, and the sun dipped below the horizon")
    dungeon_theme: bpy.props.EnumProperty(
        name="Dungeon Theme",
        items=(("crypt", "Crypt", "Grey stone, iron gates, sarcophagi, cool fill light"),
               ("cave", "Cave", "Brown rock, no doors, boulders"),
               ("castle", "Castle", "Sandstone and timber, oak doors, banners, warm fill light")),
        default="crypt",
        description="Materials, doors and props of the generated dungeon")
    dungeon_levels: bpy.props.IntProperty(
        name="Dungeon Levels", default=2, min=1, max=4,
        description="Stacked levels joined by staircases (the boss hall is on the lowest one)")

    # Lighting: live-tunable sun/night-mode/window-glow/street-lamp values,
    # see ui/panels.py's Lighting subpanel. Each mirrors night_mode's own
    # update-callback pattern instead of a separate "Apply" button, since
    # these are cheap in-place property writes (not a full regenerate).
    sun_elevation: bpy.props.FloatProperty(
        name="Sun Elevation", subtype='ANGLE', unit='ROTATION', default=0.6, min=-math.pi / 2, max=math.pi / 2,
        update=_on_sun_setting_update,
        description="Height of the sun above the horizon in day mode (0 = horizon, 90 degrees = zenith)")
    sun_rotation: bpy.props.FloatProperty(
        name="Sun Rotation", subtype='ANGLE', unit='ROTATION', default=2.4, min=0.0, max=2 * math.pi,
        update=_on_sun_setting_update,
        description="Compass direction the sun shines from")
    night_sun_elevation: bpy.props.FloatProperty(
        name="Night Sun Elevation", subtype='ANGLE', unit='ROTATION', default=-0.05, min=-math.pi / 2, max=math.pi / 2,
        update=_on_sun_setting_update,
        description="Sun elevation in night mode; slightly below the horizon keeps a twilight glow")
    sun_energy_day: bpy.props.FloatProperty(
        name="Sky Strength (Day)", default=1.0, min=0.0, max=10.0, update=_on_sun_setting_update,
        description="Brightness of the sky world in day mode")
    sun_energy_night: bpy.props.FloatProperty(
        name="Sky Strength (Night)", default=0.05, min=0.0, max=10.0, update=_on_sun_setting_update,
        description="Brightness of the sky world in night mode")
    # Night defaults retuned for v0.10's AgX/exposure pipeline: +1 EV at
    # night, a lower window glow (0.25 clipped every lit pane to white) and
    # stronger lamps so their pools actually read on the ground.
    window_emission_strength: bpy.props.FloatProperty(
        name="Window Glow", default=0.35, min=0.0, max=5.0, update=_on_window_emission_update,
        description="Emission strength of the lit window panes in night mode")
    street_lamp_energy: bpy.props.FloatProperty(
        name="Street Lamp Energy", default=1200.0, min=0.0, max=5000.0, update=_on_street_lamp_energy_update,
        description="Power (W) of the point light at every street lamp in night mode")
    lighting_preset: bpy.props.EnumProperty(
        name="Lighting Preset",
        description="Sets sun elevation/rotation, sky strength and film exposure together; "
                    "the fields below stay editable afterwards",
        items=(
            ("AFTERNOON", "Afternoon", "Mid-height sun, neutral daylight (the default look)"),
            ("MIDDAY", "Midday", "High sun, short shadows"),
            ("GOLDEN_HOUR", "Golden Hour", "Low warm sun, long shadows, warm haze"),
            ("OVERCAST_SOFT", "Soft Overcast", "Dimmer sky, lower contrast"),
        ),
        default="AFTERNOON", update=_on_lighting_preset_update)
    haze_strength: bpy.props.FloatProperty(
        name="Haze", default=0.3, min=0.0, max=1.0,
        description="Aerial perspective: how strongly distant geometry fades toward the sky color",
        update=_on_sun_setting_update)
    exposure_day: bpy.props.FloatProperty(
        name="Exposure (Day)", default=-2.7, min=-8.0, max=4.0,
        description="Film exposure (EV) applied in day mode",
        update=_on_sun_setting_update)
    exposure_night: bpy.props.FloatProperty(
        name="Exposure (Night)", default=1.0, min=-8.0, max=4.0,
        description="Film exposure (EV) applied in night mode",
        update=_on_sun_setting_update)

    # Custom presets (config/user_presets.py): JSON files in the user config dir.
    user_preset_name: bpy.props.StringProperty(
        name="Preset Name", default="My Preset",
        description="Name for saving the current settings as a custom preset (letters, digits, space, - _ .)")
    user_preset_choice: bpy.props.EnumProperty(
        name="Saved Preset", items=_user_preset_items,
        description="A custom preset saved earlier; Load applies it, the trash button deletes its file")

    export_directory: bpy.props.StringProperty(
        name="Export Directory", subtype='DIR_PATH', default="//procgen_maps_export/",
        description="Where exports and showcase renders are written ('//' is next to the .blend file)")
    # Game-engine export (exporters/game_export.py): all applied inside the
    # glTF export only, the Blender scene itself is left as it is.
    export_collision: bpy.props.BoolProperty(
        name="Collision Meshes", default=True,
        description="Box colliders for every building and prop, trimesh colliders for ground, streets, "
                    "bridges and dungeon rooms (named for the engine's importer, see Naming)")
    export_naming: bpy.props.EnumProperty(
        name="Naming",
        items=(("GODOT", "Godot", "-col / -colonly / -navmesh suffixes Godot's scene importer turns into "
                                  "StaticBody3D colliders and NavigationRegion3D"),
               ("UNITY", "Unity", "_col suffix under a Collision group, _LOD0/_LOD1 siblings for automatic "
                                  "LODGroups, a _navmesh walkable mesh")),
        default="GODOT",
        description="Which game engine's import conventions the glTF's object names follow")
    export_lods: bpy.props.BoolProperty(
        name="LODs", default=True,
        description="Every building becomes <name>/<name>_LOD0 (full facade) + _LOD1 (box), every prop "
                    "instance expands to its high and low detail meshes as _LOD0/_LOD1")
    export_walkable: bpy.props.BoolProperty(
        name="Walkable Ground", default=True,
        description="One navmesh-friendly mesh per map: downsampled terrain + streets + park paths "
                    "(Ground_Walkable) and the dungeon floors (Dungeon_Walkable)")

    showcase_angle: bpy.props.EnumProperty(
        name="Angle",
        items=(
            ("overview", "Overview", "Wide, elevated three-quarter view of the whole generated area"),
            ("close", "Close-up", "Zoomed in on one corner/building cluster"),
            ("low", "Low Angle", "Low, more dramatic street-level-ish angle"),
        ),
        default="overview",
        description="Where the auto-framed showcase camera stands")
    showcase_width: bpy.props.IntProperty(
        name="Width", default=3840, min=64, max=8192, description="Showcase render width in pixels")
    showcase_height: bpy.props.IntProperty(
        name="Height", default=2160, min=64, max=8192, description="Showcase render height in pixels")
    showcase_samples: bpy.props.IntProperty(
        name="Samples", default=128, min=1, max=4096,
        description="Cycles samples per pixel for the showcase/interior renders (denoised, so 64-256 "
                    "is plenty; lower it for quick previews)")

    stat_objects: bpy.props.IntProperty(name="Objects", default=0, description="Objects created by the last generate")
    stat_vertices: bpy.props.IntProperty(name="Vertices", default=0, description="Mesh vertices created by the last generate")
    stat_faces: bpy.props.IntProperty(name="Faces", default=0, description="Mesh faces created by the last generate")
    stat_generate_seconds: bpy.props.FloatProperty(
        name="Last Generate Seconds", default=0.0, description="Wall-clock time of the last generate")


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
