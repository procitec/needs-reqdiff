"""needs-reqdiff package."""

from importlib.metadata import PackageNotFoundError, version

from .api import generate_reqdiff
from .sphinx_roles import setup

try:
    __version__: str | None = version("needs-reqdiff")
except PackageNotFoundError:
    # A source checkout can be imported before the distribution is installed.
    __version__ = None
