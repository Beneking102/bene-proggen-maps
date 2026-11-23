# Roadmap

Where Procgen Maps is heading over the next releases. Each version has one
theme; every version ships with a green `pytest` + headless Blender smoke
test, a regression test for every bug it fixes, and a `PROGRESSION.md`
entry with renders. Order and scope may shift, but the themes are fixed.

## v0.11 - Correctness and robustness (shipped)

- Project-wide placement audit: overlaps between every object class
  (buildings, roads, sidewalks, props, signs, parking lots, special
  buildings), ground contact on terrain, floating/buried parts, z-fighting.
- Determinism of every generator (terrain, city, dungeon) pinned by a
  multi-seed fuzz test (50 seeds x 4 presets, pure Python) that also runs
  the validator over every plan.
- Regenerate without leaks: no orphan meshes/materials/collections after
  regenerating terrain, city or dungeon; clean unregister.
- Edge cases: seed 0 and huge seeds, city without terrain, terrain without
  city, city-only regenerate on an existing terrain.
- New **Validate Map** operator: reports overlaps, props/signs on the
  asphalt, disconnected streets, floating/buried objects and orphan
  datablocks for the current scene.

## v0.12 - Performance and scale (shipped)

- [x] Terrain under roads: the ground can no longer poke through the
  asphalt or sidewalks (road bed sunk into the terrain mesh, checked by
  `terrain_above_roads` in Validate Map and the smoke test).
- [x] Sidewalk corner pieces at every crossing, T-junction and bend.
- [x] Fewer objects: the whole street network is one mesh object (road
  width/lanes as per-face attributes).
- [x] Profile every preset (OPTIMIZATION.md); progress cursor while
  Generate City runs.
- [ ] Metropole (terrain + city) under 4 s - at ~5 s; needs a
  per-block building builder (buildings are ~80% of the time).
- [ ] Buildings merged per block, props via Geometry Nodes instancing.
- [ ] Large-map mode (chunked generation), runtime-budget tests, memory
  measurements.

## v0.13 - City visuals (shipped)

- [x] Night windows: contiguous lit apartments/floors instead of a
  checkerboard, warm and cool light temperatures, dark floors.
- [x] More facade and roof variety: balconies on the residential types,
  awnings and sign boards over shop entrances, hip and sawtooth roofs,
  rooftop tanks and masts.
- [x] Parks with paths, benches and lamps; paved plazas.
- [x] Water: a river with sloped banks and bridges (deck, railings, pier),
  validator rules for it. Not done: a lake option.
- [x] The road-bed "lip" along the sidewalks is gone (shallower bed at the
  sidewalk edge plus a sidewalk skirt).
- [ ] Street trees: already there since v0.10; no new street furniture
  beyond park benches/lamps this round.
- [ ] Metropole under 4 s (carried over from v0.12; see OPTIMIZATION.md).

## v0.14 - World, dungeon, game export (this release)

- [x] Game-engine glTF export (`exporters/game_export.py`): a
  City/Terrain/Dungeon hierarchy, box colliders for buildings and props
  and trimesh colliders for ground/streets/dungeon rooms in Godot
  (`-col`/`-colonly`/`-navmesh`) or Unity (`_col`, `Collision` group)
  naming, `_LOD0`/`_LOD1` groups for buildings and prop instances, a
  navmesh-friendly `Ground_Walkable` (downsampled terrain + streets +
  park paths) and `Dungeon_Walkable` mesh. Options in the Export panel;
  the smoke test re-imports the GLB and checks the result.
- [x] Dungeon: crypt / cave / castle themes (materials, sarcophagi,
  boulders, banners), doors with lintels in every doorway, two stacked
  levels joined by a staircase through the floor, wall torches plus a dim
  fill light per room; `validate_dungeon` fuzzed over 50 seeds x 3 themes.
- [x] World: meadow / forest-floor / rock / snow biomes in the terrain
  shader (distance from the city, slope, a per-terrain snow line), a
  country road from the city's edge to the map edge (no sidewalks, an
  asphalt skirt) with a small village of cottages along it - all part of
  the street graph / building plans, so Validate Map covers them.
- [x] Bridges read as bridges (post railings on a low parapet, deck
  fascia, arched soffit); balcony railings are light and slim.
- [ ] Not done: MSFT_lod extension (separate LOD objects only), convex
  hull colliders (boxes only), a lake, more than one country road.
- [ ] Metropole under 4 s (carried over; see OPTIMIZATION.md).

## v0.15 - Polish and release

- UI/UX pass, save/load custom presets, tooltips everywhere.
- Final audit and code review of every module, updated docs, release zip
  and release notes.
- Showcase: hero images and a short flyover video.
