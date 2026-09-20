"""press-to-talk package."""

from .core import main
from .version import __version__, get_version

__all__ = ["main", "__version__", "get_version"]
