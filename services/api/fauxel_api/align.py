"""Single import point for fauxel_capture.alignment (services/capture-ingestion), reused as is."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from fauxel_capture.alignment import BimCoordinateTransformer, Pose3D, UmeyamaAligner
except ImportError:  # running from the repo without `pip install -e services/capture-ingestion`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "capture-ingestion"))
    from fauxel_capture.alignment import BimCoordinateTransformer, Pose3D, UmeyamaAligner

__all__ = ["BimCoordinateTransformer", "Pose3D", "UmeyamaAligner"]
