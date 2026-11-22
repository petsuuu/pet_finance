# PR #53 — Manual deployment safety gates (read-only)

These gates **evaluate evidence only**. They do not call deployment APIs, trigger
merges, toggle Render settings, write finance data, or store baselines. Passing
the gate is **not authorization** to deploy. A separate, explicit operator
approval is required for every manual publication.

## Evidence required

Before deployment, the operator must independently retrieve:
- The exact expected 40-character `main` commit SHA, approved for deployment.
- Render service ID, branch and `autoDeploy=no`, `autoDeployTrigger=off`.
- The effective plugin version using OAuth MCP `plugin_info`.
- A fresh complete MCP `financial_audit_snapshot` (schema v2).
- A trusted, access-controlled, tamper-evident copy of the **complete** snapshot,
  associated externally with service ID, approved commit, expected plugin version,
  capture time and the operator's approval record.

The snapshot hash detects accidental content modification but **does not
authenticate its origin**. Do not accept an untrusted, user-provided baseline
merely because its internal SHA-256 is self-consistent. Never commit real
snapshots, access tokens, account balances, or private data to GitHub.

Invoke `check_predeploy(...)` with fresh read-only evidence. `PASS` means
only that the supplied evidence meets the automated conditions; manually verify
its provenance and obtain separate deployment authorization before publishing.

After an independently authorized manual deployment:
- Confirm Render reports the approved SHA as `live`.
- Recheck the service's Auto-Deploy state, effective plugin version and OAuth.
- Call `compare_financial_audit(baseline=<complete trusted pre-deploy snapshot>)`.
- Invoke `check_postdeploy(...)` using the returned comparison and fresh Render
  evidence.
- Require `PASS` with `MATCH`, matching counts and hashes for **all** five
  sections (including historical accounts and archived transactions), no changed
  buckets or account balances, and quality status `OK`.

Any `BLOCKED`, timeout, missing evidence, version mismatch, changed financial
record, or unavailable connector is a manual **review stop**. Do not correct
financial records, redeploy, or roll back without distinct authorization.
Legitimate concurrent transactions can cause `REVIEW_REQUIRED`; do not assume
all drift is a deployment bug. Hash equality does not verify bank settlement.

The library is deliberately pure: it cannot enforce Render's manual deploy
button policy by itself. Permission and branch protections must be administered
separately and are outside the scope of this PR. Use disposable PostgreSQL test
data for integration tests; never run the destructive pytest database fixture
against production.

## CI

Run `ruff check .`, `ruff format --check .`, `mypy app migrations tests`, and
`pytest -q` using the repository's existing isolated PostgreSQL 16 test setup.
The new `tests/test_deploy_safety.py` exercises both success and fail-closed
branches without writing to production. These checks do not initiate deploys.
