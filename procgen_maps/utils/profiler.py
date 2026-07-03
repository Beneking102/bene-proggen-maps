"""Lightweight performance tracking: named-stage timing plus (Blender-only)
vertex/object counting, surfaced in the N-panel profiler readout.
"""
import time
from contextlib import contextmanager


class Profiler:
    def __init__(self):
        self._durations = {}

    @contextmanager
    def stage(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._durations[name] = self._durations.get(name, 0.0) + elapsed

    def report(self):
        return dict(self._durations)

    def total_time(self):
        return sum(self._durations.values())

    def reset(self):
        self._durations.clear()


def count_scene_stats(objects):
    """Given an iterable of bpy objects, return object/vertex/face counts.

    Only mesh-type objects contribute vertex/face counts; safe to call with
    an empty iterable.
    """
    obj_count = 0
    vert_count = 0
    face_count = 0
    for obj in objects:
        obj_count += 1
        mesh = getattr(obj, "data", None)
        if mesh is not None and hasattr(mesh, "vertices"):
            vert_count += len(mesh.vertices)
            face_count += len(mesh.polygons)
    return {"objects": obj_count, "vertices": vert_count, "faces": face_count}
