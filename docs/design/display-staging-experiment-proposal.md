# Disposable display-package admission experiment proposal

**Status: proposal only; not authorized or executed.** VerdiClip has no driver
dependency. This candidate is optional disposable CI infrastructure. It must never
be installed on Mike's computer or included in the product.

## Recommended approval boundary

Request one manually dispatched, five-minute job on a fresh standard public
GitHub-hosted `windows-2025` x64 VM in this repository. Permit only staging the
pinned package into that VM's Driver Store and removing that exact addition.
Do not create a device, request `/install`, load its driver, change display
configuration or run native product tests. This first experiment answers whether
the existing Windows policy admits the package without changing trust. It does
not qualify a virtual display, multi-monitor coverage or parity.

Staging is itself a privileged system change and needs specific approval beyond
the completed signature preflight. Microsoft distinguishes [staging from device
installation][store]. A later runtime experiment requires its own reviewed
device-creation helper, exact INF hardware ID and explicit approval to load the
third-party UMDF DLL and allow any display arrival it causes. Those prerequisites
are not established by the current preflight receipt.

Suggested approval wording: "Permit one five-minute staging-only experiment for
the pinned VirtualDrivers package on a disposable standard GitHub Windows VM,
using Windows PnPUtil and exact-package cleanup. No device creation or driver
loading, trust changes, prompts accepted, reboot, display changes or local work."

## Identity and existing evidence

- Release: VirtualDrivers 25.7.23 display-only archive, the same URL used by the
  [completed signature preflight][receipt].
- Archive SHA256:
  `e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a`.
- Relative INF: `VirtualDisplayDriver/MttVDD.inf`, SHA256
  `550d211fe481e74dfe3f9d724ed78be48b3a9113405965d683d9373e8d672f5d`.
- Catalog SHA256:
  `08a0093fc9b2e32b287a6f8a77ca4de0a31830d29fc33d2b13a918dc859468f6`.
- DLL SHA256:
  `c9ca837f57a98fbd43bc416a7f535a95843626e7759eaf85cf0cd7ce334dbb05`.

The previous VM verified Authenticode and INF/DLL catalog membership, and rejected
kernel-policy verification. The latter is not conclusive for this user-mode IDD;
neither result establishes effective PnP admission. Repeat the hash and signature
preflight on the actual experimental VM. Do not reuse a prior VM's trust result
as current evidence or modify the signed INF/catalog/DLL. Require matching pinned
hashes, successful Authenticode and catalog-membership checks, and no verifier
tool errors or timeouts. Record the `/kp` result separately; a repeated kernel
policy rejection alone is not this user-mode staging experiment's admission gate.

## Exact proposed procedure

1. Require an explicit dispatch input, this repository, the reviewed commit,
   GitHub-hosted Windows x64, a fresh VM and an already elevated administrator
   token. Use `contents: read`, no secrets, checkout without persisted credentials,
   no caches, uploads, paid runner or new infrastructure. Missing privilege is a
   failed prerequisite; do not trigger elevation or UAC.
2. Download and inspect as data using the existing bounded, hash-pinned extraction
   and existing Microsoft-signed SDK verifier. Record runner image/version, OS,
   session, existing display inventory and relevant existing certificate-store
   identities read-only. Do not import certificates, fetch a verifier or execute
   any package utility, script or installer.
3. Parse the exact INF hardware and compatible IDs as data; the earlier metadata
   projection did not record them. Inventory hardware and compatible IDs across
   **all device classes**, including non-present devices, plus third-party
   packages using Windows PnPUtil. Abort if this package/provider or any matching
   ID is already present, or if baseline interpretation is ambiguous. This is
   broader than the separate Display inventory. It establishes no matching device
   observed at that time; asynchronous PnP activity remains a post-check risk.
4. Invoke the existing Microsoft-signed `%SystemRoot%/System32/pnputil.exe` once:
   `pnputil /add-driver <verified-absolute-MttVDD.inf>`. No wildcard, `/subdirs`,
   `/install`, `/reboot`, force, publisher installer, DevCon or downloaded binary.
   Capture exit code and bounded output; cap the owned process tree at thirty
   seconds. Preserve any ambiguous timeout as failure, including unknown partial
   staging. Never retry automatically.
5. Inspect the Driver Store and devices again. Identify a new `oemN.inf` only by
   the baseline difference plus exact exported INF/catalog/DLL hashes; localized
   command output alone must not authorize deletion. A successful exit without
   exact verified addition is an incomplete result. Any device creation, driver
   binding, display change or reboot requirement is outside this experiment:
   stop and record it, without continuing to runtime tests.
6. In an `always` cleanup phase, delete only the newly verified package using
   `pnputil /delete-driver <exact-oemN.inf>`, with another thirty-second bound.
   Do not use `/force`, `/uninstall` or `/reboot`. Verify the package is absent,
   devices/display inventory match baseline and relevant trust-store identities
   did not change. If identity is ambiguous or the package is in use, report
   cleanup failure; never broaden deletion. Let GitHub destroy the disposable VM.

Bound each inventory, export and final-check process to fifteen seconds, the
download to thirty seconds and each verifier to its existing fifteen seconds.
An overall monotonic normal-path deadline of 180 seconds stops further admission
work and enters cleanup, reserving ninety seconds for cleanup and final checks
before the five-minute workflow watchdog. Cap every operation by the lesser of
its individual bound and the remaining overall phase deadline. No retries.
Killing a command may not
cancel an OS operation already submitted; cancellation or a stuck operation can
prevent in-guest cleanup. VM destruction is containment, not a passing cleanup
receipt. No file in the Driver Store is edited or deleted directly.

## Prompts, failures and evidence

A valid root chain does not imply [Trusted Publisher admission][publisher].
An unknown-publisher, signature, UAC or restart prompt is not accepted or clicked;
no input injection is permitted. Record rejection or timeout and stop. Do not
import a certificate, enable test signing, disable Secure Boot/HVCI/Defender,
change policy or reboot to turn a rejection into success. No such workaround is
part of a later runtime proposal either.

Retain logs and a bounded job summary only: exact commit and package/file hashes,
tool identity, baseline and final package/device identifiers, original exit codes,
bounded diagnostics, trust snapshots and separate admission/cleanup verdicts.
No new artifact or cache uploads, screenshots, user data or credentials. Package
staging may legitimately register its catalog in the Driver Store; that expected
OS-managed change is distinct from adding certificates or changing trust policy.

Pass means only that this exact package staged and was removed under the existing
policy on this exact image, with no detected scope expansion. Rejection remains
useful evidence, not a reason to weaken policy. A timeout, ambiguous identity or
cleanup failure is not a pass. Runtime UMDF/IddCx/D3D compatibility, independent
displays, negative origin, mixed DPI and identical-fixture parity remain unproved.

## Later runtime option, separately gated

If staging succeeds, prepare and independently review a small repository-owned
device helper before asking for a runtime experiment. Prefer an already present,
validly Microsoft-signed [WDK DevGen][devgen] for a bounded software device only
if available and compatible with the exact INF. Do not download or redistribute
DevGen. Otherwise a reviewed source-built helper using documented Windows APIs
is a new implementation prerequisite, not permission to run an arbitrary binary.
PnPUtil `/install` updates matching devices; it does not itself create the missing
virtual device. The helper must return the exact created instance and own its
removal. Compatibility with a software rather than root-enumerated device must
be established from this package, not assumed from an IDD sample.

That future request must explicitly cover third-party DLL loading, expected
automatic monitor arrival and bounded read-only topology/rendering queries, with
exact-instance removal followed by exact-package cleanup. Configuration changes
for multiple monitors or scaling need a separate, concrete scope. A driver load
alone still cannot establish the required M profile or local/hosted parity.

[receipt]: https://github.com/mikejhill/verdiclip/actions/runs/40586066468
[store]: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-store
[publisher]: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/trusted-publishers-certificate-store
[devgen]: https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/devgen-command-syntax
