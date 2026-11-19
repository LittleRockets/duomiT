import tempfile
import unittest
from pathlib import Path

from duomit.errors import DuomitError
from duomit.foundation import OrganizationContext
from duomit.resource_access import SqliteResourceAccess


class ResourceAccessTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "build" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.store = SqliteResourceAccess(Path(self.temp.name) / "resources.sqlite3",
                                          now=lambda: "2026-11-19T00:00:00Z")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.owner = OrganizationContext("org", "alice", "alice", "devicea", "sessiona", "requesta", "tracea", 1)
        self.reader = OrganizationContext("org", "bob", "bob", "deviceb", "sessionb", "requestb", "traceb", 1)
        self.other_org = OrganizationContext("other", "eve", "eve", "devicee", "sessione", "requeste", "tracee", 1)

    def test_artifact_id_is_not_permission_and_exact_grant_allows_read(self):
        artifact = self.store.create_artifact(self.owner, "brief", b"private", shareability="grantable")
        grant = self.store.issue_grant(self.owner, artifact, "bob", ("read_content", "use_as_context"), "task_context")
        with self.assertRaises(DuomitError):
            self.store.read_granted_artifact(self.reader, artifact.artifact_id)
        self.assertEqual(self.store.read_granted_artifact(self.reader, grant.grant_id), b"private")
        with self.assertRaises(DuomitError):
            self.store.read_granted_artifact(self.other_org, grant.grant_id)

    def test_owner_only_artifact_cannot_be_granted(self):
        artifact = self.store.create_artifact(self.owner, "private", b"body")
        with self.assertRaises(DuomitError):
            self.store.issue_grant(self.owner, artifact, "bob", ("read_content",), "task_review")

    def test_context_revalidates_grants_after_revocation(self):
        artifact = self.store.create_artifact(self.owner, "brief", b"private", shareability="grantable")
        grant = self.store.issue_grant(self.owner, artifact, "bob", ("read_content", "use_as_context"), "task_context")
        package = self.store.create_context_package(self.reader, "taskone", "task_context", (grant.grant_id,))
        self.assertEqual(self.store.resolve_context_package(self.reader, package.context_package_id), (artifact,))
        revoked = self.store.revoke_grant(self.owner, grant.grant_id, grant.revision)
        self.assertEqual(revoked.state, "revoked")
        with self.assertRaises(DuomitError):
            self.store.resolve_context_package(self.reader, package.context_package_id)


if __name__ == "__main__":
    unittest.main()
