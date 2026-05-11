"""Permite invocar el motor con `python -m radar_soberano`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
