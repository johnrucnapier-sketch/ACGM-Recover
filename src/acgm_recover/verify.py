"""Legacy RC alias for :mod:`claude_code_recover.verify`."""

from importlib import import_module
import sys

_canonical = import_module("claude_code_recover.verify")
sys.modules[__name__] = _canonical
