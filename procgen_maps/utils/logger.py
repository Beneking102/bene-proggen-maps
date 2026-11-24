"""Structured logging wrapper over stdlib `logging`, configured once and
namespaced under `procgen_maps` so messages are easy to filter in Blender's
system console.
"""
import logging

_ROOT_NAME = "procgen_maps"


def get_logger(name=None):
    """A `procgen_maps.<name>` logger, with the package's console handler installed once."""
    full_name = _ROOT_NAME if not name else f"{_ROOT_NAME}.{name}"
    root = logging.getLogger(_ROOT_NAME)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
    return logging.getLogger(full_name)
