"""N-panel UI under View3D > Sidebar > Procgen Maps.

One parent panel (preset, seed, Generate All, the last message) and a
sub-panel per concern: Generate (what to build), Lighting, Custom
Presets, Showcase Render, Export, Validate & Stats. Every property the
panels show carries a description (its tooltip) in ui/__init__.py.
"""
import bpy

from ..utils._registry import register_classes, unregister_classes

_CATEGORY = "Procgen Maps"


class _ProcgenPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY


class PROCGEN_PT_main_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Procgen Maps"
    bl_idname = "PROCGEN_PT_main_panel"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps

        layout.prop(settings, "preset")
        row = layout.row(align=True)
        row.prop(settings, "seed")
        row.operator("procgen_maps.randomize_seed", text="", icon='FILE_REFRESH')
        layout.operator("procgen_maps.generate_all", icon='PLAY')
        if settings.last_message:
            layout.label(text=settings.last_message, icon='INFO')


class PROCGEN_PT_generate_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Generate"
    bl_idname = "PROCGEN_PT_generate_panel"
    bl_parent_id = "PROCGEN_PT_main_panel"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps

        col = layout.column(align=True)
        col.label(text="City options")
        col.prop(settings, "use_terrain")
        row = col.row(align=True)
        row.prop(settings, "enable_parks", toggle=True)
        row.prop(settings, "enable_commercial", toggle=True)
        row = col.row(align=True)
        row.prop(settings, "enable_cars", toggle=True)
        row.prop(settings, "enable_water", toggle=True)

        col = layout.column(align=True)
        col.operator("procgen_maps.generate_terrain", icon='MESH_GRID')
        col.operator("procgen_maps.generate_city", icon='HOME')

        col = layout.column(align=True)
        col.label(text="Dungeon")
        row = col.row(align=True)
        row.prop(settings, "dungeon_theme", text="")
        row.prop(settings, "dungeon_levels", text="Levels")
        col.operator("procgen_maps.generate_dungeon", icon='MOD_BUILD')

        layout.separator()
        layout.prop(settings, "night_mode", toggle=True, icon='LIGHT')


class PROCGEN_PT_lighting_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Lighting"
    bl_idname = "PROCGEN_PT_lighting_panel"
    bl_parent_id = "PROCGEN_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps

        layout.prop(settings, "lighting_preset")
        col = layout.column(align=True)
        col.label(text="Sun")
        col.prop(settings, "sun_elevation")
        col.prop(settings, "sun_rotation")
        col = layout.column(align=True)
        col.label(text="Day")
        row = col.row(align=True)
        row.prop(settings, "sun_energy_day", text="Sky")
        row.prop(settings, "exposure_day", text="Exposure")
        col.prop(settings, "haze_strength", slider=True)

        col = layout.column(align=True)
        col.label(text="Night")
        col.prop(settings, "night_sun_elevation", text="Sun Elevation")
        row = col.row(align=True)
        row.prop(settings, "sun_energy_night", text="Sky")
        row.prop(settings, "exposure_night", text="Exposure")
        col.prop(settings, "window_emission_strength")
        col.prop(settings, "street_lamp_energy")


class PROCGEN_PT_user_presets_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Custom Presets"
    bl_idname = "PROCGEN_PT_user_presets_panel"
    bl_parent_id = "PROCGEN_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps

        row = layout.row(align=True)
        row.prop(settings, "user_preset_name", text="")
        row.operator("procgen_maps.save_user_preset", text="Save", icon='FILE_TICK')
        row = layout.row(align=True)
        row.prop(settings, "user_preset_choice", text="")
        row.operator("procgen_maps.load_user_preset", text="Load", icon='IMPORT')
        row.operator("procgen_maps.delete_user_preset", text="", icon='TRASH')
        layout.operator("procgen_maps.open_user_presets_dir", icon='FILE_FOLDER')


class PROCGEN_PT_showcase_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Showcase Render"
    bl_idname = "PROCGEN_PT_showcase_panel"
    bl_parent_id = "PROCGEN_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps
        layout.prop(settings, "showcase_angle")
        row = layout.row(align=True)
        row.prop(settings, "showcase_width")
        row.prop(settings, "showcase_height")
        layout.prop(settings, "showcase_samples")
        layout.operator("procgen_maps.render_showcase", icon='RENDER_STILL')
        layout.separator()
        layout.operator("procgen_maps.render_interior", icon='CAMERA_DATA')
        layout.label(text="Select a building first to target it", icon='INFO')


class PROCGEN_PT_export_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "PROCGEN_PT_export_panel"
    bl_parent_id = "PROCGEN_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps
        layout.prop(settings, "export_directory")
        box = layout.box()
        box.label(text="Game Engine (glTF)", icon='MESH_CUBE')
        box.prop(settings, "export_naming")
        row = box.row(align=True)
        row.prop(settings, "export_collision", toggle=True)
        row.prop(settings, "export_lods", toggle=True)
        box.prop(settings, "export_walkable", toggle=True)
        col = layout.column(align=True)
        col.operator("procgen_maps.export_gltf", icon='EXPORT')
        col.operator("procgen_maps.export_fbx")
        col.operator("procgen_maps.export_usdz")
        col.operator("procgen_maps.export_svg")
        col.operator("procgen_maps.export_json")


class PROCGEN_PT_validate_panel(_ProcgenPanel, bpy.types.Panel):
    bl_label = "Validate & Stats"
    bl_idname = "PROCGEN_PT_validate_panel"
    bl_parent_id = "PROCGEN_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps
        layout.operator("procgen_maps.validate_map", icon='CHECKMARK')
        if settings.validation_summary:
            layout.label(text=settings.validation_summary,
                         icon='CHECKMARK' if settings.validation_summary.startswith("OK") else 'ERROR')
        col = layout.column(align=True)
        col.label(text=f"Objects: {settings.stat_objects:,}")
        col.label(text=f"Vertices: {settings.stat_vertices:,}")
        col.label(text=f"Faces: {settings.stat_faces:,}")
        col.label(text=f"Last generate: {settings.stat_generate_seconds:.2f} s")


classes = (PROCGEN_PT_main_panel, PROCGEN_PT_generate_panel, PROCGEN_PT_lighting_panel,
           PROCGEN_PT_user_presets_panel, PROCGEN_PT_showcase_panel, PROCGEN_PT_export_panel,
           PROCGEN_PT_validate_panel)


def register():
    register_classes(classes)


def unregister():
    unregister_classes(classes)
