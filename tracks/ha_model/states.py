"""FSM edges — transcribed from ironic/common/states.py + conductor logic.

This is a pragmatic subset covering the HA takeover paths. Full ironic has 30+ states;
we model the stable/unstable split from states.py: STABLE_STATES won't transition without
request; UNSTABLE_STATES can be changed without external request (and thus can be
orphaned when conductor dies — STUCK_STATES_TREATED_AS_FAIL).

For S3 invariant (legal transition check), we treat any edge listed in LEGAL_EDGES as
allowed, including across takeover (new conductor may move node from DEPLOYING→DEPLOYFAIL
if previous holder died).
"""

from __future__ import annotations

# Re-export constants with same names as ironic/common/states.py for adapter ease
ENROLL = "enroll"
MANAGEABLE = "manageable"
AVAILABLE = "available"
ACTIVE = "active"
DEPLOYING = "deploying"
DEPLOYWAIT = "wait call-back"
DEPLOYFAIL = "deploy failed"
CLEANING = "cleaning"
CLEANFAIL = "clean failed"
CLEANWAIT = "clean wait"
ERROR = "error"
INSPECTING = "inspecting"
INSPECTFAIL = "inspect failed"
RESERVED = "reserved"  # synthetic for DB reservation column
NOSTATE = None

STABLE_STATES = frozenset((ENROLL, MANAGEABLE, AVAILABLE, ACTIVE, ERROR))
UNSTABLE_STATES = frozenset((DEPLOYING, DEPLOYWAIT, CLEANING, CLEANWAIT, INSPECTING))
STUCK_AS_FAIL = frozenset((DEPLOYING, CLEANING, INSPECTING))

# Legal edges for S3 check — (src, dst) allowed. Includes takeover recovery edges.
LEGAL_EDGES: set[tuple[str | None, str]] = {
    (None, ENROLL),
    (ENROLL, MANAGEABLE),
    (MANAGEABLE, AVAILABLE),
    (AVAILABLE, DEPLOYING),
    (DEPLOYING, DEPLOYWAIT),
    (DEPLOYWAIT, ACTIVE),
    (DEPLOYING, ACTIVE),
    (DEPLOYING, DEPLOYFAIL),
    (DEPLOYWAIT, DEPLOYFAIL),
    (ACTIVE, CLEANING),
    (CLEANING, CLEANWAIT),
    (CLEANWAIT, AVAILABLE),
    (CLEANING, AVAILABLE),
    (CLEANING, CLEANFAIL),
    (DEPLOYING, ERROR),
    (CLEANING, ERROR),
    (ERROR, MANAGEABLE),
    (ERROR, AVAILABLE),
    # Takeover may force fail from stuck states
    (DEPLOYING, CLEANFAIL),
    (CLEANING, DEPLOYFAIL),
}


def can_transition(src: str | None, dst: str) -> bool:
    return (src, dst) in LEGAL_EDGES
