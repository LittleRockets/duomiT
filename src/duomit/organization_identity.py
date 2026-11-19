"""Organization identity contracts; OIDC adapters must produce these trusted records."""
from dataclasses import dataclass
from .contracts import identifier
from .errors import require
from .foundation import revision, utc_timestamp

MEMBERSHIP_STATES = ("invited", "active", "suspended", "removed")
DEVICE_STATES = ("pending", "active", "revoked")
SESSION_STATES = ("active", "refreshing", "locked", "expired", "revoked")

@dataclass(frozen=True)
class OrganizationMembership:
    membership_id: str; organization_id: str; issuer: str; subject: str; state: str; revision: int
    def __post_init__(self):
        for value in (self.membership_id, self.organization_id, self.subject): identifier(value)
        require(isinstance(self.issuer, str) and self.issuer.startswith("https://"), "Invalid identity issuer")
        require(self.state in MEMBERSHIP_STATES, "Invalid membership state"); revision(self.revision)

@dataclass(frozen=True)
class DeviceRegistration:
    device_id: str; organization_id: str; membership_id: str; public_key: str; state: str; revision: int
    def __post_init__(self):
        for value in (self.device_id, self.organization_id, self.membership_id): identifier(value)
        require(isinstance(self.public_key, str) and 16 <= len(self.public_key) <= 8192, "Invalid device key")
        require(self.state in DEVICE_STATES, "Invalid device state"); revision(self.revision)

@dataclass(frozen=True)
class AuthenticatedSession:
    session_id: str; organization_id: str; membership_id: str; device_id: str; state: str; expires_at: str; policy_revision: int
    def __post_init__(self):
        for value in (self.session_id, self.organization_id, self.membership_id, self.device_id): identifier(value)
        require(self.state in SESSION_STATES, "Invalid session state"); utc_timestamp(self.expires_at); revision(self.policy_revision)

def session_is_usable(membership: OrganizationMembership, device: DeviceRegistration, session: AuthenticatedSession) -> bool:
    return (membership.state == "active" and device.state == "active" and session.state == "active"
            and membership.organization_id == device.organization_id == session.organization_id
            and membership.membership_id == device.membership_id == session.membership_id
            and device.device_id == session.device_id)
