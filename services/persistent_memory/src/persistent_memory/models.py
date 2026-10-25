"""Canonical source records and hashing shared by the engine and adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

HASH_EXCLUDED_FIELDS = frozenset({"meta", "thinking"})


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return one flattened host record without transport metadata or reasoning.

    ``data`` fields are merged into the record unchanged unless the host also
    supplied one of the canonical fields at the top level. ``thinking`` is
    dropped at every depth because it is private reasoning, never source text.
    """
    if not isinstance(record, dict):
        raise TypeError("record must be a JSON object")

    normalized: dict[str, Any] = {}
    data = record.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if key not in HASH_EXCLUDED_FIELDS:
                normalized[key] = _strip_thinking(value)
    for key, value in record.items():
        if key in HASH_EXCLUDED_FIELDS or key == "data":
            continue
        normalized[key] = _strip_thinking(value)
    return normalized


def canonical_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a record and attach a stable ``_record_key``.

    Host records commonly carry their own ``id`` (for example tool call IDs),
    so the internal identity must use a separate field.
    """
    item = normalize_record(record)
    item["_record_key"] = record_fingerprint(item)
    return item


def record_fingerprint(record: dict[str, Any]) -> str:
    """Hash exact canonical JSON: sort keys, UTF-8, compact separators."""
    payload = json.dumps(
        normalize_record(record),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def records_hash(records: Iterable[dict[str, Any]]) -> str:
    """Hash the ordered list of canonical records."""
    payload = [canonical_record(record) for record in records]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _strip_thinking(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_thinking(item)
            for key, item in value.items()
            if key != "thinking"
        }
    if isinstance(value, list):
        return [_strip_thinking(item) for item in value]
    return value


def record_text(record: dict[str, Any]) -> str:
    """Extract the source text without inventing a summary."""
    text = record.get("text")
    if isinstance(text, str) and text.strip():
        return text
    result = record.get("result")
    if isinstance(result, str) and result.strip():
        return result
    return ""
