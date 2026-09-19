from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("foreman-factory")
except PackageNotFoundError:
    __version__ = "0+unknown"
