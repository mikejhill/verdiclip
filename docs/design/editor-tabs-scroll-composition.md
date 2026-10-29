# Combined tabs and scrolling composition checkpoint

This record supersedes the source/readiness claims in
`editor-tabs-capture18-integration.md`. Draft PR #62 remains a preview. Merge,
release and replacing the daily installation require Mike's local verification
of a completed, qualified candidate.

## Readiness correction integration

Capture correction `9429defd9f74ccd68ff98ac44b1e867f7cb635b2` is integrated on
top of the published `cdb948c3` checkpoint. It repairs reproduced ordering races
between timeout delivery and buffered READY/results, stale deadline callbacks,
repeated request dispatch, and a delayed empty pending-connection callback.
The startup and operation budgets remain 8s and 3s. A due callback performs one
nonblocking inspection; it does not wait, pump GUI events or retry movement.
Cleanup diagnostics do not release a busy process, pipe or resource scope before
terminal confirmation. Bounded phase evidence excludes paths, payloads and
arbitrary provider text.

Document admission, canonical ReviewedCapture, passive presentation and review
acknowledgement contracts need no adaptation. Independent combined review found
no blocking integration issue. The original capture cohort at failing seed
3660914482 passes 789 tests on both Qt 6.11.2 and 6.12.0 (17 supplemental native
cases excluded). Initial restricted-process pipe setup failures and the approved
protocol-only rerun are recorded separately; these are not desktop/input tests.

The historical hosted incident's exact phase and cause remain unproven. These
regressions qualify the reproduced races; passing reruns do not retroactively
explain the old timeout. The original readiness failure, diagnostic rerun and
18.6ms repaint outlier remain retained evidence. Renderer code and budgets are
unchanged. New combined-head CI, CodeQL/scanner/thread audit and mutation evidence
are required; previous green statuses are historical. Native qualification,
production capability composition and Mike's isolated local verification remain
held. No native/UI input, merge, release or installed-app replacement occurs.

## Current capture and mutation integration

The combined source now includes capture checkpoint `024380ec` and its committed
native-harness/passive-surface migration. Capture-owned panels use the passive
presentation policy without changing canonical capture delivery, acknowledgement
or retry ownership. This integration requires no controller API adaptation.
Native presentation, input, UIA and actual assistive-technology qualification
remain separate gates; no native scrolling factory is enabled by default.

The two earlier combined mutation jobs reached their 150-minute limit and saved
partial verdicts. Neither is a completed score. Explicit module arguments caused
mutmut 3.8 to rerun cached verdicts. The reviewed recovery uses public file scope
with no CLI names; see [incremental mutation qualification](mutation-resume.md)
for the independently executed cache, source-invalidation and bad-baseline checks.
Final hosted checks must cover the resulting published head. The older numerical
checkpoints below describe their historical sources, not this new head.

## Deferred-drop correction after the first published composition

Further independent review reproduced a queued release racing with an approved
close of the dragged tab while a neighboring tab kept its host alive. The old
callback indexed a page no longer in that host and raised `ValueError`; no data
loss was demonstrated. Drop commit now checks source eligibility and current
document/view ownership before hit testing or indexing. The real queued-callback
regression fails against the prior source and passes with the guard.

Independent verification passed all 59 workspace tests, including constructor
failure before host assignment, presentation timer teardown, actual close-button
child hits and exact interior/same-slot/cross-window insertion. Private focused
replays distinguish 13 additional survivor candidates; five other selected
recovery candidates were already rejected by existing tests. These are selected
fault experiments, not a revised aggregate mutation score. Both earlier mutation
runs are now terminal with preserved partial evidence; the correction was
published and passed CI/CodeQL. The newly integrated head needs fresh validation.

## Ownership and composition

The combined branch includes capture checkpoint `024380ec` (and its ancestors
`e1a4fa3`, `107d911` and `b89ba2d`), without importing capture-worker experiments. There is
one canonical `capture.reviewed.ReviewedCapture`; document provenance retains
metadata independently of capture sessions. Delivery stays nonactivating and
acknowledges the capture ID only after admission/output policy succeeds. Failed
delivery retains the original and retry receipts; a successful file save is not
repeated when clipboard delivery alone needs retry.

`AppController.configure_scrolling` provides the explicit application ownership
seam for an idle, unparented, application-thread UIA adapter and backend factory.
It creates the workflow with controller-owned review and input-lease predicates.
The tray and editor commands enable only after attachment succeeds. Capability
updates preserve an already open tray menu and its action identity. Configuration
starts no provider and sets no persistent preference. No production native
factory is configured by default: scrolling remains unavailable until native
qualification. Unexpected Qt failures after service ownership begins retain the
graph; atomic recovery for such attachment failures is not qualified.

Exit and background updates account for selecting, confirmation, acquisition,
paused review and retained output work. Close approval is rechecked after page
detachment and after final review closure, since either callback can synchronously
admit new work. Automatic updates defer around visible application UI and never
close it as part of capture completion.

Recovery keeps independent document/view ownership, continues past a failed view,
and retains presentation leases after partial attachment. Recovery presentation
uses the same passive/deferred policy as capture admission. Duplicate document or
view identities cannot overwrite retained work.

## Reproducible evidence and limits

The primary repository tab profile includes composed scrolling and delivery
receipt journeys under its OS-side-effect guard. Minimum Qt 6.11.2 passed 277
tests, measuring 97.63% scoped lines and 91.62% scoped branches across the same
13 modules. The restricted minimum-Qt Windows aggregate passed 1,846 tests,
with one expected skip and 37 explicit native/OS-environment exclusions. Hosted
CI must run the complete portable OS suite and both supported Qt profiles for
the final published head; these local results do not replace that gate.

Real Qt widgets are composed with replay input/provider/display/pixel adapters.
Tests cover pre-existing menus/modal dialogs without Hide/Close, ordinary and
explicit scrolling, cleanup-gated delivery, paused review/resume, same-image
retry, tray-only output, close callback races and rollback recovery. They prove
software contracts, not physical pointer behavior, actual mixed-monitor DPI,
Narrator output or foreign application compatibility.

The earlier combined head `384acd8` completed its informational full mutation
run: 7,695 killed, 13 segfaults, seven timeouts and 2,687 survivors among 10,402
checked mutations (74.2% detected); 7,262 were unselected. This is not an all-clear
safety score. Focused semantic replays of 21 reconstructed critical candidates
against new tests reject 19 directly and two with assertion failures plus Qt
callback errors. Independent review identified the missing positive-save /
partial-admission scenario; its new regression rejects both source-path omission
candidates before delivery retry, when Plain Save must already know its path.
These replays do not recompute the full mutation score or triage every survivor.

Independent Codex review, final-head hosted checks, substantive review-thread and
CodeQL disposition, native qualification and local acceptance remain separately
recorded gates. No security settings, drivers, normal installation or monitor
configuration are changed by this checkpoint.
