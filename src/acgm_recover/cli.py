"""Legacy RC CLI alias for :mod:`claude_code_recover.cli`."""

from claude_code_recover.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
