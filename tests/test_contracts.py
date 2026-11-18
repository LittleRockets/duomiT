import unittest

from duomit.contracts import ModelRequest, ModelResponse, Plan, Step, strict_json
from duomit.errors import DuomitError


class ContractTests(unittest.TestCase):
    def test_round_trip_and_digest(self):
        plan = Plan((Step("report", "report.render", ("data",)),
                     Step("data", "data.read")))
        self.assertEqual(plan, Plan.from_dict(plan.to_dict()))
        self.assertEqual(len(plan.content_digest), 64)

    def test_bad_graphs(self):
        for steps in ((Step("a", "read", ("b",)),),
                      (Step("a", "read", ("a",)),),
                      (Step("a", "read", ("b",)), Step("b", "read", ("a",))),
                      (Step("a", "read"), Step("a", "read"))):
            with self.subTest(steps=steps), self.assertRaises(DuomitError):
                Plan(steps)

    def test_model_output_cannot_supply_identity_or_permissions(self):
        data = Plan((Step("a", "read"),)).to_dict()
        for key in ("tenant_id", "principal_id", "permissions"):
            with self.subTest(key=key), self.assertRaises(DuomitError):
                Plan.from_dict(dict(data, **{key: "admin"}))

    def test_strict_json(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{', '[' * 2000,
                    '"' + 'a' * 65536 + '"'):
            with self.subTest(raw=raw[:30]), self.assertRaises(DuomitError):
                strict_json(raw)

    def test_invalid_types_and_limits(self):
        for factory in (lambda: Step("a", "read", requires_approval="false"),
                        lambda: ModelRequest("goal", ("read",), True),
                        lambda: ModelResponse("{}", "model", -1, 0),
                        lambda: Plan(()),
                        lambda: Plan((Step("a", "read"),), "2.0")):
            with self.assertRaises(DuomitError):
                factory()


if __name__ == "__main__":
    unittest.main()
