"""JSON Schema validation against contracts/*.schema.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .config import Settings


@lru_cache(maxsize=None)
def _validator(contracts_dir: str, name: str) -> Draft202012Validator:
    path = Path(contracts_dir) / (name if name.endswith(".json") else f"{name}.schema.json")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validation_errors(settings: Settings, name: str, payload) -> list[str]:
    v = _validator(settings.contracts_dir, name)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
            for e in sorted(v.iter_errors(payload), key=lambda e: list(e.absolute_path))][:20]
