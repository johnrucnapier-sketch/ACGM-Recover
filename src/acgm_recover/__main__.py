"""Support ``python -m acgm_recover`` on every supported Python platform."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
