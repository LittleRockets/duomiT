"""Narrow API boundary for LLM-produced Need candidates.

Network/provider adapters implement ``NeedCandidateApiPort`` elsewhere.  This
module deliberately accepts only a strict structured candidate and sends it
through the same Need validation path as human input.
"""

from __future__ import annotations

from typing import Protocol

from .errors import DuomitError, require
from .foundation import OrganizationContext
from .need_records import NeedCandidate, NeedOutcome, NeedRecord, NeedUnknown, SqliteNeedStore
from .task_revisions import TaskRevision


class NeedCandidateApiPort(Protocol):
    """Provider-neutral API port; implementations may call OpenAI/Gemini/etc."""

    def generate_need_candidate(self, context: OrganizationContext, task: TaskRevision) -> dict: ...


def decode_need_candidate(payload: dict) -> NeedCandidate:
    """Reject fields that could smuggle authority into a model response."""
    require(type(payload) is dict and set(payload) <= {"outcomes", "unknowns", "assumptions"},
            "Invalid model need candidate")
    outcomes = payload.get("outcomes")
    unknowns = payload.get("unknowns", [])
    assumptions = payload.get("assumptions", [])
    require(type(outcomes) is list and type(unknowns) is list and type(assumptions) is list,
            "Invalid model need candidate")
    parsed_outcomes = []
    for item in outcomes:
        require(type(item) is dict and set(item) == {"outcome_id", "statement", "acceptance_criterion_ids"}
                and type(item["acceptance_criterion_ids"]) is list, "Invalid model need outcome")
        parsed_outcomes.append(NeedOutcome(item["outcome_id"], item["statement"], tuple(item["acceptance_criterion_ids"])))
    parsed_unknowns = []
    for item in unknowns:
        require(type(item) is dict and set(item) == {"question_id", "question", "blocking"}, "Invalid model need unknown")
        parsed_unknowns.append(NeedUnknown(item["question_id"], item["question"], item["blocking"]))
    require(all(isinstance(item, str) and 1 <= len(item.strip()) <= 1024 for item in assumptions),
            "Invalid model need assumptions")
    return NeedCandidate(tuple(parsed_outcomes), tuple(parsed_unknowns), tuple(item.strip() for item in assumptions))


class BrokeredNeedNormalizer:
    """Calls a configured API port, then validates output without granting authority."""

    def __init__(self, api: NeedCandidateApiPort, store: SqliteNeedStore):
        require(hasattr(api, "generate_need_candidate") and isinstance(store, SqliteNeedStore),
                "Invalid need normalization dependencies")
        self._api = api
        self._store = store

    def normalize(self, context: OrganizationContext, task: TaskRevision) -> NeedRecord:
        payload = self._api.generate_need_candidate(context, task)
        candidate = decode_need_candidate(payload)
        return self._store.create(context, task, candidate)
