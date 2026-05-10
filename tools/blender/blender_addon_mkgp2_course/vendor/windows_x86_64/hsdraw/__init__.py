from . import hsdraw  # bind submodule explicitly to package namespace
from .hsdraw import *

__doc__ = hsdraw.__doc__
if hasattr(hsdraw, "__all__"):
    __all__ = hsdraw.__all__
