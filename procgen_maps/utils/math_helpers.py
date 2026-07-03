"""Small dependency-free math helpers shared across generators."""


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def remap(value, in_min, in_max, out_min, out_max):
    """Linearly remap `value` from [in_min, in_max] to [out_min, out_max]."""
    if in_max == in_min:
        return out_min
    t = (value - in_min) / (in_max - in_min)
    return out_min + (out_max - out_min) * t


def smoothstep(edge0, edge1, x):
    if edge1 == edge0:
        return 0.0 if x < edge0 else 1.0
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)
