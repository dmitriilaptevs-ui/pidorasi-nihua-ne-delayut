# Black-box integration suites (live stack)

Reserved for pytest suites that talk to a **running** deployment over HTTP and
never import `app.*`.

These tests must not be collected by the default unit run, because CI does not
have the compose stack. When the first suite lands here, gate it explicitly —
e.g. `pytest -m blackbox` behind a marker plus an env flag
(`RUBAI_BLACKBOX_BASE_URL`), and add the marker to `pytest.ini` so plain
`pytest` in `apps/api` keeps skipping them.

Current live acceptance packs live in `scripts/acceptance/` (see
`scripts/acceptance/README.md`) because they need container log access for
console-mail tokens and the admin bootstrap exec.

Planned suites (pending the corresponding API):

- `test_ledger_live.py` — 100-concurrent invite/reserve/settle invariants
- `test_gateway_live.py` — OpenAI SDK free+paid call, usage recorded
- `test_payments_live.py` — sandbox webhook replay/forgery, exactly one credit
