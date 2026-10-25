# Editor tabs qualification checkpoint — 25 October 2026

Latest combined migration/build evidence is in
[the canonical integration record](editor-tabs-canonical-integration.md). It
supersedes the historical reviewed-type mismatch below. The private artifact is
source 8616e395, self-check passed, native launch false.

This is local implementation evidence, not release approval. The capture worker
owns desktop input. No native tests were resumed for this checkpoint.

## Source and integration

Branch `codex/editor-tabs`, based on v0.12.1 (`a1c6bbe`). The earlier core
checkpoint is `cb586888`. No capture modules are edited here.

The implemented entry point is `AppController.deliver_reviewed_capture`, accepting
`document.admission.ReviewedCapture`. Output policy, successful-sink receipts,
retry retention, grouped admission, host wiring, close planning and update-idle
checks are integrated locally. The safe default is `activate=False`. Foreground
admission must be explicitly selected by the capture owner after preserving its
original native context. `Workspace.cancel_drag()` is the central capture quiesce
seam; it releases input and invalidates queued drops without hiding/closing widgets.

The scrolling foundation commit `8adc3574368cc8cfbd0cf60f10902b461823f721`
introduced a separate `capture.reviewed.ReviewedCapture`. These types are **not yet
interchangeable**. Reconcile them in the combined integration branch before using
scrolling delivery:

| Field | Tabs value | Scrolling value | Required reconciliation |
| --- | --- | --- | --- |
| Timestamp | `timestamp` | `taken_at` (timezone required) | One canonical name or explicit adapter. |
| Completeness | `complete` / `partial` | `SELECTED_RANGE` / `REPORTED_END` / `PARTIAL` | Preserve range/end evidence; do not flatten it into a whole-document guarantee. |
| Source | String (ordinary mode or scrolling source) | `CaptureKind` | Preserve the canonical enum value in provenance. |
| Identity | Stable `identifier` | No identifier | Mint once at producer admission, retain through retry, and reuse on duplicate callback. |

Prefer one canonical reviewed value with stable identity over long-lived duplicate
classes. Do not import experimental input code into the document/window layer.
No scrolling worker files or shared UX-contract rows were changed here.

## Deterministic and integration checks

Windows Python 3.13.5. All runs explicitly set `QT_QPA_PLATFORM=offscreen`.

| Runtime | Selected suite | Coverage | Qualification limits |
| --- | --- | --- | --- |
| Qt/PySide6 6.11.2 | 1,242 passed, 1 skipped, 14 deselected | 94.82% | Fresh random seed 630036252; formerly failing order also passes after the reproduced lifetime fix. |
| Qt/PySide6 6.12.0 | 1,242 passed, 1 skipped, 14 deselected | 94.80% | Fresh random seed 3705043836; offscreen only. |

Formatting, Ruff and strict ty checks pass. Changed executable lines against
v0.12.1: **1,161 / 1,199 = 96.83%**, measured from the current-runtime coverage XML
and added-line hunks. Overall coverage includes branches; this diff figure is
line coverage, matching the distinction used by the repository gates.

The 14 explicit deselections exclude actual global keyboard hooks, desktop pixel
capture, registry association and shell shortcut integration. Native capture and
native tab test files are also ignored. These checks do not certify the final
combined Windows workflow. The remaining skip checks a non-Windows-only guard.

Focused failure/model/controller tests pass **245** cases. Tests exercise failed
save/commit preserving the old file, no destructive write fallback, retry after
sink failure, no duplicate admission after partial failure, stale output versus
new edits, exactly-once jobs/cancel acknowledgement, deferred asynchronous edits
during close prompts, Save As pinning, global Cancel, new admissions during exit,
source/destination mutation failures, failed rollback plus retained recovery,
queued-drop cancellation and keyboard menu routing. Hypothesis-generated move
sequences check that every session keeps exactly one registered host.

Warm tab activation p95: **2.28 ms**, ten 1920×1080 documents and 100 switches,
measured separately on this machine. One earlier whole-suite 4K copy timing
sample was 287 ms against a 250 ms budget; focused and later full runs passed.
Do not present either timing as a hardware-independent promise.

## Historical teardown crash — isolated and fixed locally

The failure was isolated to the existing second-instance image-forwarding journey.
It reproduced alone on both Qt 6.11.2 and 6.12.0, and on the untouched v0.12.1
source export. Therefore the earlier minimum-only characterization was incomplete.

A standalone QCoreApplication/SingleInstance/QProcess probe reproduces the access
violation without an editor, tabs, pytest, capture, hooks or desktop widgets:
receive one message; read the accepted socket and schedule deleteLater; close and
destroy the local server owner; then process events. The received socket originally
remained parented to the server, which could destroy it before its scheduled
cleanup. Draining DeferredDelete before owner destruction prevents the failure,
but pumping a nested event loop is not an acceptable production fix.

`SingleInstance._read` now reparents the disconnected, fully-read socket to the
application before scheduling deletion. The client survives transient server
teardown until normal deferred cleanup. This changes neither received data nor
single-instance naming. Closing the client before reading was tested and rejected:
it lost buffered message data. No capture module is changed.

The isolated original journey now passes on both Qt versions. A committed child
process regression uses QCoreApplication only, performs five message/owner teardown
cycles, and processes events after each owner is gone; it checks delivered data and
normal process exit. The formerly failing full order (seed 2332510707) now passes
1,241 tests on minimum Qt; another random full order passes the same count on
current Qt. One additional pure-history test and stronger format assertions were
then added. Final randomized reruns pass **1,242** tests on each runtime: minimum
seed 630036252 and current seed 3705043836, with **94.82% / 94.80%** coverage.
The isolated child regression also passes five consecutive teardown cycles.

Local evidence: `instance-baseline-min.log`, `isolate-journey-alone.log`,
`instance-current.log`, `instance-probe-plain.log`, `instance-probe-reparent.log`,
`hardened-min.log` and `hardened-current.log` under `.test-tmp`. This is a reproduced
ownership/lifetime fix, not a passing-seed workaround.

## Targeted mutation evidence

Linux/WSL Python 3.13.16, Qt/PySide6 6.11.2, mutmut 3.8.0, forkserver isolation,
four children, offscreen. Disposable Linux-native snapshot; raw logs stream into
the Windows workspace. An earlier WSL reset lost a temporary rerun, so the final
run persisted its results directly in `.test-tmp/mutation-final-results.txt`.

Scope: History, DocumentJobs, TabTransfer, and `ImageDelivery.save`.
Test selection: history/jobs unit tests, workspace integration/failure tests,
all output tests, and reviewed-delivery controller tests.

| Module / selected functions | Killed | Survived |
| --- | ---: | ---: |
| document.history | 58 | 11 |
| editor.jobs | 80 | 14 |
| editor.transfer | 96 | 17 |
| output.delivery.save | 30 | 12 |
| **Total checked** | **264** | **54** |

**83.0% (264/318) detected**. Another 113 generated mutations in unselected output
functions were not checked and are excluded from that denominator. No timeout,
segfault or type-check detection is counted as a kill here. This is not mutation
qualification of the whole application, controller or native drag adapter.

See [the complete survivor triage](editor-tabs-survivor-triage.md): all 54 are
classified into 18 material behavior gaps, 14 representation/redundant/dormant
cases and 22 diagnostics. All 18 material gaps now have tests that reject the individual
mutations in an isolated source copy. These rechecks do not change the original
recorded score.

Survivor review of the earlier run drove real checks for unsubscribe, typed
state identity, delivered-result receipts, source/target active-tab restoration,
provisional-host cleanup and disabled direct-write fallback. Remaining survivors
include changed diagnostics, equivalent private counter/boolean representations,
redundant guarded state checks, and geometry/default-construction cases. Their
presence is recorded without declaring every survivor equivalent. The repository's
weekly whole-package mutation floor is 72%; this targeted score does not replace
that whole-package gate.

## Native and combined gates still open

Earlier checkpoint: actual Win32 capture ownership plus queued native messages
passed merge/detach/Escape/hotkey cases 4/4 on both Qt baselines. Native light/dark
gallery widgets were rendered and reviewed. Those checks precede the final
controller/jobs work and cannot be reused as final native qualification. Windows
also emitted a handled `0x8001010d` COM activation diagnostic.

Before PR/release readiness: reconcile the reviewed value; centrally quiesce tab
input; prove capture closes/hides nothing with Qt and foreign native menus/modal
dialogs; exercise physical drag-out/in/reorder, last-tab moves, release versus
cancellation, interrupted moves and repeated cancellation on both runtimes; test
mixed-DPI/multimonitor geometry, Narrator and keyboard focus; retain the socket teardown regression and investigate the
COM diagnostic; integrate the UX-contract
rows and combined acceptance suite. Capture input is owned by the scrolling worker.
No desktop input was sent here after the pause/resumption coordination.

No PR, push, merge, release, installed-app change, persistent permission/security
change or monitor-setting change is part of this checkpoint. A draft push awaits
the parent's specific user approval; none was attempted from this task.


## Verification-build handoff

The user will verify the combined feature build locally before merge. Parent owns
independent Codex PR review. Do not replace the published normal installation.
The combined build is pending the scrolling-owned canonical reviewed contract and
combined capture acceptance. A normal `python -m verdiclip` launch is not an isolated
verification launcher: it uses the normal single-instance name/settings path and
Start-menu integration defaults to enabled. The verification package must use an
isolated environment, explicit private settings/output/log paths, a distinct
single-instance namespace, disabled startup/association/shortcut/update integration,
and a clearly identifiable tray/window label. Coordinate hotkeys with the normal
running app and do not capture desktop input outside the parent-assigned window.
Do not launch that build before review and user verification coordination.

Tabs push is held pending its specific approval. Exact proposed action:
`git push origin codex/editor-tabs`. It has not been attempted. Merge and release
remain held until independent review and the user's local verification.
