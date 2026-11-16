"""Update models for the settings save routes.

A settings model has required fields and defaults, so a client that leaves a
field out would reset it to the default. The update model keeps every field's
type (a wrong type or an explicit null on a field that does not take null is
still a 422) but defaults each to "not set"; routes read it with
``model_dump(exclude_unset=True)``, so only what the client sent is saved.
An explicit null for a nullable field is a set value and is kept.

Request length limits: the settings models double as response models, so
they stay unbounded (a stored value longer than a limit would turn a read into
a 500). The update model is the request side and adds the shared limits from
``schemas`` to every field that has none.
"""
from __future__ import annotations

import types
from typing import Annotated, Any, Optional, Union, get_args, get_origin

from annotated_types import Len, MaxLen
from pydantic import BaseModel, create_model

from .schemas import ShortText, bounded_json

_MAX_LIST = 50_000  # id lists; text lists use the tighter bound below
_MAX_TEXT_LIST = 1_000
_MAX_DICT = 200


def _has_length_limit(metadata: tuple) -> bool:
    for m in metadata:
        if isinstance(m, MaxLen) or (isinstance(m, Len) and m.max_length is not None):
            return True
        if getattr(m, "max_length", None) is not None:
            return True
        # ``Annotated[..., Field(max_length=n)]`` keeps the constraint in the
        # FieldInfo's own metadata list.
        if _has_length_limit(tuple(getattr(m, "metadata", ()) or ())):
            return True
    return False


def bound_annotation(ann: Any) -> Any:
    """Add the shared request limits to every unbounded str, list/set/tuple and
    dict inside ``ann``; anything already bounded is left as it is."""
    origin = get_origin(ann)
    args = get_args(ann)
    if origin is Annotated:
        inner, *meta = args
        if _has_length_limit(tuple(meta)):
            return ann
        return Annotated[(bound_annotation(inner), *meta)]
    if ann is str:
        return ShortText
    if ann is Any or ann is dict or ann is list:
        return Annotated[ann, bounded_json(64 * 1024)]
    if origin is Union or origin is types.UnionType:
        return Union[tuple(bound_annotation(a) for a in args)]
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        element = bound_annotation(args[0])
        limit = _MAX_LIST if args[0] is int else _MAX_TEXT_LIST
        return Annotated[tuple[element, ...], MaxLen(limit)]
    if origin in (list, set, frozenset):
        element = bound_annotation(args[0]) if args else Any
        limit = _MAX_LIST if args and args[0] is int else _MAX_TEXT_LIST
        return Annotated[origin[element], MaxLen(limit)]
    if origin is dict:
        key, value = (bound_annotation(a) for a in args) if args else (str, Any)
        return Annotated[dict[key, value], MaxLen(_MAX_DICT)]
    return ann


def partial_model(model: type[BaseModel], name: str, overrides: Optional[dict[str, Any]] = None) -> type[BaseModel]:
    """``overrides`` maps a field name to the annotation to use instead of the
    automatically bounded one (for example a 0-5 skill level dict)."""
    overrides = overrides or {}
    fields = {}
    for key, info in model.model_fields.items():
        # A constraint declared at the top level of an annotation is lifted
        # into ``info.metadata``; put it back so it travels with the type.
        ann = Annotated[(info.annotation, *info.metadata)] if info.metadata else info.annotation
        fields[key] = (overrides.get(key) or bound_annotation(ann), None)
    return create_model(name, __config__=model.model_config, **fields)
