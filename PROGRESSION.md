# Progression

Visual version history for procgen_maps. Each entry shows what the generator
actually produced at that point — screenshots and renders, not just commit
messages. Presets/seeds are noted so any image can be reproduced exactly.

---

## v0.3.0 — Detail pass, ground-floor interiors, overlap fix (current)

Building facades got real per-instance variety (jittered window frequency/
pitch/floor height so same-facade buildings stop looking identical), a
proper wide entrance recess on the ground floor, and a chance at a rooftop
utility unit on flat-roofed buildings. Every building now gets a simple
furnished ground-floor interior (floor, ceiling, warm light, 2-3
facade-appropriate furniture pieces from a new catalog) visible through
actually-transmissive window glass. Also fixed two real bugs surfaced by
this pass: props could still spawn overlapping buildings in the denser
raster-layout presets (Metropole, Industrial) because `SpatialHashGrid`'s
collision search radius didn't account for large registered items (like a
building's bounding circle) — only the query's own small radius. Verified
0 overlaps across all 4 presets after the fix, with a regression test
locking it in.

**Kleinstadt, seed 21 — rendered:**
![Kleinstadt render](docs/progression/v0.3.0-kleinstadt-render.png)

**Live Blender session — actual screenshot, not a render:**
![Blender screenshot](docs/progression/v0.3.0-blender-screenshot.png)

**Dorf, seed 30 — rendered:**
![Dorf day](docs/progression/v0.3.0-dorf-day.png)

**Kleinstadt, seed 30, night mode:**
![Kleinstadt night](docs/progression/v0.3.0-kleinstadt-night.png)

---

## v0.2.0 — Core bug fixes: grounded props, real materials, working windows/roofs

Fixed the issues visible in the first end-to-end test: props (trees, lamps,
benches) were floating above or buried under sloped terrain because
`props.py` hardcoded z=0 instead of sampling the heightmap; nothing had a
real material assigned (props showed as flat gray, and Blender's Solid
viewport shading ignores shader node graphs entirely, reading a separate
`diffuse_color` field instead); and — the deepest bug of this round —
window/roof detail had never actually rendered because of a `bmesh` quirk:
`extrude_face_region`'s own return value only reports the single moved top
face and silently drops the 4 newly-created side faces every time, so the
window-carving code had been running against nothing since it was written.
Fixed with a proper before/after face diff, added a real gable roof (ridge +
two slopes, not a single-point pyramid poke), and nested `ProcgenMaps_City`
correctly under the addon's root collection instead of as a stray sibling.

**Dorf, seed 7:**
![Dorf day v0.2.0](docs/progression/v0.2.0-dorf-day.png)

**Metropole, seed 1:**
![Metropole day v0.2.0](docs/progression/v0.2.0-metropole-day.png)

---

## v0.1.0 — Initial functional build

First end-to-end pass: 4 presets, terrain, the full layout → zones → streets
→ buildings → props pipeline, a BSP dungeon generator, and glTF/FBX/USDZ/
SVG/JSON export, all verified via a headless Blender smoke test. Visually
rough: props floating/buried relative to terrain, flat gray materials, and
buildings that were plain extruded boxes with no visible window or roof
detail — the issues v0.2.0 above fixes. No image captured for this
milestone (issues were reported via a live screenshot in conversation,
not saved to a file at the time).

---

## Reproducing any of these

```
blender --background --factory-startup --python your_script.py
```
```python
import procgen_maps
procgen_maps.register()
bpy.context.scene.procgen_maps.preset = "KLEINSTADT"  # or DORF / METROPOLE / INDUSTRIAL
bpy.context.scene.procgen_maps.seed = 21
bpy.ops.procgen_maps.generate_terrain()
bpy.ops.procgen_maps.generate_city()
```
See DEPLOYMENT.md for the full headless workflow.
