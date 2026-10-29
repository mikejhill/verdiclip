# Comparable GUI readiness benchmarks

This protocol compares startup readiness and capture costs on a local Windows
machine and a standard disposable GitHub-hosted Windows VM. The shared benchmark
is implemented in `tests/ux/test_gui_readiness.py`; its presence does not authorize
local execution. No actual local benchmark has been performed. Do not predict
that the local machine will
be faster. Architecture adoption is separate from measured performance and
native feature acceptance.

## Match the subject and execution profile

Before either run, select one immutable application commit and the reviewed
shared benchmark's single pytest selector. Record the actual `git rev-parse HEAD`
and benchmark/configuration identity on both sides and reject any mismatch. A
GitHub PR merge SHA is comparable only when that exact commit is also checked
out locally; matching a branch name or PR head description is insufficient.

Use Python 3.13.16 and the same locked dependencies, explicit Qt/PySide/Shiboken
versions and synthetic fixture/configuration. Run Qt 6.11.2 and Qt 6.12.0 as
separate profiles. Windows QPA is the primary comparison; offscreen QPA is a
separate series and must never be pooled with Windows QPA. Verify actual runtime
Qt, PySide and QPA values rather than relying on environment labels.

Use the same selector and arguments on both machines: one selected case per
fresh pytest process, `--no-cov -p pytestqt.plugin -p pytest_cov.plugin -p _hypothesis_pytestplugin
-p no:randomly -vv --capture=tee-sys`, with unique `--basetemp` and a separate
JUnit receipt per trial. Use the same synthetic image dimensions, fake desktop
and OS integration backends, annotation defaults, output mode and readiness
configuration. Do not substitute real screen capture, input, hotkeys or hooks.
The module's no-op `clean_clipboard` overrides the root fixture in both roles.
The timing selector is
`tests/ux/test_gui_readiness.py::test_first_controller_start_and_capture_costs`.
The separate preparation-failure selector remains
`tests/ux/test_gui_readiness.py::test_preparation_failure_never_admits_tray_or_hotkeys`.

## Shared command and explicit context

Set `VERDICLIP_GUI_READINESS_QUALIFICATION=1`, `VERDICLIP_EXPECTED_QT` to the
selected Qt version, and both `VERDICLIP_EXPECTED_QPA` and `QT_QPA_PLATFORM` to
the selected QPA. Set `VERDICLIP_BENCHMARK_SOURCE_SHA` to the externally verified
checkout HEAD: exactly 40 lowercase hex characters. The pure context validator
checks its format; it does not independently inspect Git. The workflow verifies
the actual checkout, and a local coordinator must do the equivalent before run.

Hosted jobs set `VERDICLIP_BENCHMARK_ROLE=hosted` and require actual standard
GitHub-hosted Windows environment metadata. A separately authorized local run
sets `VERDICLIP_BENCHMARK_ROLE=local` and
`VERDICLIP_LOCAL_GUI_BENCHMARK=1`; local admission rejects hosted impersonation.
These flags configure the harness and grant no desktop authority.

After the required handoff and source/runtime checks, each trial uses this same
PowerShell command with a unique `$report` path supplied by the coordinator:

```powershell
$selector = 'tests/ux/test_gui_readiness.py::' +
  'test_first_controller_start_and_capture_costs'
& .venv/Scripts/python.exe -m pytest $selector `
  --no-cov -p pytestqt.plugin -p pytest_cov.plugin -p _hypothesis_pytestplugin `
  -p no:randomly -vv --capture=tee-sys "--junitxml=$report" "--basetemp=$report.tmp"
```

Before each child, remove inherited `PYTEST_*`, `PYTHONHOME`, `PYTHONSTARTUP`
and `PYTHONINSPECT` overrides; set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONNOUSERSITE=1` and `PYTHONPATH` to the exact checkout's `src` and root.
The named pytest-qt, coverage and Hypothesis plugins are the explicit shared
profile. Prior automatic-plugin-loading timing receipts remain a separate
configuration, even when application trees match. Do not pool those measurements
with this profile.

The optional `VERDICLIP_BENCHMARK_HARDWARE_JSON`, when supplied, must contain
exactly seven fields: `os_version`, `cpu_models`, `logical_processors`,
`memory_bytes`, `runner_image`, `runner_image_version`, `runner_arch`. Counts are
positive integers; CPU models are a bounded string list. Runner image/version
and architecture may be null when unavailable locally. Supply only anonymous
model/build information, never host/user/serial identifiers or paths. Omission
is recorded as unavailable metadata, not proof of hardware parity.

## Account for costs without double counting

- **Startup admission:** theme, font preparation and admission inside controller
  `start`, ending before readiness receipt publication and ready logging. Font
  preparation is nested; report it separately without adding it again.
- **Factory envelope:** wall time around the shared factory call, including its
  settings/fake construction, controller construction, start and remaining
  logging. It is broader than startup admission and is not pure product startup.
- **First capture after readiness:** semantic fake fullscreen dispatch to the
  owned editor's bounded `isVisible` predicate. Preserve the 300 ms assertion.
  This predicate does not prove native presentation or physical input delivery.
- **Combined envelope:** factory start through the first visible editor, including
  any inter-interval gap. Report the gap and nested intervals, not their sum as
  extra work. QApplication creation, imports and fixture setup are excluded.
- **Warm capture:** the implemented second capture uses the same controller
  after environment sampling and closure of its first owned editor. Those steps
  precede the warm clock. This result and its separate 300 ms assertion occur
  only if first-capture assertions pass. Missing warm data after a first-capture
  failure is not a pass. Never combine first and warm distributions or replace a
  failed first sample with a warm result. Both predicates measure Qt visibility,
  not native presentation.

Fresh processes do not mean a cold OS, empty system font caches or an end-to-end
cold process-launch measurement. Interpreter/import/QApplication startup lies
outside these clocks. No total cold-launch improvement follows from this test.
The structured receipt explicitly records `total_cold_launch_measured=false`.

## Record all outcomes and comparable metadata

Run the same five sequential fresh-process trials on the same machine/VM for
each profile. Label the scope `5-fresh-processes-same-VM-not-cold-OS` and retain
every trial's raw metrics, outcome and ordinal, including failures and aborted
trials. Do not discard outliers, rerun until green or select the best sample.
Five samples describe this bounded series; they do not establish a population
tail-latency guarantee. The local owner stops on its first failed or uncertain
trial for safe control return; record remaining trials and profiles as unattempted.
That truncated failed series is not a complete five-trial distribution, even
though the per-trial measurement profile matches the hosted series. Hosted
assertion failures may continue with a sticky failed aggregate; neither route
replaces failed observations with later passes. Report hosted and local series separately with their
environment differences.

Compare matching screen geometry, DPR/scaling and synthetic image dimensions
where available; otherwise label those series as different rendering workloads.
Do not change monitor layouts or scaling merely to obtain a matching sample.
Different font inventories, VM scheduling, CPU models and OS builds remain
confounders, so a timing difference alone cannot identify its cause. This
single-monitor benchmark does not qualify mixed-DPI, negative-origin or
multi-monitor parity.

Each trial needs exactly one non-skipped JUnit case, no errors and a status
consistent with pytest exit 0 or 1. Any assertion failure keeps the aggregate
failed even if later trials pass. Unsupported exit codes, missing/malformed
JUnit, skips and errors abort the series and remain visible. The hosted series
keeps the two-minute step and ten-minute job limits. On timeout/cancellation,
cleanup is **UNKNOWN**; VM disposal provides containment, not QObject destruction
proof. Local interruption requires its separately reviewed owned-process cleanup
and explicit control return; do not start another trial after uncertain cleanup.

Record only comparison-relevant metadata: OS edition/build, CPU model/logical
count, RAM, Python/Qt/PySide/Shiboken versions, actual QPA, source and benchmark
configuration identities, font family count, and screen count/geometry/DPR.
Record synthetic fixture dimensions and the exclusion/clock definitions above.
Exclude personal paths, usernames, hostnames, machine/screen serial identifiers,
screen contents and clipboard contents. Diagnostic failures must not accidentally
serialize those values into a receipt.

Retain the earlier failed 321.7736 ms and 358.6566 ms first-capture measurements
and the approximately 369 ms instrumented hotkey-dispatch measurement with their
original source/profile and metric labels. The dispatch phase is inclusive and
is not interchangeable with the first-capture metric. A later green series is
non-reproduction, not retrospective resolution or proof of a specific cause.

## Local admission and cleanup

A future local run requires a fresh explicit handoff covering duration, owned
windows/processes, abort/control-return and cleanup. Use the reviewed shared
benchmark's clipboard-safe overridden teardown: never clear, read, replace or
restore clipboard contents. Normal CI's existing clipboard policy stays
unchanged. Qt may create its platform-managed hidden clipboard listener; disclose
that separately from an application clipboard content operation.

Use only the owned synthetic fixture and fake integrations. Do not register real
hotkeys/hooks, inject input, change settings or invoke driver/trust/security
workarounds. Close owned editors/controllers through the reviewed teardown and
verify the declared cleanup result. This protocol authorizes no local inventory,
Qt execution, desktop operation or native feature qualification by itself.
