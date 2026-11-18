"""Versioned foundation contracts for the local-first duomiT product.

These are provider-neutral, dependency-free public contracts.  They deliberately
do not authenticate callers, read secrets, access storage, or schedule work.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import canonical_json, digest, identifier
from .errors import DuomitError, require


CONFIG_SCOPES = (
    "organization_default", "project", "sandbox", "task",
)


def event_type(value: Any) -> str:
    """Validate an extensible dotted event name without a core allow-list.

    Concrete event availability belongs to a versioned schema registry; this
    public foundation layer only prevents malformed or ambiguous names.
    """
    require(isinstance(value, str) and 3 <= len(value) <= 128, "Invalid event type")
    segments = value.split(".")
    require(len(segments) >= 2 and all(segment and segment.replace("_", "").isalnum()
            and segment.isascii() and segment == segment.lower() for segment in segments),
            "Invalid event type")
    return value


def utc_timestamp(value: str) -> str:
    require(isinstance(value, str) and 20 <= len(value) <= 40, "Invalid UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DuomitError("INVALID_CONTRACT", "Invalid UTC timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed),
            "Timestamp must be UTC")
    return parsed.isoformat().replace("+00:00", "Z")


def revision(value: Any) -> int:
    require(type(value) is int and value > 0, "Invalid revision")
    return value


@dataclass(frozen=True)
class OrganizationContext:
    """Trusted request context created only by an identity/session adapter."""

    organization_id: str
    membership_id: str
    principal_id: str
    device_id: str
    session_id: str
    request_id: str
    trace_id: str
    policy_revision: int

    def __post_init__(self):
        for value in (self.organization_id, self.membership_id, self.principal_id,
                      self.device_id, self.session_id, self.request_id, self.trace_id):
            identifier(value)
        revision(self.policy_revision)


@dataclass(frozen=True)
class PinnedReference:
    """An opaque immutable resource version, never an authorization grant."""

    resource_type: str
    resource_id: str
    version: str
    content_digest: str

    def __post_init__(self):
        for value in (self.resource_type, self.resource_id, self.version):
            identifier(value)
        require(isinstance(self.content_digest, str) and len(self.content_digest) == 64
                and all(char in "0123456789abcdef" for char in self.content_digest),
                "Invalid content digest")

    def to_dict(self) -> dict[str, str]:
        return {"resource_type": self.resource_type, "resource_id": self.resource_id,
                "version": self.version, "content_digest": self.content_digest}


@dataclass(frozen=True)
class EventEnvelope:
    """At-least-once event metadata; payload remains an authorized artifact reference."""

    event_id: str
    event_type: str
    schema_version: str
    organization_id: str
    aggregate_id: str
    aggregate_revision: int
    occurred_at: str
    payload_ref: PinnedReference
    trace_id: str
    causation_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None

    def __post_init__(self):
        identifier(self.event_id)
        event_type(self.event_type)
        identifier(self.schema_version)
        for value in (self.organization_id, self.aggregate_id, self.trace_id):
            identifier(value)
        revision(self.aggregate_revision)
        utc_timestamp(self.occurred_at)
        require(isinstance(self.payload_ref, PinnedReference), "Invalid payload reference")
        for value in (self.causation_id, self.task_id, self.run_id):
            if value is not None:
                identifier(value)


@dataclass(frozen=True)
class ConfigurationField:
    name: str
    default: Any
    overridable: bool = True
    sensitive: bool = False

    def __post_init__(self):
        identifier(self.name)
        require(type(self.overridable) is bool and type(self.sensitive) is bool,
                "Invalid configuration field")
        canonical_json(self.default)


@dataclass(frozen=True)
class ConfigurationSchema:
    schema_id: str
    version: str
    fields: tuple[ConfigurationField, ...]

    def __post_init__(self):
        identifier(self.schema_id)
        identifier(self.version)
        require(type(self.fields) is tuple and 0 < len(self.fields) <= 128
                and all(isinstance(field, ConfigurationField) for field in self.fields),
                "Invalid configuration schema")
        require(len({field.name for field in self.fields}) == len(self.fields),
                "Duplicate configuration field")


@dataclass(frozen=True)
class ConfigurationProfile:
    profile_id: str
    scope: str
    scope_id: str
    schema_id: str
    schema_version: str
    values: Mapping[str, Any]
    profile_revision: int

    def __post_init__(self):
        for value in (self.profile_id, self.scope_id, self.schema_id, self.schema_version):
            identifier(value)
        require(self.scope in CONFIG_SCOPES, "Invalid configuration scope")
        require(isinstance(self.values, Mapping), "Invalid configuration values")
        canonical_json(dict(self.values))
        revision(self.profile_revision)


@dataclass(frozen=True)
class ConfigurationResolution:
    target_type: str
    target_id: str
    schema_id: str
    schema_version: str
    profile_references: tuple[PinnedReference, ...]
    values: Mapping[str, Any]
    effective_digest: str

    def __post_init__(self):
        for value in (self.target_type, self.target_id, self.schema_id, self.schema_version):
            identifier(value)
        require(type(self.profile_references) is tuple and all(
            isinstance(item, PinnedReference) for item in self.profile_references),
            "Invalid configuration references")
        require(isinstance(self.values, Mapping), "Invalid resolved values")
        require(self.effective_digest == digest(dict(self.values)), "Configuration digest mismatch")


def resolve_configuration(schema: ConfigurationSchema, target_type: str, target_id: str,
                          profiles: tuple[ConfigurationProfile, ...]) -> ConfigurationResolution:
    """Resolve profiles from lowest to highest scope without accepting unknown fields."""
    identifier(target_type)
    identifier(target_id)
    require(type(profiles) is tuple and len(profiles) <= len(CONFIG_SCOPES),
            "Invalid configuration profiles")
    expected_order = {scope: index for index, scope in enumerate(CONFIG_SCOPES)}
    require(len({profile.scope for profile in profiles}) == len(profiles),
            "Duplicate configuration scope")
    require(list(profiles) == sorted(profiles, key=lambda item: expected_order[item.scope]),
            "Configuration scopes are out of order")
    fields = {field.name: field for field in schema.fields}
    values = {field.name: field.default for field in schema.fields}
    references = []
    for profile in profiles:
        require(profile.schema_id == schema.schema_id and profile.schema_version == schema.version,
                "Configuration schema mismatch")
        unknown = set(profile.values) - set(fields)
        require(not unknown, "Unknown configuration field")
        for name, value in profile.values.items():
            field = fields[name]
            require(field.overridable or values[name] == value,
                    "Configuration field is locked")
            canonical_json(value)
            values[name] = value
        references.append(PinnedReference("config_profile", profile.profile_id,
                                          str(profile.profile_revision),
                                          digest({"scope": profile.scope, "values": dict(profile.values)})))
    return ConfigurationResolution(target_type, target_id, schema.schema_id, schema.version,
                                   tuple(references), values, digest(values))
