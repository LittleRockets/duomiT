import unittest

from duomit.errors import DuomitError
from duomit.schema_registry import (SchemaDefinition, SchemaRegistry,
                                    assert_backward_compatible, semantic_version)


def schema(version, properties, required, compatibility="backward"):
    return SchemaDefinition("event_envelope", version,
                            {"type": "object", "properties": properties, "required": required},
                            compatibility)


class SchemaRegistryTests(unittest.TestCase):
    def setUp(self):
        self.v1 = schema("1.0.0", {"event_id": {"type": "string"}}, ["event_id"])

    def test_versions_are_strict_semver(self):
        self.assertEqual(semantic_version("1.2.3"), (1, 2, 3))
        for value in ("1", "1.2", "01.2.3", "v1.2.3", "1.2.x"):
            with self.subTest(value=value), self.assertRaises(DuomitError):
                semantic_version(value)

    def test_registration_is_immutable_and_resolvable(self):
        registry = SchemaRegistry()
        self.assertEqual(registry.register(self.v1), self.v1)
        self.assertEqual(registry.resolve("event_envelope", "1.0.0"), self.v1)
        self.assertEqual(registry.register(self.v1), self.v1)
        conflicting = schema("1.0.0", {"other": {"type": "string"}}, ["other"])
        with self.assertRaises(DuomitError):
            registry.register(conflicting)

    def test_backward_schema_can_add_optional_fields(self):
        registry = SchemaRegistry()
        registry.register(self.v1)
        v2 = schema("1.1.0", {"event_id": {"type": "string"}, "trace_id": {"type": "string"}}, ["event_id"])
        registry.register(v2)
        self.assertEqual([item.version for item in registry.versions("event_envelope")], ["1.0.0", "1.1.0"])

    def test_backward_schema_cannot_change_or_remove_existing_contract(self):
        for candidate in (
            schema("1.1.0", {"event_id": {"type": "integer"}}, ["event_id"]),
            schema("1.1.0", {"trace_id": {"type": "string"}}, ["trace_id"]),
            schema("1.1.0", {"event_id": {"type": "string"}, "trace_id": {"type": "string"}}, ["event_id", "trace_id"]),
        ):
            with self.subTest(candidate=candidate.document), self.assertRaises(DuomitError):
                assert_backward_compatible(self.v1, candidate)

    def test_registry_rejects_non_increasing_versions(self):
        registry = SchemaRegistry()
        registry.register(self.v1)
        with self.assertRaises(DuomitError):
            registry.register(schema("0.9.0", {"event_id": {"type": "string"}}, ["event_id"]))
