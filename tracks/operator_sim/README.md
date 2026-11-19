# operator_sim (Go)

Envtest harness for `abhibongale/ironic-operator` reconciliation.

```bash
go mod tidy
go test ./...
```

TODO P1:
- fake Ironic API (mirror `boundaries/fake_ironic_api.py`)
- invariants O1/O2/O3 as `assert` helpers
- faults: Ironic 500 on Nth call, pod eviction mid-db_sync, concurrent CR edits
