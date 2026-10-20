# Holding Core PR #3 — exact-SHA exit gates

**Branch:** `feat/03-offline-orchestrator` (Draft until every blocking row is verified).
**Target:** offline-first autonomous code orchestration for `r9327/Dof-Atlas`.
**Rule:** implementation present is not a test PASS; green targeted tests are not
Windows E2E, and neither can replace authenticated GitHub certification.

| Domain | Implemented source | Focused tests written | Evidence needed before READY |
|---|---|---|---|
| JSON + Markdown task intake | `roadmap_import.py`, `markdown_roadmap.py` | `test_roadmap_control_intake.py`, `test_markdown_roadmap.py` | Real Python 3.13 tests; representative whole roadmap |
| Incremental new direction / safe replan | `roadmap_replan.py`, `control.py` | `test_roadmap_replan.py`, `test_replan_cli.py` | Live mid-flight DAG revision, restore after reboot, no lost checkpoints |
| SQLite, WAL, leases, migrations | `state_store.py`, `migrations.py`, `daemon_lease.py` | `test_state_store.py`, `test_daemon_lease.py`, `test_migrations.py` | Local tests, double-process and crash/restart Windows |
| Isolated worktree code execution | `git_worktree.py`, `git_journal.py`, `executor.py`, `code_worker.py` | `test_code_worker.py`, `test_local_agent_service.py`, `test_git_journal.py`, `test_git_worktree.py` | Actual disposable Git demo on Windows with secured child identity; Git executable path frozen at process initialization, binary trust/ACL still installer-dependent |
| No false PASS | `proofs.py`, `qa_inbox.py`, `qa_entry.py` | `test_proof_store.py`, `test_qa_inbox.py`, `test_qa_entry.py` | Independent OS account/ACL + actual GitHub evidence issuer (#7); Pause/STOP must defer proof settlement |
| Local targeted Doctor QA | `qa_scoped.py`, `qa_dispatch.py`, `qa_scoped_entry.py` | `test_qa_scoped.py`, `test_scoped_runner_process.py`, `test_qa_chain.py`, `test_qa_dispatch.py` | Run with actual `tools.doctor_certification` pinned to SHA, verify process-tree termination |
| Recover after signed failure | `qa_repair.py`, `service.py` | `test_qa_repair_eligibility.py`, `test_local_agent_repair.py` | Run corrected new-SHA path end-to-end with signed failing proof |
| Acceptance and closure | `coverage.py`, `closure.py` | `test_coverage.py`, `test_closure.py` | All imported requirements and every task mapped to an authoritative signed proof |
| UI controller API | `control.py`, `daemon_cli.py` | `test_control.py`, `test_replan_cli.py` | Authenticated API + real Windows GUI from #8 |
| Resource budget, no 750MB coding spike | `resource_guard.py`, `windows_job.py` | `test_resource_guard.py`, `test_windows_job.py` | **NOT MEASURED:** process-tree RSS/private bytes over hours including GUI and helpers. ≤400MB normal, audits separately labeled. Windows coding-adapter Job has a 256MiB committed-memory limit; not a complete process-tree budget |
| Full unattended release | pieces above, with #2/#4/#7/#8/#9 | Integration suite and Windows E2E still required | Installer, trusted LLM benchmark, independent certification and reboot smoke |

## One targeted development test session (NOT final FULL)

Run in a verified, single canonical clone, after comparing `git rev-parse HEAD`
against the PR head and `git status --short`. Do not merge or declare certified
based on this command alone.

```powershell
py -3.13 -X utf8 -m unittest discover -s tests -p "test_*.py" -v
```

The complete set includes heavy platform or environment-specific tests, so
triage platform prerequisites explicitly. No CI certification is dispatched
per intermediate commit. **Only after the complete PR and self-review**, mark
Ready for review once so GitHub validates the final exact SHA; if checks fail,
return to Draft, fix and certify the updated candidate.

## Operator-facing commands (code exists, installation pending)

```powershell
py -3.13 -m holding.daemon_cli --db "<absolute-db-path>" import --roadmap-file "<roadmap.json>" --source "user:roadmap-v3"
py -3.13 -m holding.daemon_cli --db "<absolute-db-path>" direction --roadmap-file "<new-goals.md>" --source "user:new-direction"
py -3.13 -m holding.daemon_cli --db "<absolute-db-path>" coverage
py -3.13 -m holding.daemon_cli --db "<absolute-db-path>" closure
```

An incremental direction must declare explicit task identities and acceptance
criteria; converting unstructured prose into them remains CEO/planning-agent
work (#4). No roadmap text may change Git grants or certification policy.

**Certification state on 20 October 2026:** implementation changes exist on
GitHub, but the repository test suite and Windows process-tree/RAM experiments
have not been executed in this session. All checks are pending, not PASS.

**Pause/STOP gate:** the SQLite dispatcher must not claim new scoped tests
while paused/stopped; direct test runs and signed-proof settlement must also
refuse work under the same durable controls. An in-flight scoped check cannot
persist an advisory if suspension was recorded before settlement. The coding
agent is preempted on pause, and its returned patch plan is re-authorized
before any file write. Authorized Git refuses stopped work; the service does not
launch fresh health checks while paused. These are source/test contracts pending
executable CI and Windows verification; they do not claim instant termination
of a scoped Doctor process already running.
