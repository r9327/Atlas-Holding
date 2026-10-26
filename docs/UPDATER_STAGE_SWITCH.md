# Update staging and activation primitives — 23/10/2026

This branch implements local security primitives, not yet an installed
Windows auto-updater or a distributable release.

## Stage

- An external trusted policy must authenticate a release manifest and pin
  the archive SHA-256. A digest copied from an untrusted manifest is NOT
  authorization.
- holding.update_stage.stage_release_archive verifies a local ZIP and
  extracts it into install_root/versions/RELEASE, never the active app.
- Refuses dangerous paths, symlinks, case collisions, encrypted/special
  entries, oversized files, unsafe Windows names and bad checksums.
- Atomic directory rename publishes a complete slot and receipt.
- Failed extraction removes temporary data but never an existing release.

## Activate

- holding.update_switch.activate_staged_release requires an independently
  approved archive SHA and a trusted health-probe callback supplied by the
  launcher, never an instruction contained in a downloaded ZIP or web page.
- Writes a pending journal, atomically changes only the active-release
  pointer, then restores the old pointer if the probe fails or throws.
  Existing slots and user data remain unchanged.
- The trusted launcher must invoke
  holding.update_switch.recover_interrupted_switch before engine startup
  after an interruption. Unknown or conflicting pointer changes fail closed.
- Single-writer execution and trusted root ACL must be enforced upstream.
  This module does not provision either of them.

## Not yet delivered

- Cryptographic release-manifest signature and channel certification gate.
- Windows helper integration, stopping/restarting the running EXE, locked
  files, OS ACL/elevation tests and native power-loss E2E.
- Installer/launcher wiring from PR #2/#9/#13.
- Delta patch application; stage currently accepts full verified ZIPs only.
- Distribution and certification of a final AtlasHoldingSetup.exe.

These are scoped Python filesystem tests on Linux/Windows, NOT an end-to-end
product release certificate. Keep PR in Draft.
