# GUI resource readiness

The startup readiness architecture was adopted on 2026-10-29. Required font
catalog/default digit metrics preparation completes before controller tray/hotkey
admission. A preparation failure prevents admission and publishes no readiness
receipt. This moves work into readiness; it does not establish total cold-launch
optimization or complete preparation of every editor font, widget style or DPI
path. Architecture approval does not turn historical failed measurements into
passes or establish feature/native acceptance.

The readiness workflow, triggered by relevant PR paths and relevant main pushes,
tests
disposable public Windows VMs across Qt 6.11.2/6.12.0 and offscreen/windows QPA.
Core CI is unchanged. Each selected test runs alone in a fresh pytest process.
Imports, QApplication creation and fixture setup are **excluded**.
`outer_make_app_ms` includes the fixture factory's settings I/O, fakes,
controller construction and `start`, so it is not pure product startup. The
nested controller receipt measures the readiness sequence inside `start`: theme,
font preparation and admission. It ends before receipt publication and the ready
log. The outer-factory-minus-sequence gap includes construction, fixture work
and that remaining startup/logging work. Font preparation is nested and must not
be added again. First-capture latency covers fake fullscreen dispatch to the
owned editor's `isVisible` predicate; combined factory-to-visible wall time and
the small gap between intervals are also logged. All metrics are emitted before
the unchanged 300 ms capture assertion. No startup/combined budget or total
cold-launch improvement is claimed.

The shared timing selector now also measures a second, warm capture using the
same controller after closing its first owned editor. It runs only after the
first capture passes its assertions; a failed first capture has no warm result.
Warm timing follows environment sampling and owned-editor closure, which are
outside that interval. Its separate 300 ms assertion never substitutes for the
first-capture assertion. Both visibility predicates sample Qt state, not native
presentation. `total_cold_launch_measured` is explicitly false in the receipt.

The second fresh-process selector makes preparation raise at the controller
seam, checks that tray show and fake hotkey registration never happen, and
requires no readiness receipt. It tracks the controller before calling its real
start and explicitly shuts it down even though a failed `make_app` factory never
enters that fixture's cleanup list. This is a real hosted Qt admission check
with an injected failure, not a source/AST substitute. The failure proof runs
even after a normal pytest test-failure exit, using a process-return marker
written only after the timing child returns with pytest exit 0/1 and a valid
one-case JUnit report with no skips/errors. The timing exit code remains
failing. The separate admission proof requires exactly one passed, non-skipped
case; exit zero alone is insufficient. Both XML reports stay in `RUNNER_TEMP`
without uploads. An explicit hosted Windows environment check precedes pytest,
so a missing qualification prerequisite cannot produce a green skip. A timeout,
cancellation, collection/internal error or missing marker skips the subsequent
proof; absent proof is not a pass.

Both tests use `make_app` fake OS integrations, synthetic desktop pixels and
semantic fake hotkey dispatch: no real hotkey registration, input injection,
input hooks, drivers, trust changes or real screen capture. Ordinary successful
fixture teardown closes its owned editor and shuts down its controller. A module
`clean_clipboard` no-op overrides root clipboard cleanup for both selectors and
both hosted/local roles; the benchmark never accesses clipboard contents. Normal
CI's root clipboard policy is unchanged. Qt startup can create its
platform-managed hidden
clipboard listener. The tests deliberately use no discarded `waitExposed`
context: editor `isVisible` is the bounded predicate. Windows QPA here is not
proof of native presentation, physical input, multi-monitor parity or Mike's
final local acceptance.

Each test step is limited to 2 minutes and each job to 10 minutes. Normal pytest
teardown is the intended owned-object cleanup. On an Actions
timeout/cancellation, cleanup is **UNKNOWN**; eventual disposable VM destruction
is containment, not proof that owned QObjects were destroyed. Receipts remain in
stdout/job logs; caching and new artifact uploads are disabled. No local/native
execution is authorized by this source preparation. The process-return marker is
not proof of QObject destruction; it only permits the independent new child
after a normally returned pytest command.

Qt documents the [font catalog][families] and [text advance metric][advance].
Those API roles provide no cache-completeness or latency guarantee. All four
historical failure-admission profiles passed; future candidates still require
meaningful exact-source qualification. Earlier native Windows
readiness measurements of 321.7736 ms and 358.6566 ms failed the unchanged 300 ms
gate. An instrumented diagnostic also recorded approximately 369 ms hotkey
dispatch; its inclusive phase measurement is a different metric, not an
additional capture sample or a cause proved by timing alone. Later passes do not
erase these failures or resolve their variability.

Future comparisons follow the [shared benchmark protocol](gui-benchmark-protocol.md).
That protocol requires the same immutable checkout, harness and runtime on both
machines. Local execution additionally requires a fresh authorized handoff and a
reviewed clipboard-safe benchmark teardown. The existing hosted tests and root
clipboard cleanup policy outside this benchmark are not thereby authorized for
local use. No actual local benchmark has been performed.

The former external phase-profiler workflow is now a self-contained repeatability
lane using the shared timing selector. It runs five sequential fresh processes
per Windows QPA/Qt profile on the same VM, retains every outcome and keeps any
failed trial in its aggregate result. It does not use a separate control checkout
or profiler plugin. This distribution check is not native acceptance or a flake
fix; missing or failed qualification remains visible.

[families]: https://doc.qt.io/qt-6/qfontdatabase.html#families
[advance]: https://doc.qt.io/qt-6/qfontmetricsf.html#horizontalAdvance

## Entry failure ownership

Readiness can fail before the GUI event loop starts. Entry code therefore owns
its instance listener, native event filter, forwarding connection, signal-poll
timer and SIGINT handler explicitly, rather than depending on `aboutToQuit`.
Cleanup runs on constructor/start failure and normal event-loop return. It never
quits or deletes a borrowed QApplication. Ordinary cleanup failures retain the
original operation error as primary, with cleanup as its cause; cleanup control
exceptions remain visible. Each registered cleanup is attempted independently.

A separate hosted entry test uses a real owned local server, Qt event-filter
service and timer with fake OS integrations. It verifies retry/address reuse,
closed service, inactive timer, restored handler and retired forwarding
connection without entering `exec`. This entry test is not the shared local
benchmark selector. Its ordinary root fixture may clear the disposable VM
clipboard; do not run it on the user's desktop under benchmark-only authority.
Actual hosted results must be read before claiming those Qt cleanup checks pass.
