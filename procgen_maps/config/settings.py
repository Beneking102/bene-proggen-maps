"""Global fallback defaults shared across generators, independent of any city preset."""

DEFAULT_SEED = 0

# Terrain
# Resolution/scale/octaves/max_height are tuned together, not independently:
# every building/street/prop/sign samples the *continuous* noise function
# directly (generators/terrain.py's sample_world_height), but what's actually
# visible is the coarse mesh grid (from_pydata over resolution x resolution
# vertices) - too coarse a grid relative to the noise's own feature size lets
# the mesh disagree with the precise sampled heights everything else uses,
# which reads as buildings/streets/signs clipping into or floating above the
# ground. 256 over a 1000m world is ~3.9m/cell - visibly too coarse next to
# scale=120 noise features. 512 (~1.96m/cell), a broader scale, one fewer
# octave, and roughly half the old amplitude keep the mesh a much closer
# match to the sampled field, and read as a real city site rather than raw
# mountain terrain.
TERRAIN_DEFAULT_RESOLUTION = 512      # heightmap resolution (cells per side)
TERRAIN_DEFAULT_SCALE = 160.0         # noise feature scale, meters
TERRAIN_DEFAULT_OCTAVES = 4
TERRAIN_DEFAULT_PERSISTENCE = 0.5
TERRAIN_DEFAULT_LACUNARITY = 2.0
TERRAIN_DEFAULT_MAX_HEIGHT = 22.0     # max displacement, meters
TERRAIN_DEFAULT_WORLD_SIZE = 1000.0   # covers the largest preset radius with margin
TERRAIN_FLATTEN_MARGIN = 3.0          # meters; blend width for flattening ground under building footprints

# Performance / LOD
MAX_INSTANCES_BEFORE_LOD = 200        # prop count threshold where distance-based LOD kicks in
LOD_DISTANCE_HIGH = 40.0              # meters from camera: full detail within this range
LOD_DISTANCE_MEDIUM = 120.0           # meters from camera: medium detail within this range, low beyond

# Spatial
DEFAULT_SPATIAL_CELL_SIZE = 5.0       # meters, SpatialHashGrid cell size

# Export
EXPORT_DEFAULT_DIRECTORY_NAME = "procgen_maps_export"
