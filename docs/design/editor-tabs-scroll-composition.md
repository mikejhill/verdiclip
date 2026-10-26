# Combined tabs and scrolling composition checkpoint

This record supersedes the source/readiness claims in
`editor-tabs-capture18-integration.md`. Draft PR #62 remains a preview. Merge,
release and replacing the daily installation require Mike's local verification
of a completed, qualified candidate.

## Ownership and composition

The combined branch includes capture checkpoint `e1a4fa3` (and its ancestors
`107d911` and `b89ba2d`), without importing capture-worker experiments. There is
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
