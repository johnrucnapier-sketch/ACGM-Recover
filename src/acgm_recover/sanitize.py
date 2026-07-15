"""Legacy RC alias for :mod:`claude_code_recover.sanitize`."""

from importlib import import_module
import sys

_canonical = import_module("claude_code_recover.sanitize")
sys.modules[__name__] = _canonical
