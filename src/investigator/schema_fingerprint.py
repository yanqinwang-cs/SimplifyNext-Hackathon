"""Canonical fingerprints for JSON-schema objects."""

import hashlib
import json
from typing import Any


def canonical_schema_json(schema: Any) -> str:
    return json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def schema_fingerprint(schema: Any) -> str:
    return hashlib.sha256(canonical_schema_json(schema).encode("utf-8")).hexdigest()
