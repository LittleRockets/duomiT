import unittest

from duomit.contracts import ModelResponse, Plan, RequestContext, Step, canonical_json
from duomit.errors import DuomitError
from duomit.model_access import BuilderBinding, ModelBroker, ModelMaterial, ReferenceDesktop
from duomit.models import IndependentModel, SequentialModel
from duomit.planning import ReferencePlanner
from duomit.registry import Capability, DomainRegistry


class OutputModel:
    def __init__(self, content):
        self.content = content

    def generate(self, request, *, timeout_seconds):
        return ModelResponse(self.content, "fixture", 0, 0)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.context = RequestContext("tenant", "alice", "request", "trace")
        self.registry = DomainRegistry()
        self.registry.register(Capability("data.read", "1.0"))
        self.registry.register(Capability("report.render", "1.0", True))
        self.desktop = ReferenceDesktop()

    def planner(self, adapter, adapter_id="fixture"):
        material = ModelMaterial("model", "1.0", adapter_id)
        self.desktop.register(self.context, material)
        binding = BuilderBinding("tenant", "alice", "binding", 1, material,
                                 ("data.read", "report.render"))
        return ReferencePlanner(ModelBroker(self.desktop, {adapter_id: adapter}),
                                self.registry, binding)

    def test_two_model_adapters_are_interchangeable(self):
        sequential = self.planner(SequentialModel(), "sequence").propose(self.context, "report")
        # Different immutable versions use a fresh fixture catalog.
        self.desktop = ReferenceDesktop()
        independent = self.planner(IndependentModel(), "independent").propose(self.context, "report")
        self.assertEqual(sequential.steps[1].depends_on, ("step-0",))
        self.assertEqual(independent.steps[1].depends_on, ())
        self.assertTrue(sequential.steps[1].requires_approval)
        self.assertTrue(independent.steps[1].requires_approval)

    def test_model_cannot_remove_mandatory_approval(self):
        content = canonical_json(Plan((Step("a", "report.render"),)).to_dict())
        plan = self.planner(OutputModel(content)).propose(self.context, "report")
        self.assertTrue(plan.steps[0].requires_approval)

    def test_unregistered_and_unauthorized_capabilities_rejected(self):
        for capability in ("admin.delete", "missing"):
            with self.subTest(capability=capability), self.assertRaises(DuomitError):
                self.planner(OutputModel(canonical_json(
                    Plan((Step("a", capability),)).to_dict()))).propose(self.context, "goal")

    def test_cross_user_and_cross_tenant_denied_before_model_call(self):
        planner = self.planner(SequentialModel())
        for tenant, principal in (("tenant", "bob"), ("other", "alice")):
            with self.subTest(tenant=tenant, principal=principal), self.assertRaises(DuomitError) as err:
                planner.propose(RequestContext(tenant, principal, "r", "t"), "goal")
            self.assertEqual(err.exception.code, "NOT_FOUND")

    def test_material_must_be_registered_and_immutable(self):
        material = ModelMaterial("model", "1.0", "fixture")
        with self.assertRaises(DuomitError):
            self.desktop.acquire(self.context, material)
        self.desktop.register(self.context, material)
        with self.assertRaises(DuomitError):
            self.desktop.register(self.context, ModelMaterial("model", "1.0", "changed"))

    def test_timeout_and_provider_error_are_sanitized(self):
        class Broken:
            def __init__(self, error):
                self.error = error

            def generate(self, request, *, timeout_seconds):
                raise self.error("secret-token")

        for error, code in ((TimeoutError, "MODEL_TIMEOUT"), (RuntimeError, "MODEL_FAILED")):
            with self.subTest(error=error), self.assertRaises(DuomitError) as result:
                self.planner(Broken(error)).propose(self.context, "private goal")
            self.assertEqual(result.exception.code, code)
            self.assertNotIn("secret", str(result.exception))

    def test_invalid_model_output_rejected(self):
        with self.assertRaises(DuomitError):
            self.planner(OutputModel("I cannot return JSON")).propose(self.context, "goal")


if __name__ == "__main__":
    unittest.main()
