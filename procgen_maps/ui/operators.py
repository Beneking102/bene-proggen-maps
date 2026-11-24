"""Generate, export, render, validate and custom-preset operators.

Every operator's `execute` is wrapped by `_safe_execute`: an unexpected
exception becomes one clean ERROR report in the UI (the traceback goes to
the system console) instead of a raw Python traceback popup, and the
panel's "last message" line always says what happened.
"""
import dataclasses
import functools
import os
import time
import traceback

import bpy

from ..config import presets as preset_config
from ..config import settings as global_settings
from ..generators import city as city_gen
from ..generators import terrain as terrain_gen
from ..materials import city_mat, terrain_mat, world_mat
from ..utils._registry import register_classes, unregister_classes
from ..utils.profiler import Profiler, count_scene_stats

ROOT_COLLECTION_NAME = "ProcgenMaps"
_LAMP_LIGHT_SUFFIX = "_light"


def _get_terrain_params(scene) -> terrain_gen.TerrainParams:
    settings = scene.procgen_maps
    preset = preset_config.get_preset(settings.preset)
    # Big presets need ground well past the city (the forest ring reaches
    # 1.9x radius, and a low camera angle otherwise sees the terrain edge
    # against the sky). Resolution grows with the world so the cell size
    # stays ~2 m - see config/settings.py for why that matters.
    world_size = max(global_settings.TERRAIN_DEFAULT_WORLD_SIZE, preset.radius * 4.2)
    resolution = min(global_settings.TERRAIN_MAX_RESOLUTION,
                     int(round(global_settings.TERRAIN_DEFAULT_RESOLUTION
                               * world_size / global_settings.TERRAIN_DEFAULT_WORLD_SIZE)))
    return terrain_gen.TerrainParams(
        resolution=resolution,
        world_size=world_size,
        scale=global_settings.TERRAIN_DEFAULT_SCALE,
        octaves=global_settings.TERRAIN_DEFAULT_OCTAVES,
        persistence=global_settings.TERRAIN_DEFAULT_PERSISTENCE,
        lacunarity=global_settings.TERRAIN_DEFAULT_LACUNARITY,
        max_height=global_settings.TERRAIN_DEFAULT_MAX_HEIGHT,
        seed=settings.seed,
    )


def _root_collection():
    scene = bpy.context.scene
    existing = bpy.data.collections.get(ROOT_COLLECTION_NAME)
    if existing is None:
        existing = bpy.data.collections.new(ROOT_COLLECTION_NAME)
    if scene.collection.children.get(ROOT_COLLECTION_NAME) is None:
        scene.collection.children.link(existing)
    return existing


def _write_stats(scene, profiler, objects):
    stats = count_scene_stats(objects)
    settings = scene.procgen_maps
    settings.stat_objects = stats["objects"]
    settings.stat_vertices = stats["vertices"]
    settings.stat_faces = stats["faces"]
    settings.stat_generate_seconds = profiler.total_time()


def _safe_execute(label):
    """Decorator for Operator.execute: report what the operator did (or
    why it failed) in the UI and the panel, never let a traceback reach
    the user. The wrapped execute returns its own status set; it may call
    self.report itself for INFO/WARNING messages."""
    def decorate(execute):
        @functools.wraps(execute)
        def wrapper(self, context):
            settings = getattr(context.scene, "procgen_maps", None)
            try:
                result = execute(self, context)
            except Exception as exc:   # noqa: BLE001 - the whole point is a clean report for any error
                traceback.print_exc()
                message = f"{label} failed: {exc.__class__.__name__}: {exc}"
                self.report({'ERROR'}, message[:400])
                if settings is not None:
                    settings.last_message = f"{label} failed - see the system console"
                return {'CANCELLED'}
            return result
        return wrapper
    return decorate


def _say(self, context, message, level='INFO'):
    """Report `message` and keep it as the panel's last-message line."""
    self.report({level}, message)
    context.scene.procgen_maps.last_message = message


def _apply_toggle_overrides(preset, settings):
    """Return a copy of `preset` with the panel's on/off toggles applied,
    without mutating the shared PRESETS dict entry."""
    zone_ratios = dict(preset.zone_ratios)
    if not settings.enable_parks:
        zone_ratios["park"] = 0.0
    if not settings.enable_commercial:
        zone_ratios["commercial"] = 0.0
    return dataclasses.replace(
        preset,
        cars_enabled=settings.enable_cars and preset.cars_enabled,
        zone_ratios=zone_ratios,
        water=preset.water if settings.enable_water else "none",
    )


def _assign_city_material(building_objects):
    """Fill mesh material slot 0 (left empty by
    generators.city.buildings._build_single_building) with the shared wall
    facade material. Slot 1 (window panes) is already filled per-object."""
    mat = bpy.data.materials.get(city_mat.CITY_MATERIAL_NAME)
    if mat is None:
        return
    for obj in building_objects:
        if len(obj.data.materials) > 0:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)


def apply_night_mode(context, enabled: bool):
    """Toggle emissive building windows, street-lamp point lights, and dip
    the procedural sky's sun below the horizon (reusing the same Nishita
    sky rather than swapping to a separate flat-color night world, so the
    horizon glow/stars-less dusk tone comes from the same physical model)."""
    settings = context.scene.procgen_maps
    city_mat.set_night_mode(enabled, settings.window_emission_strength)
    _update_lamp_lights(enabled, settings.street_lamp_energy)
    apply_sun_settings(context)


def apply_sun_settings(context):
    """Single source of truth for 'what should the world's sun currently
    look like' - reconciles the live sun_elevation/sun_rotation/energy
    Lighting-panel fields (ui/__init__.py) against whichever mode
    (day/night) is currently active. Called by apply_night_mode and by
    every sun-field's live update= callback, so dragging any one of them
    always re-applies the full, currently-correct set."""
    from ..materials import atmosphere

    settings = context.scene.procgen_maps
    world = context.scene.world
    if settings.night_mode:
        world_mat.set_sun_position(world, sun_elevation=settings.night_sun_elevation,
                                    sun_rotation=settings.sun_rotation)
        world_mat.set_sun_energy(world, settings.sun_energy_night)
        context.scene.view_settings.exposure = settings.exposure_night
        # At night the haze is the dark sky, not a bright veil.
        atmosphere.set_haze(settings.haze_strength * 0.5, (0.02, 0.025, 0.04, 1.0))
    else:
        world_mat.set_sun_position(world, sun_elevation=settings.sun_elevation,
                                    sun_rotation=settings.sun_rotation)
        world_mat.set_sun_energy(world, settings.sun_energy_day)
        context.scene.view_settings.exposure = settings.exposure_day
        atmosphere.set_haze(settings.haze_strength, atmosphere.haze_color_for_sun(settings.sun_elevation))
    _apply_view_transform(context.scene)


def _apply_view_transform(scene):
    """AgX with a bit more contrast than its base look - the procedural
    sky/sun are physically bright, and the base AgX curve on top of that
    reads flat and milky (the washed-out look of every pre-0.10 render)."""
    view = scene.view_settings
    for transform in ("AgX", "Filmic"):
        try:
            view.view_transform = transform
            break
        except TypeError:
            continue
    for look in ("AgX - Medium High Contrast", "Medium High Contrast", "None"):
        try:
            view.look = look
            break
        except TypeError:
            continue


def apply_lighting_preset(context, key: str):
    """Write a LIGHTING_PRESETS entry into the settings and re-apply the sun."""
    from . import LIGHTING_PRESETS

    settings = context.scene.procgen_maps
    elevation, rotation, energy, exposure = LIGHTING_PRESETS[key]
    # Writing these fields triggers their own update callbacks, each of
    # which re-applies the full sun state - harmless, just redundant.
    settings.sun_elevation = elevation
    settings.sun_rotation = rotation
    settings.sun_energy_day = energy
    settings.exposure_day = exposure
    apply_sun_settings(context)


def apply_window_emission(context, strength: float):
    """Retune the window material's night glow strength."""
    settings = context.scene.procgen_maps
    city_mat.set_night_mode(settings.night_mode, strength)


def apply_street_lamp_energy(context, energy: float):
    """Retune every street lamp light's power."""
    settings = context.scene.procgen_maps
    _update_lamp_lights(settings.night_mode, energy)


def _first_owner_collections():
    """name -> the first Collection (in bpy.data.collections order, then the
    scene's master collection) containing each object. Built once per
    night-mode toggle: `obj.users_collection` is implemented in Python as a
    scan over every collection's objects, so calling it per street lamp
    cost ~0.3 ms x thousands of lamps - half the toggle's time."""
    owner = {}
    for collection in bpy.data.collections:
        for obj in collection.objects:
            owner.setdefault(obj.name, collection)
    for obj in bpy.context.scene.collection.objects:
        owner.setdefault(obj.name, bpy.context.scene.collection)
    return owner


def _update_lamp_lights(enabled: bool, energy: float = 400.0):
    all_objects = list(bpy.data.objects)
    # One dict instead of a bpy.data.objects.get() RNA lookup per lamp.
    by_name = {obj.name: obj for obj in all_objects}
    owner_collections = None
    for obj in all_objects:
        if obj.get("procgen_maps_asset_id") != "street_lamp":
            continue
        light_name = obj.name + _LAMP_LIGHT_SUFFIX
        light_obj = by_name.get(light_name)
        if enabled:
            if light_obj is None:
                if owner_collections is None:
                    owner_collections = _first_owner_collections()
                light_data = bpy.data.lights.new(light_name, type='POINT')
                light_data.color = (1.0, 0.85, 0.5)
                light_obj = bpy.data.objects.new(light_name, light_data)
                light_obj.location = (obj.location.x, obj.location.y, obj.location.z + 3.8)
                target_collection = owner_collections.get(obj.name, bpy.context.scene.collection)
                target_collection.objects.link(light_obj)
            light_obj.data.energy = energy
            light_obj.hide_viewport = False
            light_obj.hide_render = False
        elif light_obj is not None:
            # Still refresh energy on an existing-but-hidden light so a
            # live slider drag isn't silently lost until the next toggle.
            light_obj.data.energy = energy
            light_obj.hide_viewport = True
            light_obj.hide_render = True


def _resolve_export_path(context, filename):
    settings = context.scene.procgen_maps
    directory = bpy.path.abspath(settings.export_directory or "//procgen_maps_export/")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


def _collect_mesh_objects(collection):
    objects = [obj for obj in collection.objects if obj.type == 'MESH']
    for child in collection.children:
        objects.extend(_collect_mesh_objects(child))
    return objects


def _switch_viewport_to_material_preview(context):
    """Blender's default 'Solid' viewport shading ignores shader node graphs
    entirely (it only reads material.diffuse_color) - switch to 'Material
    Preview' so generated buildings/terrain/props show their real colors
    without the user needing to know that Blender-specific quirk.

    Material Preview's own default lighting is a generic bundled studio
    HDRI, not this scene's actual Nishita sky world - the facade/terrain
    materials' subtle noise-driven grain and bump (materials/city_mat.py,
    terrain_mat.py) were tuned to look right under real sunlight, and can
    read as harsh/blotchy dark mottling under that mismatched default
    preview lighting even though the same materials render correctly
    (confirmed via every render in this project) once real scene lighting
    is used. use_scene_world makes the viewport preview actually use the
    Nishita sky this addon sets up, so what's on screen matches renders."""
    window = context.window
    if window is None or window.screen is None:
        return
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'MATERIAL'
                    space.shading.use_scene_world = True


def _enable_high_quality_rendering(context):
    """Window glass (materials/city_mat.py's get_or_create_window_material)
    only renders as transparent - showing the furnished interiors behind it
    - with EEVEE Next's raytracing enabled; previously this was only ever
    turned on inside rendering/showcase.py's one-off showcase render, so
    every window looked like an opaque flat panel and no interior was ever
    visible just from generating and looking around. Set once here so it's
    on by default the moment there's anything to look at, in both the
    viewport and any F12 render - not just the dedicated showcase button."""
    try:
        context.scene.eevee.use_raytracing = True
    except AttributeError:
        pass  # older/non-EEVEE-Next engine without this setting - nothing to enable


class _ProgressReporter:
    """Drives the window manager's progress cursor (the percentage next to
    the mouse pointer) while a long generate runs (a no-op headless), and
    prints one timed line per stage to the system console."""

    def __init__(self, context, verbose=True):
        self._wm = getattr(context, "window_manager", None)
        self._active = False
        self._verbose = verbose
        self._started = time.perf_counter()
        self._last = self._started
        try:
            if self._wm is not None and context.window is not None:
                self._wm.progress_begin(0, 100)
                self._active = True
        except (AttributeError, RuntimeError):
            self._active = False

    def __call__(self, fraction, label):
        if self._active:
            self._wm.progress_update(int(fraction * 100))
        if self._verbose:
            now = time.perf_counter()
            print(f"[Procgen Maps] {int(fraction * 100):3d}% {label} (+{now - self._last:.2f} s, "
                  f"{now - self._started:.2f} s total)", flush=True)
            self._last = now

    def end(self):
        if self._active:
            self._wm.progress_end()
            self._active = False


class PROCGEN_OT_generate_terrain(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_terrain"
    bl_label = "Generate Terrain"
    bl_description = ("Generate the terrain for the active preset and seed: a heightmap mesh with the "
                      "countryside patchwork (fields, hedgerows, woods) and the horizon, plus the sky")
    bl_options = {'REGISTER', 'UNDO'}

    @_safe_execute("Generate Terrain")
    def execute(self, context):
        settings = context.scene.procgen_maps
        profiler = Profiler()
        params = _get_terrain_params(context.scene)
        preset = preset_config.get_preset(settings.preset)
        with profiler.stage("terrain"):
            mat = terrain_mat.get_or_create_terrain_material(params.max_height, preset.radius)
            terrain_mat.set_terrain_biomes(
                preset.radius, terrain_mat.snow_line_height(terrain_gen.generate_heightmap(params)))
            obj = terrain_gen.build_terrain_mesh(params, _root_collection())
            if mat.name not in obj.data.materials.keys():
                obj.data.materials.append(mat)
            # Fields, hedgerows and woods for the shader (generators/landscape.py),
            # and the horizon skirt so the map does not end in a cliff.
            terrain_gen.write_landscape_attributes(obj, params, preset.radius, settings.seed)
            terrain_gen.build_horizon_mesh(params, _root_collection(), material=mat)
            context.scene.world = world_mat.get_or_create_sky_world(
                sun_elevation=settings.sun_elevation, sun_rotation=settings.sun_rotation,
                sun_energy=settings.sun_energy_day)
            # get_or_create_sky_world only configures the sky on first
            # creation (it returns the existing world unchanged on later
            # calls) - apply_sun_settings is what actually guarantees the
            # current Lighting-panel values (day or night, whichever mode
            # is active) are reapplied every time terrain is regenerated.
            apply_sun_settings(context)
        _write_stats(context.scene, profiler, [obj])
        _switch_viewport_to_material_preview(context)
        _enable_high_quality_rendering(context)
        _say(self, context, f"Terrain generated ({params.resolution}x{params.resolution}, "
                            f"{params.world_size:.0f} m, {profiler.total_time():.1f} s)")
        return {'FINISHED'}


class PROCGEN_OT_generate_city(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_city"
    bl_label = "Generate City"
    bl_description = ("Generate the full city for the active preset and seed (layout, zones, streets, "
                      "buildings, props, signs, river, country road and village); replaces an existing city")
    bl_options = {'REGISTER', 'UNDO'}

    @_safe_execute("Generate City")
    def execute(self, context):
        settings = context.scene.procgen_maps
        preset = _apply_toggle_overrides(preset_config.get_preset(settings.preset), settings)
        terrain_params = _get_terrain_params(context.scene) if settings.use_terrain else None
        if terrain_params is not None and bpy.data.objects.get(terrain_gen.TERRAIN_OBJECT_NAME) is None:
            self.report({'WARNING'}, "No terrain in the scene - the city follows the heightfield anyway; "
                                     "use Generate All (or Generate Terrain first) to see the ground")

        profiler = Profiler()
        city_gen.clear_city()
        progress = _ProgressReporter(context)
        try:
            with profiler.stage("city"):
                city_mat.get_or_create_city_material()
                result = city_gen.generate_city(preset, terrain_params=terrain_params, seed=settings.seed,
                                                 parent_collection=_root_collection(), progress=progress)
                _assign_city_material(result["building_objects"])
        finally:
            progress.end()

        all_objects = (result["street_objects"] + result["building_objects"]
                       + result["building_extra_objects"] + result["prop_objects"] + result["sign_objects"]
                       + result["parking_objects"])
        _write_stats(context.scene, profiler, all_objects)
        _switch_viewport_to_material_preview(context)
        _enable_high_quality_rendering(context)
        _say(self, context, f"City generated: {len(result['building_objects'])} buildings, "
                            f"{len(result['prop_objects'])} props, {len(result['sign_placements'])} signs "
                            f"({profiler.total_time():.1f} s)")
        return {'FINISHED'}


class PROCGEN_OT_generate_dungeon(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_dungeon"
    bl_label = "Generate Dungeon"
    bl_description = ("Generate a BSP dungeon (rooms, corridors, doors, stairs between levels, torches) in "
                      "the chosen theme; independent of the city and terrain")
    bl_options = {'REGISTER', 'UNDO'}

    @_safe_execute("Generate Dungeon")
    def execute(self, context):
        from ..generators import dungeon as dungeon_gen

        settings = context.scene.procgen_maps
        params = dungeon_gen.DungeonParams(seed=settings.seed, theme=settings.dungeon_theme,
                                           levels=settings.dungeon_levels)

        profiler = Profiler()
        dungeon_gen.clear_dungeon()
        with profiler.stage("dungeon"):
            layout = dungeon_gen.generate_dungeon(params)
            objects = dungeon_gen.build_dungeon_mesh(layout, _root_collection())
        _write_stats(context.scene, profiler, objects)
        _switch_viewport_to_material_preview(context)
        _say(self, context, f"Dungeon generated: {len(layout.rooms)} rooms on {layout.levels} level(s), "
                            f"{len(layout.stairs)} staircase(s), theme {layout.theme}")
        return {'FINISHED'}


class PROCGEN_OT_generate_all(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_all"
    bl_label = "Generate All"
    bl_description = ("Generate the terrain (if Terrain is on) and then the city in one go; the dungeon "
                      "stays a separate button")
    bl_options = {'REGISTER', 'UNDO'}

    @_safe_execute("Generate All")
    def execute(self, context):
        settings = context.scene.procgen_maps
        if settings.use_terrain and bpy.ops.procgen_maps.generate_terrain() != {'FINISHED'}:
            return {'CANCELLED'}
        return bpy.ops.procgen_maps.generate_city()


class PROCGEN_OT_randomize_seed(bpy.types.Operator):
    bl_idname = "procgen_maps.randomize_seed"
    bl_label = "Randomize Seed"
    bl_description = "Pick a new random seed (regenerate afterwards to see it)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import random

        context.scene.procgen_maps.seed = random.randint(0, 99999)
        return {'FINISHED'}


class PROCGEN_OT_save_user_preset(bpy.types.Operator):
    bl_idname = "procgen_maps.save_user_preset"
    bl_label = "Save Custom Preset"
    bl_description = ("Save the current generation, lighting, showcase and export settings as a named JSON "
                      "preset in Blender's user config folder (overwrites a preset of the same name)")
    bl_options = {'REGISTER'}

    @_safe_execute("Save Custom Preset")
    def execute(self, context):
        from ..config import user_presets

        settings = context.scene.procgen_maps
        name = user_presets.safe_preset_name(settings.user_preset_name)
        path = user_presets.save_preset(name, user_presets.settings_to_dict(settings), user_presets.user_presets_dir())
        settings.user_preset_choice = name
        _say(self, context, f"Saved custom preset '{name}' to {path}")
        return {'FINISHED'}


class PROCGEN_OT_load_user_preset(bpy.types.Operator):
    bl_idname = "procgen_maps.load_user_preset"
    bl_label = "Load Custom Preset"
    bl_description = "Apply the chosen custom preset's settings (regenerate afterwards to see them)"
    bl_options = {'REGISTER', 'UNDO'}

    @_safe_execute("Load Custom Preset")
    def execute(self, context):
        from ..config import user_presets

        settings = context.scene.procgen_maps
        name = settings.user_preset_choice
        if not name:
            _say(self, context, "No custom preset chosen - save one first", 'WARNING')
            return {'CANCELLED'}
        try:
            data = user_presets.load_preset(name, user_presets.user_presets_dir(create=False))
        except FileNotFoundError:
            _say(self, context, f"Custom preset '{name}' no longer exists", 'WARNING')
            return {'CANCELLED'}
        except ValueError as exc:
            _say(self, context, str(exc), 'WARNING')
            return {'CANCELLED'}
        applied = user_presets.dict_to_settings(settings, data)
        settings.user_preset_name = name
        apply_sun_settings(context)
        _say(self, context, f"Loaded custom preset '{name}' ({len(applied)} settings)")
        return {'FINISHED'}


class PROCGEN_OT_delete_user_preset(bpy.types.Operator):
    bl_idname = "procgen_maps.delete_user_preset"
    bl_label = "Delete Custom Preset"
    bl_description = "Delete the chosen custom preset's file"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    @_safe_execute("Delete Custom Preset")
    def execute(self, context):
        from ..config import user_presets

        settings = context.scene.procgen_maps
        name = settings.user_preset_choice
        if not name:
            _say(self, context, "No custom preset chosen", 'WARNING')
            return {'CANCELLED'}
        if user_presets.delete_preset(name, user_presets.user_presets_dir(create=False)):
            _say(self, context, f"Deleted custom preset '{name}'")
        else:
            _say(self, context, f"Custom preset '{name}' was already gone", 'WARNING')
        return {'FINISHED'}


class PROCGEN_OT_open_user_presets_dir(bpy.types.Operator):
    bl_idname = "procgen_maps.open_user_presets_dir"
    bl_label = "Open Presets Folder"
    bl_description = "Open the folder the custom presets are stored in (JSON files you can copy or share)"
    bl_options = {'REGISTER'}

    @_safe_execute("Open Presets Folder")
    def execute(self, context):
        from ..config import user_presets

        directory = user_presets.user_presets_dir()
        bpy.ops.wm.path_open(filepath=directory)
        _say(self, context, f"Custom presets live in {directory}")
        return {'FINISHED'}


class PROCGEN_OT_export_gltf(bpy.types.Operator):
    bl_idname = "procgen_maps.export_gltf"
    bl_label = "Export glTF"
    bl_description = ("Export the generated map to a game-ready .glb: City/Terrain/Dungeon hierarchy, "
                      "collision meshes, LODs and a walkable ground mesh per the Export panel's options")
    bl_options = {'REGISTER'}

    @_safe_execute("Export glTF")
    def execute(self, context):
        from ..exporters import game_export
        settings = context.scene.procgen_maps
        if bpy.data.collections.get(ROOT_COLLECTION_NAME) is None and \
                bpy.data.collections.get(city_gen.CITY_ROOT_COLLECTION_NAME) is None:
            _say(self, context, "Nothing generated yet - generate terrain, a city or a dungeon first", 'ERROR')
            return {'CANCELLED'}
        filepath = _resolve_export_path(context, "procgen_maps.glb")
        options = game_export.GameExportOptions(
            collision=settings.export_collision, naming=settings.export_naming,
            lods=settings.export_lods, walkable=settings.export_walkable)
        summary = game_export.export_game_gltf(filepath, options)
        _say(self, context, f"Exported glTF to {filepath} ({summary['collision']} colliders, "
                            f"{summary['lods']} LOD groups, {summary['walkable']} walkable meshes)")
        return {'FINISHED'}


class PROCGEN_OT_export_fbx(bpy.types.Operator):
    bl_idname = "procgen_maps.export_fbx"
    bl_label = "Export FBX"
    bl_description = "Export the generated ProcgenMaps collection to a .fbx file"
    bl_options = {'REGISTER'}

    @_safe_execute("Export FBX")
    def execute(self, context):
        from ..exporters import fbx_export
        if not fbx_export.is_fbx_available():
            self.report({'ERROR'}, "Blender's built-in FBX add-on (io_scene_fbx) is disabled - "
                                    "enable it in Preferences > Add-ons.")
            return {'CANCELLED'}
        filepath = _resolve_export_path(context, "procgen_maps.fbx")
        fbx_export.export_fbx(filepath)
        _say(self, context, f"Exported FBX to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_usdz(bpy.types.Operator):
    bl_idname = "procgen_maps.export_usdz"
    bl_label = "Export USDZ"
    bl_description = "Export the generated ProcgenMaps collection to a .usdz file (best-effort AR export)"
    bl_options = {'REGISTER'}

    @_safe_execute("Export USDZ")
    def execute(self, context):
        from ..exporters import usdz_export
        filepath = _resolve_export_path(context, "procgen_maps.usdz")
        usdz_export.export_usdz(filepath)
        _say(self, context, f"Exported USDZ to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_svg(bpy.types.Operator):
    bl_idname = "procgen_maps.export_svg"
    bl_label = "Export SVG Map"
    bl_description = "Export a top-down 2D SVG map of the generated city"
    bl_options = {'REGISTER'}

    @_safe_execute("Export SVG")
    def execute(self, context):
        from ..exporters import svg_export
        filepath = _resolve_export_path(context, "procgen_maps.svg")
        root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
        objects = [obj for obj in (_collect_mesh_objects(root) if root else [])
                   if not obj.get(terrain_gen.HORIZON_PROPERTY)]
        svg_export.export_svg(filepath, objects)
        _say(self, context, f"Exported SVG map to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_json(bpy.types.Operator):
    bl_idname = "procgen_maps.export_json"
    bl_label = "Export JSON Metadata"
    bl_description = "Export generation metadata (stats, seed, preset) as JSON"
    bl_options = {'REGISTER'}

    @_safe_execute("Export JSON")
    def execute(self, context):
        from ..exporters import json_export
        settings = context.scene.procgen_maps
        stats = {
            "preset": settings.preset,
            "seed": settings.seed,
            "objects": settings.stat_objects,
            "vertices": settings.stat_vertices,
            "faces": settings.stat_faces,
            "generate_seconds": settings.stat_generate_seconds,
        }
        filepath = _resolve_export_path(context, "procgen_maps.json")
        json_export.export_json(filepath, stats)
        _say(self, context, f"Exported JSON metadata to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_render_showcase(bpy.types.Operator):
    bl_idname = "procgen_maps.render_showcase"
    bl_label = "Render Showcase Image"
    bl_description = ("Auto-frame a camera on the generated content and render a high-quality "
                       "still (procedural sky, raytraced glass/materials)")
    bl_options = {'REGISTER'}

    @_safe_execute("Render Showcase Image")
    def execute(self, context):
        from ..rendering import framing, showcase

        root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
        if root is None:
            _say(self, context, "Nothing generated yet - generate terrain or a city first", 'ERROR')
            return {'CANCELLED'}

        settings = context.scene.procgen_maps
        bounds_min, bounds_max = showcase.collect_scene_bounds(root)
        plan = framing.compute_framing(bounds_min, bounds_max, angle=settings.showcase_angle)

        showcase.build_showcase_camera(plan)
        sun_elevation = settings.night_sun_elevation if settings.night_mode else settings.sun_elevation
        showcase.ensure_showcase_sun(night_mode=settings.night_mode,
                                      sun_elevation=sun_elevation, sun_rotation=settings.sun_rotation)
        context.scene.camera = bpy.data.objects.get(showcase.CAMERA_NAME)

        filepath = _resolve_export_path(context, "procgen_maps_showcase.png")
        showcase.render_showcase(filepath, resolution=(settings.showcase_width, settings.showcase_height),
                                 samples=settings.showcase_samples)

        _say(self, context, f"Showcase render saved to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_render_interior(bpy.types.Operator):
    bl_idname = "procgen_maps.render_interior"
    bl_label = "Render Interior View (Cycles)"
    bl_description = ("Render a close-up through a building's entrance/windows so its furnished "
                       "ground-floor interior is visible - uses the selected building if one is "
                       "selected (any object tagged as a building shell), otherwise the first one "
                       "found. Uses Cycles rather than EEVEE: a room only visible through its own "
                       "window has no other screen-space data for EEVEE's screen-space transmission "
                       "to draw from, so it renders as a blank wall - real ray tracing doesn't have "
                       "that limitation. Cycles is slower (roughly a minute or two)")
    bl_options = {'REGISTER'}

    @_safe_execute("Render Interior View")
    def execute(self, context):
        from ..rendering import showcase

        target = context.active_object
        if target is None or target.get("procgen_maps_facade_type") is None:
            target = next((obj for obj in bpy.data.objects if obj.get("procgen_maps_facade_type") is not None), None)
        if target is None:
            _say(self, context, "No building found - generate a city first (or select a building)", 'ERROR')
            return {'CANCELLED'}

        settings = context.scene.procgen_maps
        plan = showcase.build_interior_framing(target)
        showcase.build_showcase_camera(plan)
        sun_elevation = settings.night_sun_elevation if settings.night_mode else settings.sun_elevation
        showcase.ensure_showcase_sun(night_mode=settings.night_mode,
                                      sun_elevation=sun_elevation, sun_rotation=settings.sun_rotation)
        context.scene.camera = bpy.data.objects.get(showcase.CAMERA_NAME)

        filepath = _resolve_export_path(context, "procgen_maps_interior.png")
        # A close-up of one building doesn't need the 4K showcase size - cap
        # at 1080p, but honor smaller settings (quick previews, the headless
        # smoke test) and the shared sample count.
        showcase.render_interior_view(
            filepath,
            resolution=(min(settings.showcase_width, 1920), min(settings.showcase_height, 1080)),
            samples=settings.showcase_samples)

        _say(self, context, f"Interior view of {target.name} saved to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_validate_map(bpy.types.Operator):
    bl_idname = "procgen_maps.validate_map"
    bl_label = "Validate Map"
    bl_description = ("Re-plan the current preset/seed and check it for overlaps (buildings, roads, "
                      "props, signs, parking), disconnected streets, floating/buried objects and orphan "
                      "datablocks; summary in the panel, full report in the system console")
    bl_options = {'REGISTER'}

    @_safe_execute("Validate Map")
    def execute(self, context):
        from .. import validation

        settings = context.scene.procgen_maps
        preset = _apply_toggle_overrides(preset_config.get_preset(settings.preset), settings)
        terrain_params = _get_terrain_params(context.scene) if settings.use_terrain else None

        report = None
        if bpy.data.collections.get(city_gen.CITY_ROOT_COLLECTION_NAME) is not None:
            # plan_city is deterministic for (preset, seed), so this is the
            # exact plan the generated city was built from.
            plan = city_gen.plan_city(preset, terrain_params=terrain_params, seed=settings.seed)
            report = validation.validate_city_plan(plan, preset)
        report = validation.validate_scene(terrain_params, report=report)

        text = report.format()
        print("[Procgen Maps] Validate Map\n" + text)
        settings.validation_summary = report.summary()
        _say(self, context, report.summary(), 'INFO' if report.ok else 'WARNING')
        return {'FINISHED'}


classes = (
    PROCGEN_OT_generate_terrain,
    PROCGEN_OT_generate_city,
    PROCGEN_OT_generate_dungeon,
    PROCGEN_OT_generate_all,
    PROCGEN_OT_randomize_seed,
    PROCGEN_OT_save_user_preset,
    PROCGEN_OT_load_user_preset,
    PROCGEN_OT_delete_user_preset,
    PROCGEN_OT_open_user_presets_dir,
    PROCGEN_OT_export_gltf,
    PROCGEN_OT_export_fbx,
    PROCGEN_OT_export_usdz,
    PROCGEN_OT_export_svg,
    PROCGEN_OT_export_json,
    PROCGEN_OT_render_showcase,
    PROCGEN_OT_render_interior,
    PROCGEN_OT_validate_map,
)


def register():
    register_classes(classes)


def unregister():
    unregister_classes(classes)
