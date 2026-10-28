---
name: holding-evidence
description: Validate evidence and certification boundaries for Holding PRs, plugins and Windows releases.
---
Read `HOLDING_CERTIFICATION.md` and `docs/PLUGIN_GOVERNANCE.md`. Distinguish CODE_DONE, VALIDATED, CERTIFIED, BLOCKED and NOT_RUN. Run real targeted tests and `python -m tools.governance_check --json`, retain exact SHA, command and environment. No tool/model response can promote a status. Native Windows and release acceptance require separate real execution evidence.