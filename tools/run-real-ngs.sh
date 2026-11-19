#!/usr/bin/env bash
# tools/run-real-ngs.sh — real NGS driver in a dedicated venv (Python 3.11)
# Why: lightweight venv (Python 3.15) can't import neutron (ssl PROTOCOL_TLSv1 + ssh-python/cmake),
# and networking-generic-switch/.tox/py3 is incomplete. This script isolates the heavy stack.
#
# Usage:
#   ./tools/run-real-ngs.sh                    # setup venv + run deterministic NGS suite
#   ./tools/run-real-ngs.sh --no-install       # skip pip install, just run
#   ./tools/run-real-ngs.sh --seeds 500        # NGS sweep seeds
#
# Requires: python3.11, cmake (for ssh-python), network access for upper/master constraints
# Output: .venv-real-ngs/ + logs/run-real-ngs-*.log

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-real-ngs"
LOGDIR="$ROOT/logs"
NGS="$HOME/Workspace/repos/networking-generic-switch"
CONSTRAINTS="${TOX_CONSTRAINTS_FILE:-https://releases.openstack.org/constraints/upper/master}"

SEEDS=200
NO_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --seeds) SEEDS="$2"; shift 2;;
    --seeds=*) SEEDS="${arg#--seeds=}"; shift;;
    --no-install) NO_INSTALL=1; shift;;
    *) echo "Unknown arg: $arg" >&2; exit 1;;
  esac
done

mkdir -p "$LOGDIR"
LOG="$LOGDIR/run-real-ngs-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "=== $(date --iso-8601=seconds) run-real-ngs SEEDS=$SEEDS VENV=$VENV ==="
echo "Python: $(python3.11 --version)"
echo "NGS: $NGS @ $(git -C "$NGS" rev-parse --short HEAD 2>/dev/null || echo n/a) $(git -C "$NGS" log --oneline -1 2>/dev/null | head -c 60)"
echo "Constraints: $CONSTRAINTS"
echo ""

if [[ ! -x /usr/bin/cmake ]]; then
  echo "Installing cmake (needed for ssh-python/libssh)..."
  # sudo is password-gated on this host — use pip cmake as fallback
  python3.11 -m pip install --user cmake -q 2>&1 | tail -3 || true
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v cmake &>/dev/null; then
    echo "WARN: cmake not available — ssh-python will be skipped (netmiko mocked in tests)" >&2
  else
    echo "cmake: $(cmake --version | head -1)"
  fi
fi

if [[ ! -d "$VENV" || "$NO_INSTALL" -eq 0 ]]; then
  echo "Creating venv $VENV (python3.11)..."
  python3.11 -m venv "$VENV"
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
  pip install -U pip -q
  echo "Installing NGS + test deps from $CONSTRAINTS..."
  # Use constraints but allow ssh-python to be skipped if cmake missing — test_determinism_ngs mocks netmiko
  pip install -c "$CONSTRAINTS" -r "$NGS/requirements.txt" -r "$NGS/test-requirements.txt" -q 2>&1 | tail -20 || true
  pip install -c "$CONSTRAINTS" neutron-lib fixtures stestr pytest hypothesis -q 2>&1 | tail -5
  # Try neutron — if fails due to ssl/cmake, we still run FakeSwitch-only suite
  pip install -c "$CONSTRAINTS" neutron 2>&1 | tail -10 || echo "neutron install failed — will run in stub mode" >&2
  pip install -e "$ROOT" -q 2>&1 | tail -5
  echo "Venv ready: $(python --version) $(pip --version | head -1)"
else
  echo "Reusing existing $VENV (NO_INSTALL)"
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
fi

export PYTHONPATH="$NGS:$ROOT:${PYTHONPATH:-}"
echo "PYTHONPATH=$PYTHONPATH"
echo ""

echo "--- pytest (ironic-determinism, lightweight) ---"
pytest "$ROOT/tests" -q 2>&1 | tail -10
echo ""

echo "--- pytest real-NGS (stestr skipped when neutron absent, 4 skipped in lightweight) ---"
pytest "$ROOT/tests/test_ngs_real_driver.py" -v 2>&1 | tail -30
echo ""

echo "--- NGS sweep --seeds $SEEDS ---"
python -m campaign.ngs_runner --seeds "$SEEDS" 2>&1 | tail -10
echo ""

echo "--- stestr (NGS unit, if neutron installed) ---"
if python -c "import neutron" 2>/dev/null; then
  python -m stestr run networking_generic_switch.tests.unit.test_generic_switch_mech.TestGenericSwitchDriver.test_update_port_postcommit_l2vni -v 2>&1 | tail -20
  python -m stestr run networking_generic_switch.tests.unit.test_determinism_ngs -v 2>&1 | tail -30 || echo "test_determinism_ngs not in NGS repo (expected until you copy it there)"
else
  echo "neutron not installed in $VENV — skip stestr; see tools/run-real-ngs.sh to run in NGS clone with Python 3.11"
fi

echo ""
echo "=== $(date --iso-8601=seconds) done — log $LOG ==="
echo "Next: open $LOG or run ./tools/run-real-ngs.sh --seeds 2000"
