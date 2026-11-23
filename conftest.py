"""Root pytest configuration.

Makes the repository root importable as `procgen_maps` no matter how pytest
is invoked. The bare `pytest` CLI (as opposed to `python -m pytest`) does
*not* put the current directory on `sys.path`, and `procgen_maps/tests/`
is deliberately not a package (no `__init__.py`), so pytest's default
"rootdir/prepend" import mode only adds `procgen_maps/tests/` itself -
which left every `from procgen_maps... import` in the test modules failing
with ModuleNotFoundError on CI while passing locally under `python -m
pytest`. Pytest always imports a rootdir `conftest.py` first and inserts
its directory into `sys.path`, which is exactly the one entry needed.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
