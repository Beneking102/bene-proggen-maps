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

## v0.12 - Performance and scale (this release)

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

## v0.13 - City visuals

- Night windows: contiguous lit apartments/floors instead of a
  checkerboard, warm and cool light temperatures.
- More facade and roof variety, balconies, shop rows, parks and plazas,
  street trees and furniture.
- Water: a river or lake with bridges and shoreline zones.

## v0.14 - World, dungeon, game export

- Terrain biomes, city-to-countryside transitions, country roads to
  villages.
- Dungeon: themes, doors, multiple levels with stairs, better lighting.
- Game-engine export: collision meshes, LODs in glTF, Unity/Godot-friendly
  hierarchy, navmesh-friendly floors.

## v0.15 - Polish and release

- UI/UX pass, save/load custom presets, tooltips everywhere.
- Final audit and code review of every module, updated docs, release zip
  and release notes.
- Showcase: hero images and a short flyover video.
