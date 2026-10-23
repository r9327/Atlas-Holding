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
metadata, native ACL + executable verification,
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

## Exclusive update lock

Release-pointer activation and interrupted-switch recovery now share a
non-blocking operating-system lock stored at `.atlas-update.lock` in the trusted
installation root. The persistent file is never used as evidence and is not
deleted on release; the kernel releases the held lock if a process exits.
Concurrent processes fail closed before reading or mutating switch metadata.

This closes only the serialization primitive. Trusted root enrollment, Windows
ACL verification, coordinated process shutdown and native restart/rollback are
still separate gates.

## Authenticated engine health acceptance

`activate_staged_release_after_authenticated_ipc` now composes the existing
crash-recoverable switch with the real dashboard IPC client. The trusted
launcher callback receives the selected slot and must return a freshly enrolled
`DashboardIpcClient`; the updater itself performs the HMAC-authenticated,
bounded snapshot request. A process spawn, open TCP port, arbitrary callback
boolean, wrong capability or wrong engine identity cannot accept the release.

The factory must also return a trusted cleanup callback. Authentication failure
closes the candidate before the transactional pointer rollback; cleanup failure
is surfaced explicitly instead of being hidden behind a generic health error.
On success, the live session is returned to the launcher so lifecycle ownership
cannot be lost.

Any factory or IPC failure follows the existing rollback transaction and
restores the previous pointer. This is an acceptance primitive only: it does
not yet pin the launched EXE to the selected slot, validate Windows ACLs,
coordinate shutdown/restart, or provide a signed release manifest. Those
installer gates remain required before the new path can be wired to production.

## Installed engine update-health enrollment

The real daemon now accepts `--update-health-handshake` only together with its
pinned installed configuration. On native Windows it consumes exactly one
32-byte read capability from non-interactive stdin, opens the existing
read-only dashboard IPC over the real durable SQLite projection, and emits only
a bounded port/engine identity handshake. Update mode does not launch the Qt
viewer and never receives a control capability.

The listener remains owned by `HoldingService`: failed output enrollment closes
it, and normal service teardown revokes it. This supplies the engine-side half
of real post-switch acceptance. The trusted Windows parent still needs to
verify the selected slot/ACL, launch this mode with private pipes, enforce a
bounded handshake timeout, and own termination/rollback.

`launch_installed_engine_health_session` now supplies that parent-side process
boundary for an already installed, pinned runtime pair: it repeats native ACL,
binary and config verification, launches without a shell or inherited secrets,
passes a fresh random capability through stdin, parses one bounded handshake,
and returns an `UpdateEngineSession`. Invalid output, timeout, early exit or
later rollback terminates the candidate with a bounded terminate/kill path.

The remaining integration gap is deliberately narrower: bind the signed update
slot layout to `InstalledEngineSpec`, coordinate shutdown of the old daemon,
and restart the previous pinned runtime if the new candidate cannot be accepted.

## Staged-slot content binding

Staging receipts now use `atlas-staged-release/v2` and bind the published slot
to a canonical SHA-256 tree digest covering every directory, file path, file
size and file-content hash. Activation recomputes the bounded tree before it
writes switch metadata, rejecting modified, missing, added, linked or special
files. This detects post-extraction corruption; it is not a signature and does
not replace authenticated release metadata or the native Windows ACL gate.

## Installation-specific engine slot

`stage_windows_engine_release` accepts only independently authenticated archive
and binary hashes. The archive supplies the pinned runtime pair but is forbidden
from choosing `engine.json`. Instead, the updater revalidates the currently
installed config, changes only its dashboard executable binding to the new
slot, writes that derived config before publication, and covers it with the v2
tree digest. `InstalledEngineSpec` now recognizes this exact
`versions/<release>/{runtime,config}` layout while keeping the stable install
root for durable data and MCP paths. Native launch still repeats binary,
configuration and ACL verification; this materialization is not release
signature verification or an automatic process replacement.
