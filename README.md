# ironic-determinism

Deterministic simulation testing for **OpenStack Ironic** + **networking-generic-switch** + **ironic-operator** — a personal research project to find integration/lifecycle bugs upstream.

> Plan: `~/Dropbox/Obsidian/RESOURCE/research/2026-11-19-ironic-ngs-operator-deterministic-research-plan.md`
> Vault: `~/Dropbox/Obsidian/RESOURCE/ironic-simulator/` (00–10)

## SUT framing

| Track | SUT | Simulators | Oracle |
|---|---|---|---|
| `tracks/ha_model` | Ironic conductor HA | `boundaries/db.py` + virtual clock/ring | S1–S4 / L1–L3 / SEC1–SEC4 |
| `tracks/ngs_sim` | NGS ML2 driver | `boundaries/fake_switch.py` + fake `tooz` | N1–N3 |
| `tracks/operator_sim` | ironic-operator controller (Go) | `boundaries/fake_ironic_api.py` + envtest | O1–O3 |

Shared deterministic runtime: `runtime/{clock,rng,scheduler}.py`

## Quick start (Python)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# P0 gate: same seed -> identical trace
pytest -v

# Sweep seeds (campaign)
python -m campaign.runner --seeds 100 --max-vtime 10000
```

## Quick start (Go — operator track)

```bash
cd tracks/operator_sim
go test ./...
```

## Layout

```
runtime/           VirtualClock, SeededRng, Scheduler (vtime,seq) heap
boundaries/        InMemoryDb, FakeSwitch, FakeIronicAPI
tracks/ha_model    conductor.py + hash_ring.py + states.py (ported from ironic)
tracks/ngs_sim     NGS driver wired to fake switch
tracks/operator_sim  Go envtest harness
faults/            seeded injector (crash, stall, latency, stale ring)
oracle/            invariants S1..SEC4, N1..N3, O1..O3
campaign/          runner + ddmin shrinker
```

## Phases

P0 spike (1w) → P1 operator (2w) → P2 NGS (2w) → P3 HA hypothesis (2w) → P4 toxiproxy (1w) → P5 hypervisor eval (`openthesis`/`dhyve`)

## References

- FDB `testing.html` + paper, TigerBeetle VOPR, `databases.systems/posts/open-source-antithesis-p1`
- Antithesis deterministic hypervisor (bhyve fork), `openthesis/openthesis`, `pgraug/dhyve-src`, `arnavsinghal09/weft`
