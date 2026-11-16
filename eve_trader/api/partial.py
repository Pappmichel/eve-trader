"""Update models for the settings save routes.

A settings model has required fields and defaults, so a client that leaves a
field out would reset it to the default. The update model keeps every field's
type (a wrong type or an explicit null on a field that does not take null is
still a 422) but defaults each to "not set"; routes read it with
``model_dump(exclude_unset=True)``, so only what the client sent is saved.
An explicit null for a nullable field is a set value and is kept.
"""
from __future__ import annotations

from pydantic import BaseModel, create_model


def partial_model(model: type[BaseModel], name: str) -> type[BaseModel]:
    fields = {key: (info.annotation, None) for key, info in model.model_fields.items()}
    return create_model(name, __config__=model.model_config, **fields)
