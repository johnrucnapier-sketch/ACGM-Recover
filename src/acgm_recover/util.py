"""Legacy RC alias for :mod:`claude_code_recover.util`."""

from importlib import import_module
import sys

_canonical = import_module("claude_code_recover.util")
sys.modules[__name__] = _canonical
