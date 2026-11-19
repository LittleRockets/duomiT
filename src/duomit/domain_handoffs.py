"""Cross-domain handoff validation for Need → Workflow → Build → Run."""

from dataclasses import dataclass

from .build_specs import BuildSpecification
from .contracts import digest
from .errors import DuomitError, require
from .need_records import NeedRecord
from .run_specs import RunSpecification
from .task_revisions import TaskRevision
from .workflow_specs import ProcessSpecification


@dataclass(frozen=True)
class DomainHandoffPlan:
    task_revision_id: str
    need_id: str
    process_spec_id: str
    build_spec_id: str
    run_spec_id: str
    digest: str


def validate_four_domain_handoff(task: TaskRevision, need: NeedRecord, process: ProcessSpecification,
                                 build: BuildSpecification, run: RunSpecification) -> DomainHandoffPlan:
    """Prove reference objects form one fixed planning chain, without execution."""
    require(all(value is not None for value in (task, need, process, build, run)), "Invalid domain handoff")
    if (need.completeness == "insufficient" or need.task_revision_id != task.task_revision_id
            or process.need_id != need.need_id or build.need_id != need.need_id
            or build.process_spec_id != process.process_spec_id
            or build.task_revision_id != task.task_revision_id
            or run.build_spec_id != build.build_spec_id or run.process_spec_id != process.process_spec_id):
        raise DuomitError("DOMAIN_HANDOFF_INVALID", "Four-domain references do not form one valid chain")
    payload = {"task_revision_id": task.task_revision_id, "task_digest": task.digest, "need_id": need.need_id,
               "need_digest": need.digest, "process_spec_id": process.process_spec_id, "process_digest": process.digest,
               "build_spec_id": build.build_spec_id, "build_digest": build.digest, "run_spec_id": run.run_spec_id,
               "run_digest": run.digest}
    return DomainHandoffPlan(task.task_revision_id, need.need_id, process.process_spec_id, build.build_spec_id,
                             run.run_spec_id, digest(payload))
