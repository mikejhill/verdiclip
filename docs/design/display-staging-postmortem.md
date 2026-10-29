# Stopped staging experiment and correction design

## Observed result

[Run 40621338936][run] used reviewed source `4b90d55` on one standard
`windows-2025` GitHub-hosted runner. The script stopped after 11.561 seconds;
the job completed with failure at 02:30:59 UTC on October 28, 2026.

The required preflight gates and before/immediate-before inventories completed.
The single add child started. Its polling loop observed a visible-window or
foreground fingerprint difference and raised `Visible window/foreground changed;
possible prompt.` This establishes a changed observation, not a prompt's identity,
signature-policy rejection, successful admission, or driver loading.

All seven recorded owned child processes exited. The receipt contains no accepted
stage result, exact package identity, removal operation or final snapshot.
`partial_operation_uncertainty=true`, `cleanup_verified=false` and `passed=false`
remain authoritative. That one-shot authorization was consumed; no runtime
experiment was authorized.

## Why the final inventory is absent

The same `Invoke-StagingProcess` window-baseline guard protects every child,
including the cleanup package enumeration. That enumeration also encountered a
fingerprint difference and stopped. One outer cleanup `try` surrounds enumeration,
ownership/export checks, deletion and the final snapshot. Its catch therefore
skipped all later collection, including the independent final snapshot.

The evidence does not distinguish a persistent changed window from two separate
changes. It retains neither before/after window rows nor failed-child output tails.
No retrospective code analysis can reconstruct those missing observations.

## Runner containment evidence

The jobs API identifies runner `GitHub Actions 1000010937`, group `GitHub Actions`,
label `windows-2025`, job `122595849685`. Setup, checkout cleanup and the Complete
job step succeeded. The job ran from 02:30:33 to 02:30:59 UTC. All twelve other
jobs were skipped, and the artifacts API returned zero artifacts.

GitHub documents a [new VM per standard Windows job][reference] and [automatic
decommissioning after a hosted job finishes][lifecycle]. This supports disposable
runner containment. The retained job metadata is not a per-VM destruction or
disk-erasure receipt. Orphan-process cleanup and decommissioning are not proof
of Driver Store removal, unchanged trust, unchanged devices or unchanged displays.
No self-hosted-runner API absence is used as evidence of VM deletion.

## Reviewed correction design at the initial postmortem

The pure prototype in `scripts/display_staging_diagnostics.ps1` has no executor,
native API, driver operation or workflow import. Its fake-state checks are in
`scripts/test_display_staging_diagnostics.ps1`. It prepares this future design:

1. Latch every admission/guard failure, including earlier ownership, signature or
   deadline failures. Pass that persistent history as `AdmissionEverFailed` and
   `GuardEverTripped`; omitted history defaults to failure. A later return to the original fingerprint
   must not clear it or authorize another add. An interrupted staging result must
   remain uncertain even if subsequent observations appear equal.
2. Preserve bounded window witnesses: before/after foreground handles, added and
   removed handles/PIDs, hashed class names and explicit truncation. Retain no
   titles, command lines, screenshots or unbounded inventory. A witness is not
   proof of prompt identity or absence of transient activity between polls.
3. Capture failed-child start/exit/kill/drain states and bounded stdout/stderr tails
   in the exception path, without waiting beyond the operation/cleanup deadline.
   Missing output stays missing; no marker or zero exit may override guard failure.
4. Separate independently bounded read-only postmortem collection from admission
   and deletion. A changed foreground must not suppress safe package enumeration
   or final snapshots. A failed package query must not skip the final snapshot.
   Unexpected UI from the observation worker itself still stops that worker.
5. Keep privileged removal gated by confirmed stage outcome, no latched guard
   event, exact baseline/export ownership and stable device/trust/display state.
   Missing evidence never permits force, uninstall, prompt acceptance or reboot.
   Read-only collection does not grant permission to remove anything. A plan never
   establishes cleanup success, even if it permits an attempt. Actual removal
   and final inventories require independent evidence; a latched failure forbids
   a cleanup-success claim. Report observed facts separately from that claim.
6. Recompute remaining budget before each action. Preserve the five-minute job,
   180-second normal phase, 90-second cleanup reserve and owned-process deadlines.
   Collection can fail independently; report each failure without claiming cleanup.

This initial prototype could not recover the VM's missing state or change the
historical receipt, pinned package, trust policy or native gates.

## Next authorized diagnostic attempt

On October 28 at 04:07:47 UTC Mike authorized continued GitHub Actions experiments.
The next attempt retains the same pinned package and staging-only action on one
fresh standard public Windows x64 VM. It does not authorize device creation,
deliberate driver loading, display changes, certificate-trust changes, security
weakening, prompt acceptance, reboot, paid infrastructure or action on Mike's PC.
VerdiClip remains driver-free.

The executor now imports the pure diagnostic policy. It latches global window
changes and admission failures; captures bounded redacted failed-child tails and
window witnesses; and restricts observation mode to exact package enumeration
and the repository's read-only inventory command. The global guard remains fatal
to admission and deletion. Observation workers may collect evidence after that
failure, but their own visible window stops collection. Each query is separately
bounded and a failed query does not skip the other query. No failure is retried as
a new staging action. A later return to baseline never clears failure history.

Deletion still requires an observed successful stage, no latched failure, one
baseline-relative package addition, exact exported hashes and stable inventories.
A failure can therefore leave a staged package on the disposable VM. Report that
as uncertain or retained state; VM disposal is containment, never cleanup proof.
No force/uninstall/reboot flags are permitted.

The workflow runs pure/static checks before the one add. Normal execution keeps
its 180-second limit, cleanup its 270-second total deadline, and the job its
five-minute cap. No new artifacts or cache uploads are added. Independent review
and pure/static validation must complete before publication and dispatch. Read
the terminal receipt before proposing a different action or experiment.

## Guarded diagnostic result and correlation correction

[Run 40631068564][diagnostic-run] at `8f86c07` failed closed after 7.113 seconds
of script execution. The new witness identifies a changed foreground window whose
class hash matches `#32770`. Its purpose, caption and relationship to PnPUtil
were not captured. The child printed that it was adding `MttVDD.inf`; the guard
killed it with exit -1. No prompt was accepted and no package deletion occurred.
All ten recorded children exited. Post-failure package queries succeeded and
final device/trust/display hashes match baseline. Baseline package identifiers
were missing, so package retention and cleanup remain unverified.

The next correction retains baseline package identifiers, a canonical digest and
observation timing. It records the staging child's PID and creation time and
correlates a changed dialog's process/parent PID and creation time while the child
is still present. PID alone never proves ownership. Correlation is observation,
not authorization to control the dialog or remove a package.

Only the changed foreground dialog is queried. [GetWindowTextW][caption] reads
its foreign-process caption without activating it; no control text, messages,
UIA invocation or secure-desktop access is requested. Raw captions are discarded;
only an allowlisted caption category, length and digest enter the receipt. A
caption category is not proof of the dialog's full purpose or policy outcome.

The script records offsets, read-only file identity/creation information and a
64-byte baseline anchor digest before staging, then rechecks continuity before
and after reading at most 256 KiB of appended data from each [SetupAPI text log][setup-log]. Only sections
mentioning the pinned INF contribute fixed keyword classifications, bounded hex
tokens and a digest; a hex token is not necessarily an error code. No raw log lines
are emitted. Rotation, unavailable data,
truncation and limits remain explicit. Equal/larger replacements and changed
baseline anchors are refused. A file absent before staging is reported separately
if it appears afterward. Logging levels are never changed.

Microsoft documents that the [Trusted Publishers store][publishers] affects
silent driver installation. This is context for interpreting evidence, not a
finding that the observed dialog is a publisher prompt, and not permission to
change any store. No trust import, prompt acceptance or guard bypass is allowed.
If the diagnostic evidence establishes that either is required, unattended
staging remains blocked under the current constraints.

[run]: https://github.com/mikejhill/verdiclip/actions/runs/40621338936
[reference]: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
[lifecycle]: https://docs.github.com/actions/using-github-hosted-runners/using-github-hosted-runners
[diagnostic-run]: https://github.com/mikejhill/verdiclip/actions/runs/40631068564
[caption]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowtextw
[setup-log]: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/setupapi-text-logs
[publishers]: https://learn.microsoft.com/en-us/windows-hardware/drivers/install/trusted-publishers-certificate-store
