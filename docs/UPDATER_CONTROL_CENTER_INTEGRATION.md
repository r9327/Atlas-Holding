# Installed update observations — integration candidate (23 October 2026)

## Implemented
This branch derives from PR #11, imports the tested staging and rollback
primitives of PR #21, and adds a read-only **Mises à jour** tab in the
live Control Center.

An explicit display-only `--update-root INSTALL_DIR` is accepted by the
viewer (real trusted installer/launcher wiring is not yet performed).
Bounded local metadata is inspected on opening and every 30 seconds:
NOT_CONFIGURED, UNVERIFIED_LOCAL_STATE, RECOVERY_REQUIRED,
NO_ACTIVE_POINTER, or LOCAL_RECEIPT_ONLY.

Observations are clearly **unsigned local metadata**: never a published
release certificate, engine health attestation or integrity proof for EXE bytes.
No updater command is exposed from Qt, and no engine IPC schema changes.

## Targeted verification
- Run test_update_stage and test_update_switch from the original PR #21.
- Test real temporary ZIP/staging/pointer receipt/corruption/recovery cases.
- Test Qt update tab offscreen and authenticated viewer command enrollment.
- Build a real nonrelease frozen Control Center EXE on native Windows.

## Still required
Trusted update-root enrollment from the installed launcher, signed release
metadata, exclusive Windows update lock, native ACL + executable verification,
coordinated shutdown/restart, real IPC health acceptance, power-loss rollback,
and end-to-end signed installer certification in PR #9.

Keep in Draft; do not merge until gates are demonstrated at the exact SHA.

## Real viewer enrollment wiring

The installed daemon now forwards the existing configuration's
database-parent installation root through
daemon_entry -> HoldingService.launch_dashboard ->
dashboard_session.start_dashboard_session -> control_center_entry --update-root.
The handoff preserves the existing one-shot private IPC credentials. This root
is only for read-only local observations and does not create an update grant.

A real verified child subprocess test checks that the path arrives without
making the readonly viewer able to pause the engine. The exact Windows
installed-root selection and ACL proof remain separate release gates.
