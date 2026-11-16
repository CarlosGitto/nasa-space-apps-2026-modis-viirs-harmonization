"""Make `include.*` importable when pytest runs from etl/ (the Astro project root)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
