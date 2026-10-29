# GUI resource readiness cost prototype

This source-only prototype makes required font catalog/default digit metrics
preparation explicit before controller tray/hotkey admission. It moves work into
readiness; it does not establish total cold-launch optimization or complete
preparation of every editor font, widget style or DPI path.

The standalone workflow, triggered only by PR paths for this prototype, tests
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
fixture teardown closes its owned editor and shuts down its controller. Existing
root pytest teardown **may clear the disposable VM clipboard**; these tests must
never run on Mike's desktop. Qt startup can create its platform-managed hidden
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
Those API roles provide no cache-completeness or latency guarantee. Actual
timing and admission results remain unqualified until exact-source hosted
qualification completes.

[families]: https://doc.qt.io/qt-6/qfontdatabase.html#families
[advance]: https://doc.qt.io/qt-6/qfontmetricsf.html#horizontalAdvance
