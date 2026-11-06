# Opt-in Qt filter delivery diagnostic

Base: e5e9522, same tree as remote 20e5a1aebe69b9763f0eb0d24c0e6c7447a3bfea.
Load only with `-p scripts.qt_filter_health`; no conftest or gate changes.
Retain original failing seed 2613928143 and the normal complete Windows 3.14
suite, coverage and assertions when using this diagnostic in CI. Do not use a
reduced Linux pass as Windows acceptance or replace the original test order.

The outer teardown hook samples after downstream teardown, including existing
fixture finalizers and pytest-qt processing. It never creates an application,
window, input event, event loop, or persistent filter. A synchronous private
QObject/registered-private-type event measures fresh app-filter, receiver-filter and receiver
callbacks separately. Each filter removal/object deletion is attempted independently with validity
checks. Fixed cleanup error labels are retained. Reporter exceptions are counted
and cannot replace the original test verdict.
No titles, text, paths from widgets, native handles, or user content are logged.
`after` is the test node ID. Output is bounded to the first unhealthy boundary
(with the preceding healthy test identity) and one final compact counter summary.
Inspect that boundary test and its fixture teardown. Keep the full
transcript, runtime versions and original failures. Probe errors are explicitly
reported without replacing test outcomes.

Interpretation: counts 1/1/1 are locally healthy. 1/0/0 can indicate another
app-level filter consumed the event. 0/1/1 points toward application-level filter
registration/dispatch while receiver dispatch survives. 0/0/0 is a broader
routing failure. Thread affinity, wrapper validity and modal presence are
reported separately. This is a new filter and a registered private event, not proof
that existing widget filters and keyboard/Show dispatch are healthy. Installing
and removing a filter can itself perturb Qt state; a diagnostic-only pass is
not evidence of repair. No runtime pointers or strong references persist across
tests. QApplication.instance monkeypatches should already be restored at this
boundary, but any probe error or no-application boundary remains evidence.

Local run: managed CPython 3.14.9, Qt 6.11.2, Linux offscreen. One reduced replay
of platform/test_hotkeys, shell/test_verification, capture/test_modality and
shell/test_hotkey_field with the original seed: 236 passed, 1 sandbox IPC failure
(QLocalServer listen, test_run_gui_composes_only_fake_sources_offscreen).
Initial diagnostic version produced 237 records: 235 healthy 1/1/1, two no-application; no probe error.
Log: /tmp/filter-health-314.log. Isolated environment /tmp/verdiclip-filter-314;
managed runtime /tmp/verdiclip-python. Native worktree and .venv untouched.
Separate tiny controls verified healthy delivery, deliberately consumed event
(1/0/0), and repeat probes after removal. Source /tmp/filter-health-controls.py.
Ruff lint/format and ty checks passed for the diagnostic module. Full gate not
run. Independent review found no blockers for the isolated diagnostic.

Reviewed revisions add scripts/diagnostic_tests/test_qt_filter_health.py: four passing
noninterference controls for independent cleanup, reporter exceptions, original
teardown exception preservation when the probe and reporter fail, and bounded
output with preceding healthy identity. Ruff lint/format and ty pass on both
modules. These tests are separate from the one reduced seeded replay; no second
replay was performed. The initial controls used the original User-type probe;
the revised registered-event implementation has not had Windows execution.

.github/workflows/qt-filter-health.yml is an isolated branch-restricted workflow:
only diagnostic/qt-filter-health branch is allowed, Windows latest Python3.14,
locked Qt6.11.2 checked, full suite with existing native_desktop exclusion,
coverage threshold unchanged, seed2613928143, plugin opt-in and ten-minute
suite timeout. Artifacts retain log, coverage and JUnit on failure. No changes
to ordinary CI. Diagnostic tests live outside configured testpaths and execute separately.
Collection-only comparison against a pristine e5e9522 worktree, identical
Python3.14.9 environment, native_desktop exclusion and seed2613928143:
2973 selected / 3008 total, 35 deselected, exact nodeid sequence equal.
SHA256 of newline-joined selected nodeids with trailing newline:
f1bc3931fad96e4a0d95e294096adbd4c8395c94dca8d18fd12dab9918991d84.
Candidate collection loaded the diagnostic plugin; base collection did not.
Evidence: /tmp/filter-health-{base,candidate}-{collection,nodeids}.txt.
Base commit e5e9522fb5c4291a481514531d5c9a3731f74912; tree
ea2da0c8fa0b2d9e0c43b7513ef3002b18324cf3. This is Linux collection evidence,
not Windows execution evidence. No additional full suite was run.
