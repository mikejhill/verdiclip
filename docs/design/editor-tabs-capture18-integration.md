# Combined editor tabs and capture 18e72 integration

This is the current integration record. It supersedes the older source/readiness
checkpoints in editor-tabs-combined-preview.md, editor-tabs-canonical-integration.md
and editor-tabs-validation.md. Draft publication is now approved; merge, release
and normal install changes remain held. Mike must verify the eventual qualified
candidate locally before merge.

## Publication follow-up

[Draft PR #62](https://github.com/mikejhill/verdiclip/pull/62) was published after
review of all 31 outgoing commits / 181 historical blobs and an independent scan
of all 106 changed text files. No sensitive information was found; private wheels,
logs and snapshots remain ignored/local. Independent final source review at
21381ed also passed 248 tabs contracts, with no new material findings.

The initial hosted capture/tabs matrices, build, gallery, lint/type, dependency
and workflow checks passed. Full tests on Python 3.13/3.14 exposed a collection
error: console pytest lacked the checkout root needed to import scripts. Pytest
now adds that root consistently; mutmut copies the harness script it needs.
Console-entrypoint and isolated copied-layout tests pass. The first mutation run
failed during stats collection and produced no valid score; its new run must
qualify that context rather than treating the previous failure as a score.

All 25 initial CodeQL quality findings were triaged: deferred module aliases avoid
partially initialized class imports; invalid output handling returns explicitly;
preview no-op methods retain protocol parameter names; close actions execute
outside asserts. Two imported capture **test-only** adjustments keep the idle
callback outside finally and full-image equality after cleanup. Capture production
modules and canonical contract remain unchanged. Independent review found no
material issue in this remediation and passed 100 focused tests. Hosted final-head
CodeQL verification remains required; no finding was dismissed or scanner changed.

CONTRIBUTING now blocks merge for unresolved PR threads or unaddressed CodeQL
findings at the exact final head, separately from Mike's local acceptance. Read-only
inspection found the effective main ruleset already requires review-thread
resolution; its sole required status is CI passed, with no separate CodeQL rule.
That is an enforcement gap to report, not permission to change security settings.
PR #62 initially has no inline review threads; its CodeQL review and alerts are
tracked independently. Live final-head status is authoritative in the PR checks.

## Exact source and boundary

Local branch `codex/combined-editor-capture-preview` merged capture head
`18e72dc966fe74203646fc8a0ac303285598becc` in 03b8e3d. Reviewed production source
for the portable checkpoint below ends at a57b810; its final test cleanup is
**7eed1d05ff85bf6b8738b015786297710658508b**. At that checkpoint, capture modules/tests and the
single-instance source/regression tests are byte-identical to the capture head.
There is one canonical `capture.reviewed.ReviewedCapture`, with its existing
timezone-aware timestamp, stable ID and completeness enums. No second contract or
duplicate IPC fix was introduced.

`AppController.deliver_reviewed_capture(capture, *, activate_editor=False)` keeps
activation policy outside the immutable result. Existing output choices, sink
receipts, retries, capture-ID deduplication and review acknowledgements remain.
The real `ScrollCaptureBinding` is exercised through a fixture backend into the
controller, including editor and tray-only success, session acknowledgement and
retained document pixels. This does not exercise desktop acquisition.

## Presentation without popup dismissal

Captured delivery defaults to passive admission. An existing host is not raised,
activated, restored from minimized state or switched to a newly admitted tab.
A hidden editor uses `WA_ShowWithoutActivating`, and canvas focus is suppressed
through page attachment and show callbacks. Explicit file-open actions may request
activation; a current Qt popup or modal dialog still suppresses it.

Even a nonactivating Qt top-level show can dismiss a Qt menu. Therefore a hidden
editor is retained, without showing, while a Qt popup or modal dialog is present.
A timer retries passive presentation once the user dismisses the protected UI.
Capture admission/acknowledgement does not wait for presentation. Documents remain
owned, unsaved, and part of close/update-idle policy throughout the delay.

Presentation rechecks protected UI after admission, restore, show, and raise, and
between each queued host: a synchronous callback may open a popup at any point.
Show failure retains a retryable presentation lease and a tray notification;
retry reuses the same document. Close staging and rollback retain that lease.
Late explicit user presentation/minimization wins over queued presentation.
No capture-specific hide, close or state change is performed on existing content.

Offscreen Qt tests verify these contracts, including `Hide`, `Close`, state,
focus/raise/activation calls, popup identity/actions and delivery ownership. They
do not certify Windows foreground rules or foreign Win32 menus. The capture
owner's `NativeBackendOptions.preservation_qualified` remains false by default;
no unqualified native scrolling launch was enabled.

## Independent review

A separate read-only Codex reviewer found and helped resolve three material
presentation gaps: retrying an initially failed hidden show, retaining the show
lease through close rollback, and rechecking popup protection between queued
hosts. The final a57b810 review reports no remaining material findings. The
reviewer independently ran 69 workspace/presentation tests on current Qt and the
original queued-popup regression on minimum Qt.

The final test-only 7eed1d05 change also received independent review and two
independent passing tests: disconnect an admission callback before popup teardown,
and require an update dialog plus unsaved capture to survive until explicit user
dismissal. No production condition was relaxed to make those tests pass.

## Reproducible primary proof

Both `.github/workflows/ci.yml` jobs, `capture-contracts` and `tabs-contracts`, are
retained and required by `ci-passed`, alongside the existing checks. The full test
job excludes `native_desktop`; no virtual display driver, VM or dedicated laptop
runner was introduced. Workflow actionlint/zizmor, Ruff, format, strict ty and
offline lock checks passed locally. Hosted follow-up is recorded above; the
portable numbers below retain their exact tested source identity.

Clean source 7eed1d05, Windows 11, Python 3.13.5, Qt offscreen:

| Check | Qt 6.11.2 | Qt 6.12.0 |
| --- | --- | --- |
| Tabs CI harness | 248 passed | 248 passed |
| Scoped lines / branches | 97.37% / 91.23% | 97.37% / 91.23% |
| Replay seed | 861882461 | 719314095 |
| Capture CI selection | 539 passed, 15 deselected | 539 passed, 15 deselected |
| Whole application portable run | 1638 passed, 1 skipped, 32 deselected | 1638 passed, 1 skipped, 32 deselected |
| Whole application branch-aware coverage | 94.83% | 94.81% |

Changed executable line coverage against v0.12.1 is **3947/4089 (96.53%)** on
minimum Qt. The tabs harness separately enforces at least 90% lines and 90%
branches across its eleven explicitly listed modules. It does not claim full
controller/window coverage from that focused profile.

The portable whole run excludes the native desktop marker and three real Windows
integration classes: LowLevelKeyboardHook, WinRegAssociations and ShellLinkBackend.
The one skip is the non-Windows keyboard-hook guard. The first sandbox aggregate
attempts had four loopback/private-pipe permission failures each. The approved
reruns, with distinct coverage data files, passed without source changes, native
input, actual UIA provider access or persistent permissions changes. Original
failure logs remain available; they are not counted as green evidence.

Evidence lives in `.test-tmp/capture18-tabs-{min,current}-final/` (runtime, source,
seed, coverage, JUnit, log and gate summary), `capture18-whole-*-qualified.*` and
`coverage-capture18-*-qualified.xml`. Capture-profile logs are
`capture18-contracts-{min,current}.log`. An additional replay of the previously
failing activation order (seed 3919433021) passes all 248 tests.

Earlier mutation evidence remains scoped to the runs documented in
editor-tabs-survivor-triage.md: 264/318 killed, all 54 survivors classified, 18
material gaps individually rejected after added tests; six close-race and six
delivery-integration fault injections also rejected. No new global mutation score
is claimed for this merge. The three newly found presentation faults have direct
regressions and independent review.

## Private artifact and remaining gates

The private bundle is `../editor-tabs-qualification/combined-preview-7eed1d05/`.
Its wheel SHA256 is
`cc139e510252bdbd7bfa4a9cc8a16c24472e79e4c510b1e5d4c14680f083d71d`.
It includes the UIA PowerShell helper and one canonical reviewed module. Its
installed-wheel self-check passed on Qt 6.12.0 without constructing QApplication
or creating state/output/log directories. Normal executable entry points were
removed only from this private runtime. `native_ui_ready=false`; the UI launcher
refuses before constructing an application, with no override.

Still pending: capture-owner native preservation capability and a qualified public
capture composition; successful final-head hosted checks and finding/thread review; final qualified
local candidate plus Mike's acceptance before merge. Earlier synthetic native tab
checks on both Qt versions are supplemental and from earlier source. This merge
performed no desktop input and makes no fresh physical, Narrator, clipboard or
mixed-monitor certification claim. Normal installation, settings, credentials,
security configuration and monitor layout remain untouched.
