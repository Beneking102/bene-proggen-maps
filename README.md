# Procgen Maps

[![License: GPL v3](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)

A Blender addon that procedurally generates cities, terrain, and dungeons,
and exports them to common interchange formats. Targets Blender 4.2+.

## Features

- **4 city archetype presets** (`procgen_maps/config/presets.py`): Metropole,
  Kleinstadt, Dorf, Industrial - each tuning radius, block size, layout mode,
  building height range, zone mix, street widths, prop density and more.
- **Procedural terrain**: an FBM (fractal Brownian motion) noise heightmap,
  sampled consistently so streets and buildings can follow terrain height
  without ray-casting.
- **Full city generation pipeline**: layout (grid or raster block partition)
  -> zone classification -> street network -> buildings -> props, each stage
  a separate, independently testable module.
- **12 procedural building facade types** (glass tower, office block, brick
  commercial, apartment slab/tower, townhouse, cottage, shopfront, warehouse,
  factory hall, industrial tower, mixed use), extruded floor-by-floor with
  bmesh and driven by a shared parametrized facade material.
- **5 special buildings** (supermarket, police station, hospital, fire
  station, school), placed by explicit zone/size selection rather than the
  random facade pick, each with an illuminated sign and, for hospitals, a
  rooftop helipad (`procgen_maps/generators/city/special_buildings.py`).
- **Separate BSP dungeon generator**, independent of the city/terrain
  pipeline - rooms and corridors from binary space partitioning.
- **Procedural placeholder props**: 12 tree variants, a street lamp, a
  bench, a parked car, and a sign, all built from primitive geometry.
  Real 3D asset modeling is explicitly out of scope for this codebase (see
  `procgen_maps/assets/library.py`).
- **Instance-Collection-based prop instancing** with distance-based LOD, so
  placing thousands of props costs lightweight Empties, not thousands of
  independent meshes.
- **Night mode**: emissive building windows plus real point lights at every
  street lamp, toggled from the panel - also dips the procedural sky's sun
  below the horizon.
- **Procedural sky + material detail**: a Nishita sky world (no HDRI file
  needed) plus noise-driven grain/bump on facade and terrain materials for
  higher-quality renders without baked textures.
- **Render Showcase Image**: one-click auto-framed render - computes a
  camera placement from whatever's been generated (`procgen_maps/rendering/`),
  sets up day/night sun position, enables raytracing for correct glass, and
  renders straight to the export directory.
- **Export** to glTF/GLB, FBX, USDZ, a top-down SVG map, and a JSON stats
  sidecar.
- **N-panel UI** under `View3D > Sidebar > Procgen Maps`, with a profiler
  readout (object/vertex/face counts, last generate time).

No scipy dependency anywhere - it isn't bundled with Blender's Python.
Block partitioning, spatial queries and nearest-neighbor lookups all use
numpy and Blender's own `mathutils` instead. See `ARCHITECTURE.md` for
details.

## Installation

1. Get the code: clone this repository, or download a release zip built by
   `procgen_maps/scripts/package_release.py` from the project's Releases page.
2. If you cloned instead of using a release zip, zip the `procgen_maps/`
   folder itself (the zip must contain `procgen_maps/__init__.py` at its
   top level, not nested one level deeper).
3. In Blender: **Edit > Preferences > Add-ons > Install from Disk...**, and
   select the zip.
4. Enable the **Procgen Maps** checkbox in the add-on list.

## Quick Start

1. Open the sidebar in the 3D Viewport (press `N`) and select the
   **Procgen Maps** tab.
2. Pick a city preset (Metropole / Kleinstadt / Dorf / Industrial) and a
   seed.
3. Click **Generate Terrain**, **Generate City**, and/or **Generate
   Dungeon** - each is independent and can be run on its own.
4. Toggle **Night Mode** to switch on emissive windows and street lamp
   lights.
5. Open the **Export** sub-panel to write glTF, FBX, USDZ, SVG, or JSON
   output to the configured export directory.

## Contributing

See `ARCHITECTURE.md` for the module layout, registration pattern, and the
pure-Python/bpy split convention every generator follows - read it before
sending a pull request. `DEPLOYMENT.md` and `OPTIMIZATION.md` cover running
headless and performance tuning, respectively. `PROGRESSION.md` tracks the
project's visual history with screenshots/renders per version.

## License

GPL-3.0-or-later. See `LICENSE`.
