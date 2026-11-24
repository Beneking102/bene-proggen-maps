# Changelog

All notable changes to Procgen Maps. The visual history with renders per
version is in `PROGRESSION.md`; the plans in `ROADMAP.md`.

## v0.15.0 - Polish and release (2026-11-24)

**Landscape.** The countryside outside the city is a patchwork instead of
a pale plain: field cells with their own tone (meadow greens, a dark
crop, ripening barley, golden stubble, ploughed earth), dark hedgerow
lines on the field borders with trees along them, and the forest ring
grouped into woods with a humus floor exactly under the trees. All of
it comes from one pure-numpy module (`generators/landscape.py`) that
both the terrain shader (via per-vertex attributes) and the prop
planner read, so ground and vegetation agree. The map no longer ends in
a cliff against a grey void: a horizon skirt slopes down from the
terrain edge into a flat far ground that fades into the haze, and the
sky's below-horizon band, the haze colour and that ground meet in one
tone. The aerial-perspective haze is scene-referred bright now (it used
to fade distant things to a *dark* grey), the day exposure is -2.7 EV,
the ground palette is darker and more saturated. Country roads have a
grass verge (terrain material) sloping into the ground instead of a
vertical asphalt skirt, so they no longer look like they float with a
black outline.

**UI/UX.** Panels reorganised into Generate / Lighting / Custom Presets
/ Showcase Render / Export / Validate & Stats sub-panels; every property
has a tooltip; a "last message" line under Generate All; timed stage
lines in the system console while a city generates. Custom presets:
save/load/delete the whole panel state as JSON files in Blender's user
config folder (`config/user_presets.py`), with an "open folder" button.
Every operator reports failures as one clean error message (traceback in
the console) instead of a Python traceback popup; Generate All skips the
terrain when Terrain is off; exports refuse politely when nothing is
generated.

**Audit.** Docstrings on every public function and class, unused
imports removed, the horizon left out of the game export / SVG / stats /
showcase framing. New tests: `test_landscape.py`, `test_user_presets.py`,
the forest-ring clump rules; the smoke test checks the landscape
attributes, the horizon, the custom-preset round trip and that the GLB
leaves the horizon out.

**Release.** README rewritten (hero image, feature overview, install,
quick start), this changelog, release zip via
`procgen_maps/scripts/package_release.py`, preview stills (1280x720) and
a 10 s flyover video in `docs/progression/` (the full-size golden-hour
and night hero stills are pending).

## v0.14.0 - World, dungeon, game export

- Game-engine glTF export: City / Terrain / Dungeon hierarchy, box and
  trimesh colliders, `_LOD0`/`_LOD1` groups, navmesh-friendly walkable
  meshes, Godot or Unity naming; the scene is restored afterwards.
- Dungeon: crypt / cave / castle themes, doors with lintels, two to four
  stacked levels joined by staircases, wall torches and a fill light per
  room; `validate_dungeon` fuzzed over 50 seeds x 3 themes.
- World: terrain biomes (meadow, forest floor, rock on slopes, snow on
  real peaks), a country road from the city to the map edge with a
  village of cottages along it.
- Bridges read as bridges (post railings, fascia, arched soffit); slim
  balcony railings.

## v0.13.0 - City visuals

- Night windows planned per floor and per apartment (lit runs, warm or
  cool, dark floors) via per-face mesh attributes on one shared window
  material.
- Facade and roof variety: balconies, awnings and sign boards, hip and
  sawtooth roofs, rooftop tanks and masts.
- Parks with path crosses, fountains, benches and lamps; paved plazas.
- A meandering river with sloped banks and bridges; nothing is planned in
  the water and the validator checks it.
- The road-bed lip along sidewalks is gone (shallower bed plus a skirt).

## v0.12.0 - Performance and scale

- The whole street network is one mesh object (-700 objects on a
  Metropole); road width and lanes are per-face attributes.
- The terrain is sunk under every street corridor so no ground pokes
  through the asphalt (`terrain_above_roads` in Validate Map).
- Sidewalk corner pieces at every crossing and bend.
- A profile table per preset in `OPTIMIZATION.md`; a progress cursor
  while a city generates.

## v0.11.0 - Correctness and robustness

- Project-wide placement audit (buildings, roads, sidewalks, props,
  signs, parking lots, special buildings) with a regression test per
  bug; parked cars clear of junctions, lamps off the crossings.
- Determinism of every generator pinned by a 50 seeds x 4 presets fuzz
  test over the pure-Python planner plus the validator.
- Regenerate without leaks (no orphan datablocks), clean unregister,
  seed 0 / huge seeds, city without terrain, terrain without city.
- The **Validate Map** operator.

## v0.10.0 - CI fixed, 3.4x faster Metropole, a city that looks like one

- Cycles CPU showcase renders so CI can render; deterministic seeds.
- numpy terrain build, scalar fbm fast path, cheaper night mode.
- Real window columns, a connected irregular-grid layout, sidewalks and
  curbs, road markings, setbacks, an edge forest, weathered facades,
  parapets, grass/rock terrain, haze, AgX exposure, lighting presets,
  dungeon halls; showcase stills and the first flyover video.

Earlier versions (v0.1 - v0.9) are described in `PROGRESSION.md`.
