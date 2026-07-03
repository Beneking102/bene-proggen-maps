"""Global fallback defaults shared across generators, independent of any city preset."""

DEFAULT_SEED = 0

# Terrain
TERRAIN_DEFAULT_RESOLUTION = 256      # heightmap resolution (cells per side)
TERRAIN_DEFAULT_SCALE = 120.0         # noise feature scale, meters
TERRAIN_DEFAULT_OCTAVES = 5
TERRAIN_DEFAULT_PERSISTENCE = 0.5
TERRAIN_DEFAULT_LACUNARITY = 2.0
TERRAIN_DEFAULT_MAX_HEIGHT = 40.0     # max displacement, meters
TERRAIN_DEFAULT_WORLD_SIZE = 1000.0   # covers the largest preset radius with margin

# Performance / LOD
MAX_INSTANCES_BEFORE_LOD = 200        # prop count threshold where distance-based LOD kicks in
LOD_DISTANCE_HIGH = 40.0              # meters from camera: full detail within this range
LOD_DISTANCE_MEDIUM = 120.0           # meters from camera: medium detail within this range, low beyond

# Spatial
DEFAULT_SPATIAL_CELL_SIZE = 5.0       # meters, SpatialHashGrid cell size

# Export
EXPORT_DEFAULT_DIRECTORY_NAME = "procgen_maps_export"
