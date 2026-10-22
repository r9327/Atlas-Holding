# Experiencing real Doctor on a disposable Atlas candidate (nonrelease)

This follows [real Ollama 3/3 CI](https://github.com/r9327/Atlas-Holding/actions/runs/39793253915)
and [real read-only Doctor CI](https://github.com/r9327/Atlas-Holding/actions/runs/39803799582).
These results are independent: real Ollama did NOT write this candidate.

This opt-in Windows Actions test exercises Atlas Doctor's actual
`agent plan`, `agent check --run-tests`, and `agent validate` commands
against an actual new Git commit derived from a **pinned public Dof-Atlas SHA**.
The commit is deliberately tiny and documentary; it does not claim to fix a
product issue, prove real AI coding on Atlas, or perform a FULL certification.

## Safety controls

- Two Git repositories in an ephemeral Windows runner: original read-only
  pinned public `r9327/Dof-Atlas@54d45478c88cd733d2127789c1bef7caa599b54d`,
  and an independently cloned disposable candidate under `RUNNER_TEMP`.
- A fixed nonrelease HTML comment is appended to `README.md` only in
  the disposable clone; `project_docs` is the declared owner in
  `.ai/scopes/project_docs.yaml`. Exactly one new commit and changed file
  are required. No arbitrary model-selected paths, patch, or shell commands.
- For each Doctor step, Holding checks the command's exit code, action,
  candidate SHA, baseline, paths, clean worktree identity, report digest
  and `certified_full=False`. A local `CHECKED_LOCAL` must name at least
  one actually executed test. `REVIEW`, `BLOCKED`, and `FAIL` never
  silently become a certification; they remain explicit stage states.
- The original source tree is rechecked before and after each step, including
  HEAD and all tracked/untracked changes. Neither Holding checkout nor
  public Atlas repository may be altered or pushed to.
- A second pure-Python verifier independently rejects malformed JSON,
  duplicate keys, stale SHA, unknown keys/stages, missing or reordered stages,
  fake certification, missing tests behind `CHECKED_LOCAL`, any model
  execution claim, or any GitHub push permission.
- The final file is exclusive-created, retained as a NONRELEASE artifact for
  just 3 days. There is no GitHub write token in the job and no CI attestation
  signing key, no production model or developer workstation requirement.

## Expected interpretation

A green integration run only means each Doctor action **completed and
returned authentic, internally consistent local evidence on a disposable
candidate**. Stage statuses and number of actually executed modules must be
read separately; they might show `REVIEW` rather than a test PASS.
It proves no unattended code quality, Doctor FULL, GitHub release, automatic
merge, real Atlas modification, or production model selection.

Future work: a true code change proposed by a local model in a disposable
Atlas worktree, verified against a real scoped test and independently certified
through existing GitHub Doctor/CI grants. Never allow the code worker to
self-certify or use this nonrelease diagnostic as production authorization.
