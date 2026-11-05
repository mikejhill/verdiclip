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
