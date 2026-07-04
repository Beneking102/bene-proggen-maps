"""Generate + export operators."""
import dataclasses
import os

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
    return terrain_gen.TerrainParams(
        resolution=global_settings.TERRAIN_DEFAULT_RESOLUTION,
        world_size=max(global_settings.TERRAIN_DEFAULT_WORLD_SIZE, preset.radius * 2.5),
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
    settings = context.scene.procgen_maps
    world = context.scene.world
    if settings.night_mode:
        world_mat.set_sun_position(world, sun_elevation=settings.night_sun_elevation,
                                    sun_rotation=settings.sun_rotation)
        world_mat.set_sun_energy(world, settings.sun_energy_night)
    else:
        world_mat.set_sun_position(world, sun_elevation=settings.sun_elevation,
                                    sun_rotation=settings.sun_rotation)
        world_mat.set_sun_energy(world, settings.sun_energy_day)


def apply_window_emission(context, strength: float):
    settings = context.scene.procgen_maps
    city_mat.set_night_mode(settings.night_mode, strength)


def apply_street_lamp_energy(context, energy: float):
    settings = context.scene.procgen_maps
    _update_lamp_lights(settings.night_mode, energy)


def _update_lamp_lights(enabled: bool, energy: float = 400.0):
    for obj in list(bpy.data.objects):
        if obj.get("procgen_maps_asset_id") != "street_lamp":
            continue
        light_name = obj.name + _LAMP_LIGHT_SUFFIX
        light_obj = bpy.data.objects.get(light_name)
        if enabled:
            if light_obj is None:
                light_data = bpy.data.lights.new(light_name, type='POINT')
                light_data.color = (1.0, 0.85, 0.5)
                light_obj = bpy.data.objects.new(light_name, light_data)
                light_obj.location = (obj.location.x, obj.location.y, obj.location.z + 3.8)
                target_collection = obj.users_collection[0] if obj.users_collection else bpy.context.scene.collection
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
    without the user needing to know that Blender-specific quirk."""
    window = context.window
    if window is None or window.screen is None:
        return
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'MATERIAL'


class PROCGEN_OT_generate_terrain(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_terrain"
    bl_label = "Generate Terrain"
    bl_description = "Generate a heightmap terrain mesh for the active preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.procgen_maps
        profiler = Profiler()
        params = _get_terrain_params(context.scene)
        with profiler.stage("terrain"):
            terrain_mat.get_or_create_terrain_material(params.max_height)
            obj = terrain_gen.build_terrain_mesh(params, _root_collection())
            mat = bpy.data.materials.get(terrain_mat.TERRAIN_MATERIAL_NAME)
            if mat is not None and mat.name not in obj.data.materials.keys():
                obj.data.materials.append(mat)
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
        self.report({'INFO'}, f"Terrain generated ({params.resolution}x{params.resolution})")
        return {'FINISHED'}


class PROCGEN_OT_generate_city(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_city"
    bl_label = "Generate City"
    bl_description = "Generate the full city (layout, zones, streets, buildings, props) for the active preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.procgen_maps
        preset = _apply_toggle_overrides(preset_config.get_preset(settings.preset), settings)
        terrain_params = _get_terrain_params(context.scene) if settings.use_terrain else None

        profiler = Profiler()
        city_gen.clear_city()
        with profiler.stage("city"):
            city_mat.get_or_create_city_material()
            result = city_gen.generate_city(preset, terrain_params=terrain_params, seed=settings.seed,
                                             parent_collection=_root_collection())
            _assign_city_material(result["building_objects"])

        all_objects = (result["street_objects"] + result["building_objects"]
                       + result["building_extra_objects"] + result["prop_objects"] + result["sign_objects"])
        _write_stats(context.scene, profiler, all_objects)
        _switch_viewport_to_material_preview(context)
        self.report({'INFO'}, f"City generated: {len(result['building_objects'])} buildings, "
                               f"{len(result['prop_objects'])} props, {len(result['sign_placements'])} signs")
        return {'FINISHED'}


class PROCGEN_OT_generate_dungeon(bpy.types.Operator):
    bl_idname = "procgen_maps.generate_dungeon"
    bl_label = "Generate Dungeon"
    bl_description = "Generate a BSP dungeon (rooms + corridors), independent of the city/terrain generators"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from ..generators import dungeon as dungeon_gen

        settings = context.scene.procgen_maps
        params = dungeon_gen.DungeonParams(seed=settings.seed)

        profiler = Profiler()
        dungeon_gen.clear_dungeon()
        with profiler.stage("dungeon"):
            layout = dungeon_gen.generate_dungeon(params)
            objects = dungeon_gen.build_dungeon_mesh(layout, _root_collection())
        _write_stats(context.scene, profiler, objects)
        _switch_viewport_to_material_preview(context)
        self.report({'INFO'}, f"Dungeon generated: {len(layout.rooms)} rooms")
        return {'FINISHED'}


class PROCGEN_OT_export_gltf(bpy.types.Operator):
    bl_idname = "procgen_maps.export_gltf"
    bl_label = "Export glTF"
    bl_description = "Export the generated ProcgenMaps collection to a .glb file"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..exporters import gltf_export
        filepath = _resolve_export_path(context, "procgen_maps.glb")
        gltf_export.export_gltf(filepath)
        self.report({'INFO'}, f"Exported glTF to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_fbx(bpy.types.Operator):
    bl_idname = "procgen_maps.export_fbx"
    bl_label = "Export FBX"
    bl_description = "Export the generated ProcgenMaps collection to a .fbx file"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..exporters import fbx_export
        if not fbx_export.is_fbx_available():
            self.report({'ERROR'}, "Blender's built-in FBX add-on (io_scene_fbx) is disabled - "
                                    "enable it in Preferences > Add-ons.")
            return {'CANCELLED'}
        filepath = _resolve_export_path(context, "procgen_maps.fbx")
        fbx_export.export_fbx(filepath)
        self.report({'INFO'}, f"Exported FBX to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_usdz(bpy.types.Operator):
    bl_idname = "procgen_maps.export_usdz"
    bl_label = "Export USDZ"
    bl_description = "Export the generated ProcgenMaps collection to a .usdz file (best-effort AR export)"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..exporters import usdz_export
        filepath = _resolve_export_path(context, "procgen_maps.usdz")
        usdz_export.export_usdz(filepath)
        self.report({'INFO'}, f"Exported USDZ to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_svg(bpy.types.Operator):
    bl_idname = "procgen_maps.export_svg"
    bl_label = "Export SVG Map"
    bl_description = "Export a top-down 2D SVG map of the generated city"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..exporters import svg_export
        filepath = _resolve_export_path(context, "procgen_maps.svg")
        root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
        objects = _collect_mesh_objects(root) if root else []
        svg_export.export_svg(filepath, objects)
        self.report({'INFO'}, f"Exported SVG map to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_export_json(bpy.types.Operator):
    bl_idname = "procgen_maps.export_json"
    bl_label = "Export JSON Metadata"
    bl_description = "Export generation metadata (stats, seed, preset) as JSON"
    bl_options = {'REGISTER'}

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
        self.report({'INFO'}, f"Exported JSON metadata to {filepath}")
        return {'FINISHED'}


class PROCGEN_OT_render_showcase(bpy.types.Operator):
    bl_idname = "procgen_maps.render_showcase"
    bl_label = "Render Showcase Image"
    bl_description = ("Auto-frame a camera on the generated content and render a high-quality "
                       "still (procedural sky, raytraced glass/materials)")
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..rendering import framing, showcase

        root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
        if root is None:
            self.report({'ERROR'}, "Nothing generated yet - generate terrain or a city first")
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
        showcase.render_showcase(filepath, resolution=(settings.showcase_width, settings.showcase_height))

        self.report({'INFO'}, f"Showcase render saved to {filepath}")
        return {'FINISHED'}


classes = (
    PROCGEN_OT_generate_terrain,
    PROCGEN_OT_generate_city,
    PROCGEN_OT_generate_dungeon,
    PROCGEN_OT_export_gltf,
    PROCGEN_OT_export_fbx,
    PROCGEN_OT_export_usdz,
    PROCGEN_OT_export_svg,
    PROCGEN_OT_export_json,
    PROCGEN_OT_render_showcase,
)


def register():
    register_classes(classes)


def unregister():
    unregister_classes(classes)
