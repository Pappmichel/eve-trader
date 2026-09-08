"""Pipeline actions for the Sorting tool - see CLAUDE.md's Architecture
section: the FastAPI router calls these do_* functions, never
storage.py/engine.py directly.
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from ..actions import ActionError
from ..production.constants import INTAKE_HANGAR_FLAGS
from . import engine


def do_sorting_list() -> dict:
    return engine.do_sorting_list()


def do_list_intake_sources() -> dict:
    sources = [
        {
            "id": source_id,
            "source_kind": source_kind,
            "character_name": character_name,
            "hangar_flag": hangar_flag,
            "label": label,
        }
        for source_id, source_kind, character_name, hangar_flag, label
        in storage.load_sorting_intake_sources()
    ]
    return {"sources": sources}


def do_add_intake_source(source_kind: str, hangar_flag: str,
                         character_name: Optional[str] = None,
                         label: Optional[str] = None) -> dict:
    if source_kind not in ("character", "corp"):
        raise ActionError("source_kind must be 'character' or 'corp'.")
    if hangar_flag not in INTAKE_HANGAR_FLAGS:
        raise ActionError(
            f"hangar_flag: {hangar_flag!r} is not a known hangar division. "
            f"Options: {', '.join(INTAKE_HANGAR_FLAGS)}"
        )
    if source_kind == "character":
        if not character_name or not character_name.strip():
            raise ActionError("character_name is required for a character intake source.")
        character_name = character_name.strip()
    else:
        character_name = None
    if label is not None:
        label = label.strip() or None
    source_id = storage.add_sorting_intake_source(
        source_kind, hangar_flag, character_name=character_name, label=label,
    )
    return {
        "id": source_id,
        "source_kind": source_kind,
        "character_name": character_name,
        "hangar_flag": hangar_flag,
        "label": label,
    }


def do_remove_intake_source(source_id: int) -> dict:
    storage.remove_sorting_intake_source(source_id)
    return {"removed": source_id}


def do_list_available_characters() -> dict:
    return {"characters": storage.load_tenant_character_names()}
