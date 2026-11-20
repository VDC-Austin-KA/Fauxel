import sys
from pathlib import Path

API_ROOT = str(Path(__file__).resolve().parents[2])
if API_ROOT not in sys.path:
    sys.path.insert(0, API_ROOT)
