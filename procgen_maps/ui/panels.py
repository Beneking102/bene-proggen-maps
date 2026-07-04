"""N-panel UI: preset picker, toggles, generate/export buttons, profiler readout."""
import bpy

from ..utils._registry import register_classes, unregister_classes

_CATEGORY = "Procgen Maps"


class PROCGEN_PT_main_panel(bpy.types.Panel):
    bl_label = "Procgen Maps"
    bl_idname = "PROCGEN_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps

        layout.prop(settings, "preset")
        layout.prop(settings, "seed")

        col = layout.column(align=True)
        col.prop(settings, "use_terrain")
        col.prop(settings, "enable_parks")
        col.prop(settings, "enable_commercial")
        col.prop(settings, "enable_cars")

        layout.separator()
        layout.operator("procgen_maps.generate_terrain")
        layout.operator("procgen_maps.generate_city")
        layout.operator("procgen_maps.generate_dungeon")

        layout.separator()
        layout.prop(settings, "night_mode", toggle=True)


class PROCGEN_PT_export_panel(bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "PROCGEN_PT_export_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    bl_parent_id = "PROCGEN_PT_main_panel"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps
        layout.prop(settings, "export_directory")
        col = layout.column(align=True)
        col.operator("procgen_maps.export_gltf")
        col.operator("procgen_maps.export_fbx")
        col.operator("procgen_maps.export_usdz")
        col.operator("procgen_maps.export_svg")
        col.operator("procgen_maps.export_json")


class PROCGEN_PT_showcase_panel(bpy.types.Panel):
    bl_label = "Showcase Render"
    bl_idname = "PROCGEN_PT_showcase_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    bl_parent_id = "PROCGEN_PT_main_panel"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps
        layout.prop(settings, "showcase_angle")
        row = layout.row(align=True)
        row.prop(settings, "showcase_width")
        row.prop(settings, "showcase_height")
        layout.operator("procgen_maps.render_showcase")


class PROCGEN_PT_lighting_panel(bpy.types.Panel):
    bl_label = "Lighting"
    bl_idname = "PROCGEN_PT_lighting_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    bl_parent_id = "PROCGEN_PT_main_panel"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps

        layout.label(text="Sun")
        col = layout.column(align=True)
        col.prop(settings, "sun_elevation")
        col.prop(settings, "sun_rotation")
        row = layout.row(align=True)
        row.prop(settings, "sun_energy_day")
        row.prop(settings, "sun_energy_night")

        layout.separator()
        layout.label(text="Night Mode")
        layout.prop(settings, "night_sun_elevation")
        layout.prop(settings, "window_emission_strength")
        layout.prop(settings, "street_lamp_energy")


class PROCGEN_PT_profiler_panel(bpy.types.Panel):
    bl_label = "Profiler"
    bl_idname = "PROCGEN_PT_profiler_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY
    bl_parent_id = "PROCGEN_PT_main_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.procgen_maps
        layout.label(text=f"Objects: {settings.stat_objects}")
        layout.label(text=f"Vertices: {settings.stat_vertices}")
        layout.label(text=f"Faces: {settings.stat_faces}")
        layout.label(text=f"Last generate: {settings.stat_generate_seconds:.2f}s")


classes = (PROCGEN_PT_main_panel, PROCGEN_PT_export_panel, PROCGEN_PT_showcase_panel,
           PROCGEN_PT_lighting_panel, PROCGEN_PT_profiler_panel)


def register():
    register_classes(classes)


def unregister():
    unregister_classes(classes)
