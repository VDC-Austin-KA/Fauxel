"""Pure tests need nothing. Router/DB tests reuse the API fixtures (Postgres with PostGIS + pgvector):
FAUXEL_TEST_ADMIN_URL=postgresql://fauxel@localhost:55432/postgres pytest connectors/ifc-speckle-bcf/tests"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_api_conftest = REPO / "services" / "api" / "tests" / "conftest.py"
if _api_conftest.exists():
    spec = importlib.util.spec_from_file_location("fauxel_api_conftest", _api_conftest)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fauxel_api_conftest"] = mod
    spec.loader.exec_module(mod)
    from fauxel_api_conftest import db_url, make_client, make_settings, pid  # noqa: F401,E402
    from fauxel_api_conftest import client as _unused  # noqa: F401,E402
