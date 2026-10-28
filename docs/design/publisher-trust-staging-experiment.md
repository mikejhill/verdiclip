# One-VM publisher-trust staging experiment

This specifically approved experiment is confined to one fresh standard public
GitHub Windows x64 runner. VerdiClip remains driver-free. No local computer,
product dependency, driver runtime or monitor capability is involved.

## Admission and limits

Dispatch CI once with both `driver_store_staging=true` and
`publisher_trust_staging=true`; leave every other experiment input false.
The five-minute job uses `contents: read`, no retained artifacts or caches, and
the existing pinned display-only archive from VirtualDrivers release 25.7.23.
Archive SHA-256:
`e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a`.

Repeat archive, Authenticode and catalog-membership verification with the
existing Microsoft-signed SDK tool. Reverify the actual catalog signer, its
code-signing EKU, end-entity constraints, current validity and an offline chain
without certificate downloads. Missing offline revocation evidence blocks the
experiment; it does not permit relaxed verification.

Only this public leaf may be added to `LocalMachine\TrustedPublisher`:

- Subject: SignPath Foundation.
- SHA-256: `69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb`.
- Thumbprint: `3CF8CF26D8BA266C3A483AB7D26D4A818E317D76`.

Abort if already present. Open only the existing publisher store. Establish
write ownership immediately before Add, after the final absence check; a
partial write that throws still requires independent rollback. Verify the
post-add snapshot differs from the baseline only by this leaf.

The trust temporarily covers other software signed by this certificate, not
only this catalog. It is not a root import and does not establish suitability
for runtime. No Root/CA changes, security disabling, prompts answered, device
creation, deliberate driver loading, display changes or reboot are allowed.

## Staging and independent rollback

Run one `pnputil /add-driver` without `/install`. Retain the existing visible
window guard, closed stdin, owned-child bounds, exact package export hashes,
no-binding checks and conservative refusal on ambiguous package ownership.
Normal admission ends at 180 seconds. Package cleanup stops by 240 seconds;
the certificate rollback has a separate reserve through 270 seconds, inside
the five-minute job watchdog. Synchronous store operations remain bounded by
that outer watchdog, not a claim of cancellable API calls.

Package removal requires exactly one newly published package matching all
approved INF/CAT/DLL hashes and stable inventory. Never use force, uninstall or
reboot switches. A guard failure can prevent package removal; report this as
unverified cleanup rather than treating runner disposal as proof.

Certificate removal runs in a separate finally path even if staging, package
ownership, export, removal or final inventory fails. Remove only the leaf this
operation claimed immediately before writing. Independently verify its absence
even if Remove throws, then compare the full device/trust/display snapshot with
the original baseline. Retain separate package, certificate and full-snapshot
verdicts. Removing the certificate does not undo earlier OS work.

Success establishes only bounded staging and observed rollback on that VM.
Prior failed attempts retain their original uncertainty. Runtime, multiple
monitors, mixed DPI, native capture parity and local acceptance remain open.
