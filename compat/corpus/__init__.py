"""Curated compatibility corpus metadata for WinForge."""
from __future__ import annotations

import json
from importlib import resources
from typing import Any

CORPUS_SCHEMA_VERSION = "winforge.compat-corpus/v0"


def load_default_corpus() -> dict[str, Any]:
    """Load the packaged default compatibility corpus."""
    text = resources.files(__package__).joinpath("apps.json").read_text(encoding="utf-8")
    payload = json.loads(text)
    if payload.get("schemaVersion") != CORPUS_SCHEMA_VERSION:
        raise ValueError(f"default corpus schemaVersion must be {CORPUS_SCHEMA_VERSION}")
    apps = payload.get("apps")
    if not isinstance(apps, list) or not apps:
        raise ValueError("default corpus must contain a non-empty apps list")
    return payload


def load_corpus() -> dict[str, Any]:
    """Backward-compatible alias for loading the packaged corpus."""
    return load_default_corpus()
