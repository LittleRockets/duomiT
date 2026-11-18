"""Reference metadata registry. Registration never imports or executes plugins."""

from dataclasses import dataclass

from .contracts import identifier
from .errors import DuomitError, require


@dataclass(frozen=True)
class Capability:
    name: str
    version: str
    requires_approval: bool = False

    def __post_init__(self):
        identifier(self.name)
        identifier(self.version)
        require(type(self.requires_approval) is bool, "Invalid approval policy")


class DomainRegistry:
    """Trusted composition root registers metadata; there is no public write API."""

    def __init__(self):
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        if capability.name in self._capabilities:
            raise DuomitError("CONFLICT", "Capability already registered")
        self._capabilities[capability.name] = capability

    def resolve(self, name: str) -> Capability:
        if name not in self._capabilities:
            raise DuomitError("UNKNOWN_CAPABILITY", "Capability is not registered")
        return self._capabilities[name]
