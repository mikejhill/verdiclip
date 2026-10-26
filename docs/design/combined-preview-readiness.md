# Combined preview: remaining qualification

This is an integration handoff, not permission to enable a native capability.
The normal installed VerdiClip remains unchanged. Mike must verify the completed
isolated build before merge or release.

## Portable gaps closed in this checkpoint

The three concrete candidates from mutation assessment were:

1. Cleanup callbacks in the final second of the two-second watchdog. A controlled
   half-second remainder now checks rearming, retained busy/scope ownership, the
   actual deadline receipt, and cleanup only after process exit.
2. A queued operation timeout after its reply. The idle warm helper must remain
   alive and accept the next request. Dropping the pending-request guard kills
   that helper; the new assertion rejects this fault.
3. Positive diagnostic times. Nonzero clock origins now check relative
   milliseconds, and a deliberately late operation callback checks 125 ms of
   lateness. Zero-only clocks could not detect wrong units or origin arithmetic.

These are four tests. Independent source-level replay rejected six faults:
early cleanup threshold, missing idle guard, null elapsed time, wrong elapsed
units, added rather than subtracted origin, and wrong lateness sign. This is
fresh semantic evidence, not a revised aggregate mutation score. Three further
portable cases check the exact Windows helper argument vector and independent
missing-host/missing-script refusal without executing a helper. No production
behavior changed for these tests.

The full randomized Qt 6.12 suite also exposed test-process logging leakage:
entry-point tests replaced root handlers with a stream tied to temporary pytest
stderr, then later tests logged through the closed stream. Their fixture now
detaches and restores prior handlers/level and closes only its own handlers.
This fixes test isolation without suppressing logging errors or changing the
application's production logging setup. Preserve the triggering run alongside
the corrected same-order run.

That clean-logging run exposed a separate premature observer timeout in the
cleanup-callback integration test: pytest-qt stopped at its default five seconds,
before the unchanged eight-second startup policy expired. The observer now uses
the existing protocol-test budget of startup + operation + one-second reporting
margin; production deadlines and completion/cleanup assertions are unchanged.
The delayed child startup's exact cause is unproven. This repairs the test's
budget mismatch, not evidence of a diagnosed native helper stall.

## What the 72 unqualified mappings mean

The preserved mutation run at `c332f64e` was successful: 9,091 of 12,417 detected
(73.2%), 3,326 survivors. Assessment selected 289 surviving names in four UIA
modules; 217 had count-comparable function inventories, and these 72 did not:

| Function | Unqualified names |
| --- | ---: |
| `UiaWorkerLease.submit` | 20 |
| `UiaWorkerLease._send` | 3 |
| `UiaWorkerLease._read` | 3 |
| `UiaWorkerLease._read_data` | 9 |
| `UiaWorkerLease._drain_error` | 1 |
| `UiaProcess.start` | 16 |
| `UiaProcess._default_command` | 20 |

Hosted covered-line filtering excludes mutations before numbering them. Local
unfiltered reconstruction therefore shifts suffixes in these functions. A
reconstructed diff at the same ordinal cannot identify the hosted fault. These
are neither 72 confirmed bugs nor 72 equivalents. Exact mapping would require
the matching filtered inventory; repeating a broad run for a prettier score is
not a readiness prerequisite. Retain this evidence limitation. Ownership,
at-most-once dispatch, correlation and cumulative byte limits require semantic
tests regardless of mapping. Existing tests cover those controls; the timing
and portable command tests above close identified gaps. A cached aggregate run
must not be represented as fresh verification of every newly added assertion.

## Mandatory gates and specific next actions

| Gate | Present state | Next action and evidence |
| --- | --- | --- |
| Exact-head portable qualification | Required after this test/document change | Both Qt suites, hosted CI, independent review, CodeQL findings and PR threads, and the existing mutation workflow must reach terminal results. Preserve failures and cached/fresh scope labels. |
| Permission and exclusive desktop slot | Native input is currently held | Parent must explicitly hand over a bounded qualification window. A read-only WTS query on 2026-10-26 returned active/unlocked (state 0, flags 1); the desktop is **not** the sole blocker. No UI/input was sent for that check. Recheck at execution time. |
| Combined native presentation | Existing capture harness is available but unrun for this combined head | After handoff, run `tests/capture/test_capture_native.py --require-interactive-capture` with Windows QPA on both supported Qt runtimes. It uses actual Qt/MSS presentation with replay input transport; retain that label. Assert existing menu/dialog identity, visibility, focus, handles and zero Hide/Close, including delivery and failure. |
| Production capture input | The replay suite cannot qualify actual OS hooks/raw input/external menus | Capture owner must provide and run a bounded production-broker matrix for real Qt and external native menus, owned modal dialogs, cancellation, repeated requests and confirmed cleanup. Input-target guards remain active. Do not substitute replay for this gate. |
| Scrolling runtime composition | The preview calls no `configure_scrolling`; default UIA and backend capability gates are false | Following capture qualification, add a reviewed composition that owns `UiaAdapter`/`UiaProcess` and a per-session `UiaFrameBackend`, binds the service's latched `input_active` predicate, uses private spool paths and acknowledges delivery only after cleanup. Portable integration tests must verify ownership, disabled defaults, error nonactivation, partial/retry identity and shutdown. Then qualify actual UIA discovery/consent/step/stop/partial review against test-owned content. A manifest flag alone cannot supply this missing composition. |
| Isolated preview launch | `native_ui_ready` remains false; launcher refuses before QApplication | Keep it false until the preceding capabilities are verified. Build a new isolated runtime from the qualified exact head, self-check its private namespace/output/settings/integration restrictions, then enable only the verified profile. No normal installation replacement. |
| Mike's local acceptance | Explicitly required | Provide that isolated build and known limits for Mike's test. No merge/release until acceptance. |

Native tab detach/merge failures from the early fixture were diagnosed: a queued
WindowDeactivate cancelled a drag before the posted release. Waiting for real
exposure/foreground readiness corrected the fixture without changing production
cancellation. Saved supplemental results passed four transfer/cancellation cases
and ten extended keyboard/rollback cases on Qt 6.11.2 and 6.12.0. See
[the native tab record](editor-tabs-native-qualification.md). Requalify relevant
cases on the final combined build after handoff; do not resurrect the resolved
fixture failure as an unexplained production blocker.

## Follow-up limits, not substitutes for the gates

Physical pointer behavior, Narrator announcements and physical mixed-DPI display
changes are distinct from synthetic native events and supplied topology. The
repository's deterministic topology/transfer/shortcut contracts remain primary
proof; a permanent unlocked laptop, VM or new display driver is not required
infrastructure. Actual assistive-technology usability must be observed before
claiming that certification. Survivor mapping debt and dynamic-content/fractional
scrolling compatibility remain documented follow-up areas, not evidence of
automatic safety or grounds to discard failing tests.

The original hosted helper timeout's exact cause remains unproven. The bounded
readiness correction addresses reproduced races, and passing corrected runs
provide current evidence; they do not retroactively establish that incident's
cause. Preserve the original failure, unchanged rerun, phase traces and repaint
budget outlier without relaxing deadlines or repaint budgets.
