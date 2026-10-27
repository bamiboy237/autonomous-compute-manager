"""Every state machine against an independent copy of its transition table.

Each test enumerates all (from, to) pairs of the status enum, so adding a status or a
transition without updating the expected table here fails.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import product

import pytest

from acm_core.domain import (
    ATTEMPT_STATE_MACHINE,
    DECISION_STATE_MACHINE,
    RUN_STATE_MACHINE,
    ActionKind,
    Actor,
    AgentRole,
    Attempt,
    AttemptStatus,
    Decision,
    DecisionKind,
    DecisionStatus,
    FailureClass,
    ResourceRequest,
    Run,
    RunStatus,
    StateMachine,
)
from acm_core.errors import InvalidInputError, InvalidTransitionError
from acm_core.ids import (
    CampaignId,
    ComputeTargetId,
    ExperimentId,
    ProviderAccountId,
    RunId,
)

R = RunStatus
A = AttemptStatus
D = DecisionStatus

RUN_EXPECTED: dict[RunStatus, set[RunStatus]] = {
    R.PENDING: {R.RUNNING, R.FAILED, R.STOPPED},
    R.RUNNING: {R.RECOVERING, R.SUCCEEDED, R.FAILED, R.STOPPED},
    R.RECOVERING: {R.RUNNING, R.FAILED, R.STOPPED},
    R.SUCCEEDED: set(),
    R.FAILED: set(),
    R.STOPPED: set(),
}

ATTEMPT_EXPECTED: dict[AttemptStatus, set[AttemptStatus]] = {
    A.PENDING: {A.SUBMITTED, A.FAILED, A.CANCELLED},
    A.SUBMITTED: {A.RUNNING, A.CANCELLING, A.CANCELLED, A.FAILED, A.PREEMPTED},
    A.RUNNING: {A.SUCCEEDED, A.FAILED, A.PREEMPTED, A.CANCELLING, A.CANCELLED},
    A.CANCELLING: {A.CANCELLED, A.FAILED, A.SUCCEEDED},
    A.SUCCEEDED: set(),
    A.FAILED: set(),
    A.PREEMPTED: set(),
    A.CANCELLED: set(),
}

DECISION_EXPECTED: dict[DecisionStatus, set[DecisionStatus]] = {
    D.PROPOSED: {D.APPROVED, D.REJECTED, D.EXPIRED},
    D.APPROVED: {D.EXECUTED, D.FAILED},
    D.REJECTED: set(),
    D.EXECUTED: set(),
    D.FAILED: set(),
    D.EXPIRED: set(),
}

MACHINES: list[tuple[StateMachine[StrEnum], dict[StrEnum, set[StrEnum]]]] = [
    (RUN_STATE_MACHINE, RUN_EXPECTED),  # type: ignore[list-item]
    (ATTEMPT_STATE_MACHINE, ATTEMPT_EXPECTED),  # type: ignore[list-item]
    (DECISION_STATE_MACHINE, DECISION_EXPECTED),  # type: ignore[list-item]
]


def _all_pairs() -> list[tuple[StateMachine[StrEnum], StrEnum, StrEnum, bool]]:
    cases: list[tuple[StateMachine[StrEnum], StrEnum, StrEnum, bool]] = []
    for machine, expected in MACHINES:
        for src, dst in product(machine.status_type, repeat=2):
            cases.append((machine, src, dst, dst in expected[src]))
    return cases


@pytest.mark.parametrize(
    ("machine", "src", "dst", "legal"),
    _all_pairs(),
    ids=lambda v: v.name if isinstance(v, StateMachine) else str(v),
)
def test_every_transition(
    machine: StateMachine[StrEnum], src: StrEnum, dst: StrEnum, legal: bool
) -> None:
    assert machine.can_transition(src, dst) is legal
    if legal:
        machine.check(src, dst)
    else:
        with pytest.raises(InvalidTransitionError) as exc:
            machine.check(src, dst)
        assert exc.value.code == "invalid_transition"
        assert exc.value.details["from"] == src.value
        assert exc.value.details["to"] == dst.value


@pytest.mark.parametrize(("machine", "expected"), MACHINES, ids=lambda v: getattr(v, "name", ""))
def test_terminal_states_and_initial(
    machine: StateMachine[StrEnum], expected: dict[StrEnum, set[StrEnum]]
) -> None:
    assert machine.terminal == {s for s, dsts in expected.items() if not dsts}
    assert machine.transitions() == {(s, d) for s, dsts in expected.items() for d in dsts}
    assert not machine.is_terminal(machine.initial)
    for status in machine.status_type:
        assert machine.allowed_from(status) == expected[status]


class _Color(StrEnum):
    RED = "red"
    GREEN = "green"


def test_machine_requires_every_status() -> None:
    with pytest.raises(ValueError, match="no transitions declared"):
        StateMachine("color", _Color, initial=_Color.RED, transitions={_Color.RED: set()})


def test_machine_rejects_self_transition() -> None:
    with pytest.raises(ValueError, match="self-transition"):
        StateMachine(
            "color",
            _Color,
            initial=_Color.RED,
            transitions={_Color.RED: {_Color.RED}, _Color.GREEN: set()},
        )


T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _run() -> Run:
    return Run(campaign_id=CampaignId.new(), experiment_id=ExperimentId.new())


def test_run_transition_to_happy_path() -> None:
    run = _run()
    assert run.status is RunStatus.PENDING
    running = run.transition_to(RunStatus.RUNNING, at=T0)
    assert running.status is RunStatus.RUNNING
    assert running.started_at == T0
    assert run.status is RunStatus.PENDING  # original untouched
    recovering = running.transition_to(RunStatus.RECOVERING, at=T0 + timedelta(minutes=5))
    again = recovering.transition_to(RunStatus.RUNNING, at=T0 + timedelta(minutes=6))
    assert again.started_at == T0  # first start is kept
    done = again.transition_to(RunStatus.SUCCEEDED, at=T0 + timedelta(hours=1))
    assert done.finished_at == T0 + timedelta(hours=1)
    assert done.updated_at == T0 + timedelta(hours=1)


def test_run_stop_reason_and_illegal() -> None:
    stopped = _run().transition_to(RunStatus.STOPPED, reason="early stop: plateau")
    assert stopped.stop_reason == "early stop: plateau"
    assert stopped.finished_at is not None
    with pytest.raises(InvalidTransitionError):
        stopped.transition_to(RunStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        _run().transition_to(RunStatus.SUCCEEDED)


def _attempt() -> Attempt:
    return Attempt(
        run_id=RunId.new(),
        ordinal=1,
        provider_account_id=ProviderAccountId.new(),
        compute_target_id=ComputeTargetId.new(),
        resources=ResourceRequest(gpus=1, gpu_type="a100-80gb"),
    )


def test_attempt_transition_to() -> None:
    attempt = _attempt()
    submitted = attempt.transition_to(AttemptStatus.SUBMITTED, provider_job_ref="4242", at=T0)
    assert submitted.provider_job_ref == "4242"
    assert submitted.submitted_at == T0
    running = submitted.transition_to(AttemptStatus.RUNNING, at=T0 + timedelta(minutes=1))
    assert running.started_at == T0 + timedelta(minutes=1)
    failed = running.transition_to(
        AttemptStatus.FAILED, failure_class=FailureClass.OOM_GPU, exit_code=137
    )
    assert failed.failure_class is FailureClass.OOM_GPU
    assert failed.exit_code == 137
    assert failed.finished_at is not None
    with pytest.raises(InvalidTransitionError):
        failed.transition_to(AttemptStatus.RUNNING)


def _decision() -> Decision:
    return Decision(
        campaign_id=CampaignId.new(),
        kind=DecisionKind.RECOVERY,
        action=ActionKind.RETRY,
        rationale="transient NCCL error",
        proposed_by=Actor.agent(AgentRole.DIAGNOSTICIAN),
    )


def test_decision_transition_to() -> None:
    human = Actor.human("usr_alice")
    approved = _decision().transition_to(DecisionStatus.APPROVED, actor=human, at=T0)
    assert approved.decided_by == human
    assert approved.decided_at == T0
    executed = approved.transition_to(DecisionStatus.EXECUTED, at=T0 + timedelta(seconds=5))
    assert executed.executed_at == T0 + timedelta(seconds=5)

    failed = approved.transition_to(DecisionStatus.FAILED, error="provider rejected job")
    assert failed.error == "provider rejected job"

    expired = _decision().transition_to(DecisionStatus.EXPIRED)
    assert expired.decided_by is None


def test_decision_needs_actor_to_approve_or_reject() -> None:
    with pytest.raises(InvalidInputError):
        _decision().transition_to(DecisionStatus.APPROVED)
    with pytest.raises(InvalidInputError):
        _decision().transition_to(DecisionStatus.REJECTED)


def test_decision_illegal() -> None:
    with pytest.raises(InvalidTransitionError):
        _decision().transition_to(DecisionStatus.EXECUTED)
