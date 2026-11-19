"""Placeholder for ironic/common/states.py FSM edges.

Used by S3 invariant.
"""

# TODO: transcribe legal edges from ironic/common/states.py
LEGAL_EDGES: set[tuple[str, str]] = {
    ("available", "active"),
    ("active", "available"),
    ("deploying", "active"),
    ("deploying", "deploy failed"),
}
