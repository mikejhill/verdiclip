# 0.13.0 regression repair evidence

PR [#91](https://github.com/mikejhill/verdiclip/pull/91) remains a draft repair,
not a qualified replacement release. GitHub 0.13.0 was withdrawn. A green test
run does not establish that every reported regression is fixed.

## Implemented behavior

- Admitting a capture selects its new document. The persisted capture destination
  setting chooses a new tab or a new window.
- Live inline edits and editor-owned popups keep their content and identity. A
  capture uses another passive host when switching the existing tab would dismiss
  one of those controls. Failed admission retires only a newly allocated empty host.
- Repeated active-window capture can explicitly select a visible foreground popup
  while ordinary discovery continues filtering auxiliary windows.
- Raw Input release-before-hook ordering no longer starts another wait for the
  release already received. This reproduced ordering correction does not prove
  physical-pointer delivery in a different environment. Missing-report detection,
  eligibility checks and the 250 ms pointer-delivery budget remain unchanged.
- UIA discovery compiles the desktop checks without the unrelated scrolling COM
  client. Native operation isolation, eligibility checks and deadlines remain.
  This removes unnecessary startup work but is not a measured latency guarantee.

## Qualified automated checkpoint

At source `443e905aeca940ac370d6933fb40cd39acb871cc`:

- [CI](https://github.com/mikejhill/verdiclip/actions/runs/42045122407) passed.
  Windows Python 3.13 and 3.14 each passed 2,774 tests, with 10 skips and 35 native
  deselections, at 95.59% aggregate coverage. The Linux portable baseline passed
  2,711 tests, with 73 skips and 35 native deselections. Existing coverage and
  changed-line gates were not reduced.
- Both supported Qt capture-contract and simulated-tab profiles passed.
- The owned WPF scrolling fixture passed on Qt 6.12 at process scales 1, 1.5 and
  2, and Qt 6.11.2 at scale 1. It also verified two subsequent distinct selected
  window captures, preserved popup state and cleanup. The WinForms profile
  correctly refused its unsupported provider. These use explicitly injected
  fixture input, not a physical pointer or arbitrary user application.
- [GUI readiness](https://github.com/mikejhill/verdiclip/actions/runs/42045122481)
  and [five-process repeatability](https://github.com/mikejhill/verdiclip/actions/runs/42045122697)
  passed. The single native-QPA first fake captures measured 154.9961 ms on Qt
  6.11.2 and 149.7795 ms on Qt 6.12, below the unchanged 300 ms gate. These are
  synthetic captures and do not measure total cold launch or explain past failures.
- [CodeQL](https://github.com/mikejhill/verdiclip/actions/runs/42045122365) passed;
  its PR check reported no new alerts in changed code and zero annotations.
  No inline review threads were present at this checkpoint.

A separate restricted cloud run passed 2,667 tests but failed nine local-IPC cases.
A direct QLocalServer listener probe failed in the same environment. That run is
not a passing aggregate; the successful hosted profiles above supply the primary
cross-platform evidence.

## Follow-up checkpoint with a visible logical baseline

At source `066fb5e78afc12689f6eb98a615ca127d3b7f0fb`, the single guarded hosted
preparation MOVE changed logical cursor flags from 2 to 1 while preserving the
owned popup. All four WPF profiles completed scrolling and repeated captures.
The Magnification hide call returned success, but the global cursor metadata
remained logically showing. There is no documented equivalence between that flag
and Magnification's final rendered result; presentation remains unverified.
Later receipt labels explicitly distinguish logical state from rendered proof.

[CI](https://github.com/mikejhill/verdiclip/actions/runs/42048061021) was **red**:
the WinForms profile's first provider-center inspect exhausted the unchanged
3-second operation budget. Startup had completed and the request was sent at
652 ms; operation timeout was recorded at 3,652 ms and owned-child termination
at 3,662 ms. Popup preservation and cleanup were confirmed. No cursor experiment
or broker interception began in that failed case. Do not substitute the previous
WinForms pass or a later retry for this failed outcome.

Windows Python 3.13 and 3.14 each passed 2,801 portable tests at 95.59% coverage;
Linux passed 2,738 tests. Both capture-contract and simulated-tab profiles,
builds, lint, types, CodeQL and uninstrumented readiness/repeatability passed.
CodeQL again reported no new alerts in changed code. These successes do not
make the aggregate CI result green.

The [instrumented cold-path diagnosis](https://github.com/mikejhill/verdiclip/actions/runs/42048061037)
was also **red**: scheduler analysis correctly refused incomplete state attribution.
The new phase names were accepted. First Qt 6.12 dispatch measured 278.0028 ms,
including 0.0193 ms unclassified scheduler time; editor show measured 220.1705 ms
and included 196.9847 ms classified waiting, with 0.0039 ms unknown. The later
instrumented editor-show spans were approximately 16.65–17.72 ms. This localizes
one slow interval, not its cause. Preserve unknown intervals and strict analysis
refusal rather than renaming labels, discarding outcomes or treating waiting as
proof of a specific OS operation.

## Diagnostic checkpoint 1a60ee8

[CI at `1a60ee8eca0538288891a6d177e0d609280ec86c`](https://github.com/mikejhill/verdiclip/actions/runs/42051568790)
remained red. Windows Python 3.13/3.14 each passed 2,833 tests at 95.59% coverage,
and CodeQL reported no new changed-code alerts; no review threads were present.
The retained failures are distinct:

- WinForms inspect reached `inspect_root` at 1,664 ms but never reached
  `inspect_process`; the 3-second operation budget expired at 4,176 ms. Thus the
  last started operation was `AutomationElement.FromHandle`, including its runtime
  binding, not later ancestry or ScrollPattern queries. Cleanup was confirmed.
- WPF Qt 6.12 scale 1 exhausted the existing 20-second fixture preparation budget
  after `controls-created`, before `pixels-read`. Forced retirement remains a
  cleanup failure, not confirmed ordinary shutdown. Other WPF profiles passed.
- Linux passed 2,764 tests but the pure fake activation case exceeded its unchanged
  10-second PowerShell subprocess budget. Its `after-decision` marker proves the
  extracted function returned; it does not distinguish the subsequent assertions
  from engine shutdown or process completion. The aggregate is not a pass.

The cold-path scheduler diagnostic on Qt 6.11 correctly remained incomplete. Its
new fixed categories recorded unknown time from tied timestamps, ready-while-
scheduled observations and unsupported old states; none was reclassified away.
The Qt 6.12 diagnostic compiler step exceeded its existing one-minute allocation
before any scheduler recording began. No causal interpretation or successful
latency qualification follows from either failure.

Optional cursor preparation is subsequently sequenced after passive retirement
and the WinForms provider preflight, still before binding and broker interception.
This prevents the experiment's MOVE from altering that existing preflight scene.
It does not establish the earlier timeout's cause or remove its record.

## Checkpoint 308fca0

[CI at `308fca0d9cac85d36fda6267d43ed80049cd7434`](https://github.com/mikejhill/verdiclip/actions/runs/42054619943)
remained red for two distinct WPF cases:

- [Qt 6.12 / 2x](https://github.com/mikejhill/verdiclip/actions/runs/42054619943/job/127339504819)
  completed provider discovery and three samples, then `strips.append` took
  1.017245 seconds of wall time with no recorded increase in thread CPU time.
  The independent input worker correctly ended interception when the unchanged
  one-second GUI heartbeat expired. Popup preservation and cleanup were confirmed.
  This identifies a synchronous storage interval, not which disk, encoder or
  scheduling operation caused its delay.
- [Qt 6.11.2 / 1x](https://github.com/mikejhill/verdiclip/actions/runs/42054619943/job/127339505052)
  exhausted the unchanged 20-second fixture readiness budget and required forced
  retirement. Its first snapshot reached `state-built` at 15,539 ms but never
  reported `serialized` or `published`. The general JSON serializer was the next
  operation on the owned WPF dispatcher. This differs from the earlier failures
  before `pixels-read` or during bitmap preparation; it does not establish one
  common cause.

WinForms provider preflight and Linux activation passed this run, as did the other
three native profiles, portable Windows suites, build and static gates. Those
passes neither establish the earlier failures' causes nor make aggregate CI green.
Uninstrumented readiness and repeatability passed. Both
[scheduler diagnostics](https://github.com/mikejhill/verdiclip/actions/runs/42054619573)
retained unknown-state intervals and correctly refused complete analysis; no
causal or latency-regression conclusion follows. Exact-head CodeQL check
`127339985774` reported no new changed-code alerts and zero annotations, and inline
review threads were empty. Cursor presentation and physical input remain separate
open qualifications.

## Subsequent WPF fixture repair

The owned WPF fixture now formats only its fixed state, command acknowledgement
and retirement schemas with invariant numeric formatting. It no longer invokes
the general JSON serializer on the dispatcher or during retirement. Strict nonce,
boolean and finite-number validation preserves wire types and 64-bit identities.
Cancellation is checked before activation, snapshot geometry and atomic readiness
publication. The publication-success marker is emitted only after a frame was
actually published; suppressed publication can leave only private partial staging.

The focused local checks passed 77 tests with 9 Windows-dependent skips. New probes
execute the actual PowerShell functions and callback guards, verify adversarial
values and comma-decimal cultures, and parse the result through the existing peer.
Those Windows probes and the full native lifecycle still require hosted validation.
Independent review found no remaining serializer/protocol issue after correcting
the misleading publication marker. Readiness and cleanup deadlines, failure on
forced retirement, dependencies and cursor behavior remain unchanged. Removing
unnecessary serializer work does not establish a common cause for the retained
controls, bitmap and serialization stalls.

## Hosted result at 5014f8c

[CI at `5014f8c78e8dd76fb84e7a387ff03689e6c88daa`](https://github.com/mikejhill/verdiclip/actions/runs/42059056949)
remained red. Both Windows Python profiles passed 2,841 tests at 95.59% coverage,
including actual execution of the fixed serializer and cancellation probes. The
[WPF Qt 6.12 / 1x profile](https://github.com/mikejhill/verdiclip/actions/runs/42059056949/job/127355322313)
still reached the unchanged 20-second fixture readiness limit before a first
snapshot. It retired normally with a matching closed receipt and no cleanup
errors. The other four native profiles passed. This verifies the new wire-format
probes and one ordinary cancellation path, not the cause or resolution of the
retained slow-construction cases. Uninstrumented readiness and repeatability
passed; both scheduler diagnostics still refused unknown intervals.

The successful CodeQL workflow was not a security-clean result: exact-head check
`127355766740` reported one new unused-import alert in the added test, tracked as
[code-scanning alert 203](https://github.com/mikejhill/verdiclip/security/code-scanning/203).
Its review thread must remain open until the substantive correction is verified
on the final source. No alert suppression or security-setting change is justified.

## Staged scrolling storage repair

A controlled blocked-writer reproduction held storage for approximately 1.05
seconds on the old GUI-thread path, used almost no CPU and delivered no GUI
heartbeat callbacks. This proves the responsiveness risk independently of the
hosted 1.017-second interval; it does not identify that interval's underlying cause.

Scrolling now stages PNG writes, filesystem checks, renames and checksums on one
retained worker with no queue. Immutable transaction receipts carry exact private
directory ownership. Only the GUI owner may admit metadata, after generation,
state, resource and finite-deadline checks. Stop retains only committed rows;
before a committed baseline it cancels. Cancel invalidates late admission, while
review and replacement wait for storage retirement. Native helper cleanup can
release input independently of slow private storage. Failed rollback retains its
exact owners and requires explicit cleanup retry. Retired callbacks are guarded
by binding identity and capture epoch. Bounded shutdown reports unconfirmed
cleanup rather than abandoning a potentially live owner or claiming success.

The new blocked-writer, lifecycle and widget journeys verify responsive controls,
no queue-ahead, stale/expired admission rejection, both native/storage cleanup
receipt orders, stopped-prefix retention, rollback failures, interrupted thread
startup and replacement safety. This repair keeps pixel, quota, provider and
input guards and their deadlines. Native Windows and physical-pointer acceptance
remain separate requirements; no cursor-presentation fix is included.

## Frozen repair validation before hosted qualification

The final focused storage/lifecycle suite passed 216 tests plus 3 subtests.
Independent review passed 202 tests plus 3 subtests on unchanged source hashes,
with no remaining actionable finding. Scoped combined coverage was 93.32%;
changed executable lines were 480/490 (97.96%). The new worker module had 100%
line and branch coverage. Whole-repository format, Ruff and strict ty passed.

With the same controlled 1.05-second storage wait moved off the GUI thread, 210
heartbeat callbacks ran and the largest Qt timer gap was 6.22 ms, versus 1.055
seconds before. This demonstrates the repaired blocking mechanism on the
controlled offscreen fixture, not the precise I/O cause of the hosted failure.

The full restricted-cloud run passed 2,794 tests plus 3 subtests, with 120
platform/qualification skips and 92.14% aggregate coverage. Nine tests failed at
private IPC setup with QLocalServer Unknown error 1, matching the independently
reproduced environment restriction. The run is not green. The separate UX
snapshot run passed 222 tests, skipped 2 and deselected 8; its one failure was the
same second-launch private-IPC setup. Neither run weakened or filtered the gate.

The gallery rendered all 20 images. Editor, capture settings and selected-tab
images were reviewed for layout and labels for both requested theme modes. This
Linux offscreen platform rendered the requested dark mode with light native
widget colors, so it does not qualify Windows dark-mode appearance. Native
Windows CI and exact-head security/review checks remain required before merge or
release. Local artifacts are retained separately from the scoped coverage run.

## Further bounded fixture startup trim

Optional focus diagnostics no longer construct managed stack traces or run
reflection pipelines on the WPF dispatcher. Snapshot diagnostics no longer refresh
process state or query CPU. Both retain fixed bounded phase/elapsed evidence.
Owned start ticks use GetCurrentProcess with guaranteed disposal, followed by a
stop check before Window construction. This closes the observed cancellation gap
between the identity read and a potentially slow constructor. It preserves the
same WPF controls, activation, full startup clock, schemas and deadlines.
Independent review found no issue; focused local checks passed 78 tests with 10
platform-dependent skips. Windows runtime validation is still required, and no
common cause or resolution of all earlier stalls is claimed.

The unused-import cause of alert 203 is corrected without suppression by using
subprocess.Popen[bytes] as the actual runtime cast type. Its final exact-head
CodeQL result and review-thread status still require verification.

## Hosted asynchronous-storage checkpoint d8ca4e4

[CI at `d8ca4e41c608ead19f6e9e9c94a3804031fb889a`](https://github.com/mikejhill/verdiclip/actions/runs/42066469622)
remained red for two separate failures:

- [WPF Qt 6.12 / 2x](https://github.com/mikejhill/verdiclip/actions/runs/42066469622/job/127381798491)
  exhausted the unchanged 20-second fixture readiness budget at 20,009 ms before
  capture started. Window construction took approximately 7.427 seconds and
  control setup 4.686 seconds. No pixel-read marker followed; a cancellation
  check immediately after control setup can explain that omission. The provider
  retired normally with a matching closed receipt and no cleanup errors. No
  broker or storage operation ran. These are undifferentiated wall intervals,
  not proof of CPU work, disk waits or scheduler delay.
- [Python 3.13](https://github.com/mikejhill/verdiclip/actions/runs/42066469622/job/127381798731)
  passed 2,877 tests but failed annotated 4K flatten/copy at 847.7311 ms against
  the unchanged 250 ms budget. Python 3.14 passed all 2,878 tests. Both had
  95.69% aggregate coverage, 10 skips and 35 native deselections. The measured
  renderer, flattened-image and delivery paths and budget fixture are unchanged
  from main. That observation does not explain or invalidate the failure.

Linux passed 2,809 tests plus 3 subtests, with 79 skips and 35 native deselections.
Both capture-contract and simulated-tab profiles passed. Three WPF profiles and
WinForms unsupported-provider refusal passed, including two subsequent selected
captures. At Qt 6.12 / 1.5x, all 27 sample completions stayed below 5.736 ms in
this run. The old synchronous append timer recorded zero calls because deferred
staging uses another path; it does not measure worker-side storage latency. The
native observer requires updating to cover actual preparation, staging and
admission, including refusal counters, without expanding trace limits.

Build, static checks, uninstrumented readiness and five-process repeatability
passed. The Windows gallery artifact matched its published SHA-256 digest;
native light/dark capture settings and editor images were reviewed for readable
labels and layout. Cold-path diagnosis remained red: Qt 6.12 retained unknown
scheduler state, while the Qt 6.11 reader compiler exceeded its existing one-minute
preparation limit before any trace. Neither result supports a causal claim.

Alert 203 was substantively fixed and its thread auto-resolved, but the successful
CodeQL workflow again was not a clean result. Exact-head check `127382229823`
reported six new findings: two assert side effects, two cleanup lambdas lexically
inside finally, and two direct BaseException handlers. These require substantive
correction and exact-head verification; no suppression or security-setting change
is justified. The review threads remain release and merge blockers.

## Reviewed control-propagation and native-observer correction

The six findings at the asynchronous-storage checkpoint are addressed without
suppression. Side-effecting test actions occur outside assertions and teardown
predicates are defined before finally. Prepared requests own their exact directory
before staging I/O. Synchronous callers retain incomplete cleanup receipts before
staging; worker finalization publishes terminal cleanup evidence in finally.
SystemExit, KeyboardInterrupt, GeneratorExit, custom controls and BaseExceptionGroup
propagate unchanged while failed cleanup owners remain retryable. An incomplete
receipt cannot admit unwritten pixels. Cancellation transfer and terminal receipt
publication remain serialized under the same lock, and consumption still requires
actual thread retirement.

The focused repair suite passed 241 tests plus 3 subtests at 93.35% scoped combined
coverage, with 100% line/branch coverage in the worker module. Independent review
passed 333 tests plus 3 subtests on unchanged hashes, with no remaining actionable
finding. Final published CodeQL and review-thread closure remain required.

The native harness now observes actual preparation, staging and admission using
synchronized constant-space counters without additional trace events. Refusal
requires zero actual storage calls. Successful capture requires positive staging
exclusively off the GUI thread, GUI-only preparation/admission and no active call
after cleanup. Descriptor behavior, optional clock failure and a deliberately
synchronous negative case are tested. Related checks passed 245 tests; independent
review passed 118 focused tests on unchanged hashes. The existing trace cap and
all product deadlines remain unchanged.

The original single annotated 4K flatten/copy operation now records bounded phase
wall/GUI-CPU evidence, runtime, seed and worker counts. The complete 250 ms total
includes instrumentation and metadata collection; no repeat, subtraction or new
warm-up was added. Unavailable CPU clocks report null. Seven helper tests and all
three existing budget tests passed locally. This diagnostic does not explain or
replace the retained 847.7311 ms Windows failure.

On the complete frozen local candidate, 2,869 tests plus 3 subtests passed with
120 platform/qualification skips and 92.16% aggregate coverage. The same nine
private-IPC setup failures remained; this run is not green. A direct source-line
comparison to main found 542/553 changed executable lines covered (98.01%); hosted
diff-cover remains authoritative. Whole-repository format, Ruff and strict ty
passed. The final Windows/native matrix and exact-head security checks remain
required before merge or release.

## Retained failures and open release gates

### Visible cursor

The unrelated-thread `ShowCursor` experiment cannot qualify hiding a cursor owned
by another input queue. All five native profiles at this checkpoint observed
`CURSOR_SUPPRESSED` (flags 2) and a null cursor handle. One balanced owned-popup
visibility-count adjustment did not clear that state. Cleanup was confirmed, but
both experiments were **inconclusive**, not successful cursor suppression.

The [cursor experiment](cursor-presentation-probe.md) has no production integration.
Independent proof of rendered cursor presentation before hiding, absence during
capture, and restoration afterward, plus a safe production restoration design,
remain required. Experimental API returns and logical `GetCursorInfo` flags alone
do not provide that proof. Unchanged logical flags do not establish that the
Magnification presentation operation was ineffective; current probes explicitly
report `presentation_verified: false`.

### WPF startup stall

At source `9e9d7ac15046e3e1f0c99323da9339283c26e1c3`, the
[Qt 6.12 / 1.5x job](https://github.com/mikejhill/verdiclip/actions/runs/42035829809/job/127273602600)
exhausted the existing 20-second fixture preparation allocation and required
forced provider termination. The phase log has an approximately 11.45-second gap
between `controls-created` and `bitmap-created`. Later fixture cancellation checks,
phase diagnostics, redundant-activation avoidance and passing runs do not establish
its cause or erase that failure. It occurred in the owned provider's preparation,
not in a measured production capture interval. No further deadline extension is
justified by the later passes.

### First-capture latency

At source `eb5b8f48d40180647e06e3f188c80c3c6e838578`, the
[Qt 6.12 repeatability job](https://github.com/mikejhill/verdiclip/actions/runs/42033219888/job/127264627627)
recorded 300.7408 ms for its first fake fullscreen capture and failed the unchanged
300 ms assertion. Four later fresh-process measurements on that VM were
69.7616, 65.8496, 63.7879 and 64.9115 ms. Those passes do not replace the first
outcome or prove why it was slow. The older 321.7736 and 358.6566 ms failures in the
[readiness record](gui-resource-readiness.md) are also retained. Phase instrumentation
adds overhead and is diagnostic, not uninstrumented qualification.

### Physical input and final acceptance

Injected fixture input does not resolve the reported real-pointer scrolling
failure. A pointer-delivery timeout means a required matching report was absent
within its budget; it does not by itself mean the entire session received zero Raw
Input reports. Local versus remote-session device behavior remains a separate
qualification question. Preserve fail-closed eligibility and cleanup rather than
crediting unrelated earlier packets or extending the missing-input budget.

Before merge or release, inspect the final immutable source's complete CI,
review threads and security findings, and satisfy remaining critical behavior
and required final acceptance. Do not replace the installed app or publish a
replacement release on the strength of this checkpoint.
