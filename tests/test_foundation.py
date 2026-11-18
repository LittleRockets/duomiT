import unittest

from duomit.errors import DuomitError
from duomit.foundation import (ConfigurationField, ConfigurationProfile, ConfigurationSchema,
                               EventEnvelope, OrganizationContext, PinnedReference,
                               resolve_configuration, utc_timestamp)


class FoundationTests(unittest.TestCase):
    def test_organization_context_requires_trusted_identifiers_and_revision(self):
        context = OrganizationContext("org", "member", "principal", "device", "session", "request", "trace", 1)
        self.assertEqual(context.organization_id, "org")
        with self.assertRaises(DuomitError):
            OrganizationContext("bad id", "member", "principal", "device", "session", "request", "trace", 1)

    def test_pinned_reference_requires_sha256_digest(self):
        reference = PinnedReference("artifact", "a", "1", "a" * 64)
        self.assertEqual(reference.to_dict()["resource_id"], "a")
        with self.assertRaises(DuomitError):
            PinnedReference("artifact", "a", "1", "bad")

    def test_event_envelope_keeps_payload_as_reference(self):
        event = EventEnvelope("event", "task.created", "1.0", "org", "task", 1,
                              "2026-11-18T00:00:00Z", PinnedReference("payload", "p", "1", "b" * 64), "trace")
        self.assertEqual(event.event_type, "task.created")
        with self.assertRaises(DuomitError):
            EventEnvelope("event", "unknown", "1.0", "org", "task", 1,
                          "2026-11-18T00:00:00Z", event.payload_ref, "trace")

    def test_timestamp_requires_utc(self):
        self.assertEqual(utc_timestamp("2026-11-18T01:02:03+00:00"), "2026-11-18T01:02:03Z")
        with self.assertRaises(DuomitError):
            utc_timestamp("2026-11-18T01:02:03+08:00")

    def test_configuration_resolution_uses_ordered_overrides(self):
        schema = ConfigurationSchema("runtime", "1", (
            ConfigurationField("limit", 1), ConfigurationField("locked", "safe", overridable=False),
        ))
        org = ConfigurationProfile("org-profile", "organization_default", "org", "runtime", "1", {"limit": 2}, 1)
        task = ConfigurationProfile("task-profile", "task", "task", "runtime", "1", {"limit": 3}, 2)
        value = resolve_configuration(schema, "task", "task", (org, task))
        self.assertEqual(value.values["limit"], 3)
        self.assertEqual(len(value.profile_references), 2)

    def test_configuration_rejects_unknown_or_locked_override(self):
        schema = ConfigurationSchema("runtime", "1", (ConfigurationField("locked", "safe", overridable=False),))
        profile = ConfigurationProfile("p", "sandbox", "sandbox", "runtime", "1", {"unknown": 1}, 1)
        with self.assertRaises(DuomitError):
            resolve_configuration(schema, "sandbox", "sandbox", (profile,))
        locked = ConfigurationProfile("p", "sandbox", "sandbox", "runtime", "1", {"locked": "unsafe"}, 1)
        with self.assertRaises(DuomitError):
            resolve_configuration(schema, "sandbox", "sandbox", (locked,))

    def test_configuration_rejects_duplicate_and_out_of_order_scopes(self):
        schema = ConfigurationSchema("runtime", "1", (ConfigurationField("limit", 1),))
        task = ConfigurationProfile("task", "task", "task", "runtime", "1", {}, 1)
        org = ConfigurationProfile("org", "organization_default", "org", "runtime", "1", {}, 1)
        with self.assertRaises(DuomitError):
            resolve_configuration(schema, "task", "task", (task, org))
        duplicate = ConfigurationProfile("other", "task", "other", "runtime", "1", {}, 1)
        with self.assertRaises(DuomitError):
            resolve_configuration(schema, "task", "task", (task, duplicate))
