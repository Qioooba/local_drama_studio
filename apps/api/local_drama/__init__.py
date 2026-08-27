"""LocalDramaStudio API package."""

from .bootstrap.build_identity import load_build_identity

__version__ = load_build_identity().version
