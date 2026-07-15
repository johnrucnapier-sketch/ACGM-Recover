"""Legacy RC alias for ``python -m claude_code_recover``."""

from claude_code_recover.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
