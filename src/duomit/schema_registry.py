"""A small immutable JSON Schema registry for public duomiT contracts.

The registry intentionally validates only the conservative object-schema subset
needed for compatibility gates.  Adapters may use a fuller JSON Schema engine
at their boundary, but cannot bypass these versioning rules.
"""

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import canonical_json, digest, identifier
from .errors import DuomitError, require


def semantic_version(value: str) -> tuple[int, int, int]:
    require(isinstance(value, str), "Invalid schema version")
    parts = value.split(".")
    require(len(parts) == 3 and all(part.isascii() and part.isdecimal()
            and (part == "0" or not part.startswith("0")) for part in parts),
            "Invalid schema version")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _object_schema(value: Mapping[str, Any]) -> tuple[dict[str, Any], set[str]]:
    require(isinstance(value, Mapping) and value.get("type") == "object",
            "Schema must be an object schema")
    properties = value.get("properties", {})
    required = value.get("required", [])
    require(isinstance(properties, Mapping) and isinstance(required, list)
            and all(isinstance(key, str) for key in properties)
            and all(isinstance(key, str) for key in required)
            and set(required) <= set(properties), "Invalid object schema fields")
    for name, field in properties.items():
        identifier(name)
        require(isinstance(field, Mapping) and isinstance(field.get("type"), str),
                "Every property requires a type")
    return dict(properties), set(required)


@dataclass(frozen=True)
class SchemaDefinition:
    schema_id: str
    version: str
    document: Mapping[str, Any]
    compatibility: str = "backward"

    def __post_init__(self):
        identifier(self.schema_id)
        semantic_version(self.version)
        require(self.compatibility in ("backward", "none"), "Invalid compatibility mode")
        canonical_json(dict(self.document))
        _object_schema(self.document)

    @property
    def content_digest(self) -> str:
        return digest({"schema_id": self.schema_id, "version": self.version,
                       "document": dict(self.document), "compatibility": self.compatibility})


def assert_backward_compatible(previous: SchemaDefinition, candidate: SchemaDefinition) -> None:
    """Ensure old readers can accept a new version of the same object schema."""
    require(previous.schema_id == candidate.schema_id, "Schema identifiers differ")
    old_properties, old_required = _object_schema(previous.document)
    new_properties, new_required = _object_schema(candidate.document)
    require(old_required <= new_required, "New schema removes a required field")
    require(not (new_required - old_required), "New schema adds a required field")
    for name, old_field in old_properties.items():
        require(name in new_properties, "New schema removes a field")
        require(new_properties[name]["type"] == old_field["type"],
                "New schema changes a field type")


class SchemaRegistry:
    """In-memory reference registry; persistent adapters implement this contract later."""

    def __init__(self):
        self._definitions: dict[tuple[str, str], SchemaDefinition] = {}

    def register(self, definition: SchemaDefinition) -> SchemaDefinition:
        require(isinstance(definition, SchemaDefinition), "Invalid schema definition")
        key = (definition.schema_id, definition.version)
        existing = self._definitions.get(key)
        if existing is not None:
            require(existing.content_digest == definition.content_digest,
                    "Schema version is immutable")
            return existing
        versions = [item for item in self._definitions.values()
                    if item.schema_id == definition.schema_id]
        if versions:
            latest = max(versions, key=lambda item: semantic_version(item.version))
            require(semantic_version(definition.version) > semantic_version(latest.version),
                    "Schema version must increase")
            if latest.compatibility == "backward":
                assert_backward_compatible(latest, definition)
        self._definitions[key] = definition
        return definition

    def resolve(self, schema_id: str, version: str) -> SchemaDefinition:
        identifier(schema_id)
        semantic_version(version)
        definition = self._definitions.get((schema_id, version))
        if definition is None:
            raise DuomitError("SCHEMA_NOT_FOUND", "Schema is not registered")
        return definition

    def versions(self, schema_id: str) -> tuple[SchemaDefinition, ...]:
        identifier(schema_id)
        return tuple(sorted((item for item in self._definitions.values()
                             if item.schema_id == schema_id),
                            key=lambda item: semantic_version(item.version)))
