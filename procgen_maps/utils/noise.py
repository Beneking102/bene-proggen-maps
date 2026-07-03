"""Deterministic, dependency-light noise primitives.

`value_noise_2d` is a pure-Python scalar reference implementation (no numpy)
used mainly for zone/prop scattering and unit tests. `fbm` / `fbm_heightmap`
are numpy-vectorized and are what the terrain/city generators actually use
for bulk sampling. No scipy dependency anywhere (see ARCHITECTURE.md).
"""
import math

import numpy as np


def _hash_scalar(ix, iy, seed):
    h = (ix * 374761393) ^ (iy * 668265263) ^ (seed * 2147483647)
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return (h & 0xFFFFFFFF) / 0xFFFFFFFF


def _smoothstep_scalar(t):
    return t * t * (3 - 2 * t)


def value_noise_2d(x, y, seed=0):
    """Pure-Python scalar value noise in [0, 1)."""
    x0, y0 = math.floor(x), math.floor(y)
    x1, y1 = x0 + 1, y0 + 1
    tx, ty = x - x0, y - y0
    sx, sy = _smoothstep_scalar(tx), _smoothstep_scalar(ty)

    n00 = _hash_scalar(x0, y0, seed)
    n10 = _hash_scalar(x1, y0, seed)
    n01 = _hash_scalar(x0, y1, seed)
    n11 = _hash_scalar(x1, y1, seed)

    nx0 = n00 + sx * (n10 - n00)
    nx1 = n01 + sx * (n11 - n01)
    return nx0 + sy * (nx1 - nx0)


def _hash_grid(ix, iy, seed):
    # Integer overflow here is intentional wraparound (standard for this kind
    # of hash), not a bug - silence numpy's overflow warning for it.
    with np.errstate(over="ignore"):
        h = (ix.astype(np.int64) * 374761393) ^ (iy.astype(np.int64) * 668265263) ^ np.int64(seed * 2147483647)
        h = (h ^ (h >> 13)) * np.int64(1274126177)
        h = h ^ (h >> 16)
    return (h & 0xFFFFFFFF).astype(np.float64) / 0xFFFFFFFF


def _value_noise_grid(xs, ys, seed):
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    x1, y1 = x0 + 1, y0 + 1
    tx, ty = xs - x0, ys - y0
    sx = tx * tx * (3 - 2 * tx)
    sy = ty * ty * (3 - 2 * ty)

    n00 = _hash_grid(x0, y0, seed)
    n10 = _hash_grid(x1, y0, seed)
    n01 = _hash_grid(x0, y1, seed)
    n11 = _hash_grid(x1, y1, seed)

    nx0 = n00 + sx * (n10 - n00)
    nx1 = n01 + sx * (n11 - n01)
    return nx0 + sy * (nx1 - nx0)


def fbm(x, y, *, scale=100.0, octaves=5, persistence=0.5, lacunarity=2.0, seed=0):
    """Fractal Brownian motion sampled at world-space coordinates.

    `x`/`y` may be Python scalars or numpy arrays of any (matching) shape -
    they do not need to form a meshgrid. Returns values normalized to
    [0, 1]. `scale` is the dominant feature size in world units.
    """
    xs = np.asarray(x, dtype=np.float64) / scale
    ys = np.asarray(y, dtype=np.float64) / scale

    total = np.zeros(np.broadcast(xs, ys).shape, dtype=np.float64)
    amplitude = 1.0
    frequency = 1.0
    max_amplitude = 0.0
    for octave in range(octaves):
        total = total + _value_noise_grid(xs * frequency, ys * frequency, seed + octave * 101) * amplitude
        max_amplitude += amplitude
        amplitude *= persistence
        frequency *= lacunarity
    result = total / max_amplitude
    return float(result) if result.shape == () else result


def fbm_heightmap(resolution, world_size, *, octaves=5, persistence=0.5, lacunarity=2.0, scale=100.0, seed=0):
    """Generate a (resolution, resolution) heightmap array in [0, 1],
    covering a world_size x world_size square centered on the origin."""
    half = world_size / 2.0
    coords = np.linspace(-half, half, resolution)
    xs, ys = np.meshgrid(coords, coords)
    return fbm(xs, ys, scale=scale, octaves=octaves, persistence=persistence, lacunarity=lacunarity, seed=seed)
