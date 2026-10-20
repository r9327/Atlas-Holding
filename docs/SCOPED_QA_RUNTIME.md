# Atlas Holding — scoped QA runtime (implementation checkpoint)

This is **working code**, but **not a certified unattended Windows chain**.
The code-writer and local test runner are separate roles. A locally successful
scoped run is an advisory; it cannot sign a release, waive a FULL, or mark a
mission `DONE`.

## Exact lifecycle

1. `HoldingService` checks a scoped `code` + `commit` grant, runs the
   installed coding adapter, commits on `holding/<mission>`, and persists
   `QA_PENDING` with the exact candidate SHA in SQLite.
2. An externally sandboxed, restricted **QA supervisor**, granted
   `local_test` independently of `code`, invokes:
   ```powershell
   py -3.13 -m holding.qa_scoped_entry --db "C:\ProgramData\AtlasHolding\holding.sqlite" --canonical-root "D:\Dof-Atlas" --workspace "D:\AtlasHolding\worktrees" --repository r9327/Dof-Atlas --interpreter "C:\Python313\python.exe" --mission my-task
   ```
   Paths shown are illustrative; the real installer must provide verified
   locations. No model-provided arguments or shell commands are accepted.
3. `ScopedDoctorRunner` reuses `DoctorPlanner.plan` before any test,
   refuses changed Doctor/security gates or dirty/outdated worktrees, then runs
   `tools.doctor_certification --run` with the canonical scope selector.
   The runner limits stdout to **2 MiB**, enforces a hard deadline and terminates
   descendant processes. The process must run under **restricted Windows
   identity and ACLs** established by the installer; process-tree control
   is not a security sandbox.
4. `scoped_qa_advisories` stores the actual scoped outcome and exact SHA;
   `ControlPlane.snapshot().scoped_qa_counts` and
   `ControlPlane.scoped_qa_missions()` expose these **advisory** results.
   FULL-required / plan-blocked / failed tests are not successful.
5. Only an independent **signed** exact-SHA receipt consumed by
   `holding.qa_entry` / `QaInbox` can transition `QA_PENDING` to `DONE`.
   Locally generated reports and the coding agent have **no signing API**.

## Verified by source tests (not yet run on Windows)

- `tests/test_qa_scoped.py`: safe scope/identity, blocked plans,
  no false PASS, Git mutations and standing grant.
- `tests/test_scoped_runner_process.py`: real subprocess with bounded
  output, timeouts, token-free environment.
- `tests/test_qa_scoped_entry.py`: CLI paths and advisory outcomes.
- `tests/test_qa_chain.py`: scoped local PASS **does not** unlock
  dependencies; independent signed proof does.
- `tests/test_control.py`: UI snapshot ignores a stale candidate SHA.

## Blockers — DO NOT call this production-ready yet

- Build an installer-controlled restricted Windows account/ACL profile,
  key isolation, executable pins, canonical repo data permissions and
  end-to-end tests on a real Windows machine.
- Wire a dedicated QA worker supervisor to dispatch the new CLI after each
  `QA_PENDING`, with bounded retries and resource budgets, without operator
  commands per mission.
- Implement a **separate attestation producer** that validates authoritative
  GitHub CI/Doctor evidence, checks required steps and exact SHA, and issues
  signed receipts from outside the code worker's security boundary.
- Complete autonomous local failure analysis/repair and safe new-SHA retry.
  Never reclassify a failed local test as a certified PASS.
- Run the complete targeted suite in the exact branch environment, and only
  when the scoped PR is complete run a single final CI certification. Keep PR
  Draft until then.
