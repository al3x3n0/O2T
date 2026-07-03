"""Compatibility package for the renamed :mod:`o2t` module tree."""

from __future__ import annotations

import importlib

_o2t = importlib.import_module("o2t")
__path__ = _o2t.__path__
__all__ = getattr(_o2t, "__all__", [])
