"""Run-domain readiness and observability specifications without operations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import uuid

from .build_specs import BuildSpecification
from .contracts import canonical_json, digest, identifier
from .errors import DuomitError, require
from .foundation import OrganizationContext
from .workflow_specs import ProcessSpecification


@dataclass(frozen=True)
class OperationalTarget:
    target_id: str
    description: str
    environment_class: str
    readiness_conditions: tuple[str, ...]
    reconcile_strategy: str

    def __post_init__(self):
        identifier(self.target_id)
        require(isinstance(self.description, str) and 1 <= len(self.description.strip()) <= 1024, "Invalid operational target")
        require(self.environment_class in ("local", "test", "staging", "production"), "Invalid environment class")
        require(type(self.readiness_conditions) is tuple and self.readiness_conditions
                and all(isinstance(item, str) and 1 <= len(item.strip()) <= 1024 for item in self.readiness_conditions),
                "Invalid readiness conditions")
        require(isinstance(self.reconcile_strategy, str) and 1 <= len(self.reconcile_strategy.strip()) <= 2048,
                "Invalid reconcile strategy")


@dataclass(frozen=True)
class ObservationRule:
    signal_id: str
    metric: str
    response: str

    def __post_init__(self):
        identifier(self.signal_id); identifier(self.metric)
        require(self.response in ("notify", "pause", "require_review"), "Invalid observation response")


@dataclass(frozen=True)
class RunSpecification:
    run_spec_id: str
    build_spec_id: str
    process_spec_id: str
    targets: tuple[OperationalTarget, ...]
    observations: tuple[ObservationRule, ...]
    digest: str


class SqliteRunSpecStore:
    def __init__(self, database: str | Path):
        self._db = sqlite3.connect(str(database), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""CREATE TABLE IF NOT EXISTS run_specs (
          id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_membership_id TEXT NOT NULL,
          build_spec_id TEXT NOT NULL, body_json TEXT NOT NULL, content_digest TEXT NOT NULL,
          UNIQUE(organization_id,owner_membership_id,build_spec_id,content_digest))""")

    def close(self): self._db.close()

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield; self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK"); raise

    def derive(self, context: OrganizationContext, build: BuildSpecification,
               process: ProcessSpecification) -> RunSpecification:
        require(isinstance(build, BuildSpecification) and isinstance(process, ProcessSpecification), "Invalid run specification inputs")
        if build.process_spec_id != process.process_spec_id:
            raise DuomitError("DOMAIN_HANDOFF_INVALID", "Run inputs are not a valid Build/Workflow handoff")
        target = OperationalTarget(digest({"build": build.build_spec_id, "kind": "target"})[:32],
                                   build.deliverables[0].description, "local", ("blocking verification passed",),
                                   "Pause and require human reconciliation for unknown outcomes")
        observation = ObservationRule(digest({"build": build.build_spec_id, "metric": "verification_status"})[:32],
                                      "verification_status", "require_review")
        body = {"build_spec_id": build.build_spec_id, "process_spec_id": process.process_spec_id,
                "targets": [target.__dict__], "observations": [observation.__dict__]}
        content_digest = digest(body)
        with self._transaction():
            existing = self._db.execute("SELECT id,body_json,content_digest FROM run_specs WHERE organization_id=? AND owner_membership_id=? AND build_spec_id=? AND content_digest=?",
                                        (context.organization_id, context.membership_id, build.build_spec_id, content_digest)).fetchone()
            if existing is not None:
                return self._from_body(existing["id"], json.loads(existing["body_json"]), existing["content_digest"])
            run_spec_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO run_specs VALUES (?,?,?,?,?,?)",
                             (run_spec_id, context.organization_id, context.membership_id, build.build_spec_id,
                              canonical_json(body), content_digest))
            return self._from_body(run_spec_id, body, content_digest)

    @staticmethod
    def _from_body(run_spec_id: str, body: dict, content_digest: str) -> RunSpecification:
        return RunSpecification(run_spec_id, body["build_spec_id"], body["process_spec_id"],
                                tuple(OperationalTarget(**{**item, "readiness_conditions": tuple(item["readiness_conditions"])}) for item in body["targets"]),
                                tuple(ObservationRule(**item) for item in body["observations"]), content_digest)
