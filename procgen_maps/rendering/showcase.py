"""Showcase render build step: turns a `framing.FramingPlan` into an actual
camera object, ensures a sun exists at the right angle for day/night, and
configures+triggers a render. This is exactly the setup every ad-hoc
render script used during this project's development did by hand - now a
real, reusable addon feature instead of a one-off script per screenshot.
"""
from . import framing as framing_calc

CAMERA_NAME = "ProcgenMaps_ShowcaseCamera"
SUN_NAME = "ProcgenMaps_ShowcaseSun"

DAY_SUN_ELEVATION = 0.97   # radians; matches materials/world_mat.py's default sky sun
NIGHT_SUN_ELEVATION = 1.9  # radians; below-horizon tilt used for night mode's dipped sun
SUN_ROTATION_Z = 2.4       # radians; matches materials/world_mat.py's default sky sun


def collect_scene_bounds(root_collection):
    """World-space AABB of every mesh object (+ Empty origins, since
    Instance-Collection props/furniture have no mesh data of their own)
    under `root_collection`, recursively. Returns (min, max) as plain
    3-tuples; (0,0,0)/(1,1,1) if nothing was found (an empty scene)."""
    import mathutils

    min_co = [1e9, 1e9, 1e9]
    max_co = [-1e9, -1e9, -1e9]
    found = False

    def visit(collection):
        nonlocal found
        for obj in collection.objects:
            if obj.type == 'MESH':
                for corner in obj.bound_box:
                    world_corner = obj.matrix_world @ mathutils.Vector(corner)
                    for axis in range(3):
                        min_co[axis] = min(min_co[axis], world_corner[axis])
                        max_co[axis] = max(max_co[axis], world_corner[axis])
                found = True
            elif obj.type == 'EMPTY':
                for axis, value in enumerate(obj.location):
                    min_co[axis] = min(min_co[axis], value)
                    max_co[axis] = max(max_co[axis], value)
                found = True
        for child in collection.children:
            visit(child)

    visit(root_collection)
    if not found:
        return (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    return tuple(min_co), tuple(max_co)


def build_showcase_camera(plan: framing_calc.FramingPlan):
    import bpy
    import mathutils

    camera_data = bpy.data.cameras.get(CAMERA_NAME)
    if camera_data is None:
        camera_data = bpy.data.cameras.new(CAMERA_NAME)
    camera_obj = bpy.data.objects.get(CAMERA_NAME)
    if camera_obj is None:
        camera_obj = bpy.data.objects.new(CAMERA_NAME, camera_data)
        bpy.context.scene.collection.objects.link(camera_obj)

    camera_obj.location = plan.camera_location
    direction = mathutils.Vector(plan.look_at) - mathutils.Vector(plan.camera_location)
    if direction.length < 1e-6:
        direction = mathutils.Vector((0.0, -1.0, -0.3))
    camera_obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    camera_data.lens = plan.focal_length
    camera_data.clip_end = plan.clip_end
    return camera_obj


def ensure_showcase_sun(night_mode: bool, energy_day=2.0, energy_night=0.08,
                         sun_elevation=None, sun_rotation=None):
    """`sun_elevation`/`sun_rotation`, if given, come from the Lighting
    panel's ProcgenMapsSettings (ui/panels.py) in materials/world_mat.py's
    elevation-from-horizon convention (0=horizon, +pi/2=zenith) - the same
    convention the Nishita sky texture uses. This Sun *light object*
    instead uses a zenith-angle convention (0=straight down/zenith,
    pi/2=horizontal/horizon), related by `zenith_angle = pi/2 - elevation`
    (this is exactly why the old hardcoded DAY_SUN_ELEVATION=0.97 was
    already "pi/2 - 0.6", world_mat.py's old default day elevation).
    Falls back to the existing DAY_SUN_ELEVATION/NIGHT_SUN_ELEVATION
    module constants when not given, so every other caller (the headless
    smoke test) is unaffected. Energy/color intentionally stay independent
    or Lighting-panel sun_energy_day/night (Sun-light watts and World
    Background Strength are different units - see PROGRESSION.md/OPEN
    RISKS for this feature)."""
    import bpy

    sun_data = bpy.data.lights.get(SUN_NAME)
    if sun_data is None:
        sun_data = bpy.data.lights.new(SUN_NAME, type='SUN')
    sun_obj = bpy.data.objects.get(SUN_NAME)
    if sun_obj is None:
        sun_obj = bpy.data.objects.new(SUN_NAME, sun_data)
        bpy.context.scene.collection.objects.link(sun_obj)

    if sun_elevation is None:
        zenith_angle = NIGHT_SUN_ELEVATION if night_mode else DAY_SUN_ELEVATION
    else:
        import math
        zenith_angle = math.pi / 2.0 - sun_elevation
    rotation_z = SUN_ROTATION_Z if sun_rotation is None else sun_rotation

    sun_obj.rotation_euler = (zenith_angle, 0.0, rotation_z)
    sun_data.energy = energy_night if night_mode else energy_day
    sun_data.angle = 0.2
    sun_data.color = (0.6, 0.7, 1.0) if night_mode else (1.0, 1.0, 1.0)
    return sun_obj


def configure_render_settings(resolution=(3840, 2160), samples=256):
    import bpy

    scene = bpy.context.scene
    try:
        scene.render.engine = 'BLENDER_EEVEE_NEXT'
    except TypeError:
        scene.render.engine = 'BLENDER_EEVEE'
    try:
        scene.eevee.use_raytracing = True
        scene.eevee.taa_render_samples = samples
    except Exception:
        pass
    scene.render.resolution_x = resolution[0]
    scene.render.resolution_y = resolution[1]


def render_showcase(filepath, resolution=(3840, 2160), samples=256):
    import bpy

    configure_render_settings(resolution=resolution, samples=samples)
    bpy.context.scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)
