# Reproducible tab contracts: primary CI proof

Repository-defined CI is the primary proof for tab state, transfer, topology,
hotkey routing and accessibility contracts. Laptop/native checks are supplemental
evidence, not required infrastructure. This supersedes earlier wording that made
an exclusive laptop session the primary qualification gate. No dedicated VM,
display driver, monitor changes, security changes or credentials are introduced.
Mike's final local verification before merge remains required.

## One target locally and in CI

```powershell
uv sync
uv run poe test-tabs-ci
# Reproduce an archived run using its seed and recorded dependency/runtime set:
uv run python scripts/test_tabs_ci.py --seed 125669617 --expected-qt 6.11.2 --expected-python 3.13
```

`scripts/test_tabs_ci.py` selects the test paths and measured production modules
explicitly. It forces Qt offscreen, this checkout's source imports and a fresh
temporary test directory; disables inherited pytest addopts/plugins and Hypothesis
database state; supplies one recorded seed to pytest-randomly and Hypothesis.
Failures preserve counterexamples/blob text in the log. There is no CLI option to
add native tests or weaken coverage thresholds. Expected Qt/Python mismatches fail
before pytest. Safely validated output directories receive an explicit failed
preflight receipt; old green logs/XML/JUnit/summary are invalidated even on reuse.

The `tests.tabs_ci_support` plugin fails before app construction unless the
platform is offscreen. Per-test guards refuse actual Python ctypes DLL loading,
MSS capture construction and QDesktopServices shell/URL calls; tests may replace
these with explicit local doubles. This does not claim Qt performs no OS calls:
it rules out the native input/screen/shell boundaries exercised by this target.
Guard tests themselves install safe sentinels first, so a broken guard cannot
reach a real backend. Independent patch lifetimes preserve test cleanup order.

## Defined matrix and required gate

`.github/workflows/ci.yml` runs `tabs-contracts` on the existing Windows hosted
runner pool, Python 3.13, with explicit Qt/PySide6/shiboken 6.11.2 and 6.12.0 jobs.
It installs other dependencies from uv.lock; the Qt override does not edit the
lockfile. `uv run --no-sync` deliberately preserves that selected runtime, and
the runner checks the actual version. The manifest records exact Python patch,
Qt/dependency versions, OS/platform, runner image metadata, Git SHA/dirty flag,
test paths, coverage scope, command and seed. GitHub's runner image may evolve;
the manifest identifies what actually ran rather than claiming a frozen machine.

Both matrix jobs are required by the existing `ci-passed` aggregate. Test success,
pytest-cov's existing combined floor, **scoped lines >=90% AND scoped branches
>=90%** must all pass. A unit test rejects 89.9% branches despite 99% lines; another
proves runtime mismatch fails before a run. Existing whole-application tests,
diff coverage, lint/type/build and mutation jobs remain unchanged and required.
The focused gate adds proof; it does not replace their broader scope.

| Contract | Repository evidence | Simulation boundary |
| --- | --- | --- |
| Independent content/dirty state | History/admission/jobs tests; generated content oracle interleaves edit, undo/redo, delivered states, selection/zoom, move, close Cancel and stale/current jobs. | Generated in-memory documents; no external OCR/export engine. |
| Atomic transfer and close | Workspace tests, injected before/after-removal/attachment/allocation failures, retained rollback recovery and close callback races. | Real Qt widgets in offscreen platform, injected failures. |
| Mixed display topology | Synthetic scales 100–250%, negative/above-primary origins, mixed DPI, gaps, removal/reordering and generated available-work-area containment. | Explicit screen geometry/doubles; does not certify Windows physical-coordinate reporting. |
| Input/late events | Injected CaptureApi, generated native message sequences, signed LPARAM cases, failure to acquire, reentrant cancellation/release and queued deactivation before late release. | Native protocol modeled without real SetCapture/global input. |
| Hotkeys/shortcuts | Controller tray/hotkey routes quiesce drag; foreign WM_HOTKEY cancels safely; keyboard tab/reorder/detach/close actions route within the active document. | Fake hotkey backend and local Qt key events; no OS registration/hook. |
| Accessibility/focus | Spoken identity/order/dirty/close names through reorder/detach; window-scoped shortcut discoverability; inline text undo and origin commit on tab switch. | Qt accessibility properties and focus contracts, not Narrator certification. |
| Reviewed capture delivery | Same canonical ID/image across retries, tray-only acknowledgement, repaired output settings, independent sink receipts, hidden/closed review ownership and cleanup retries. | Synthetic scrolling results, offscreen clipboard/private output folders; no live scrolling backend. |

## Measured evidence and honest scope

Each job uploads `environment.json`, `pytest.log`, `junit.xml`, `coverage.xml` and
`summary.md`, also showing the scoped line/branch rates in the Actions summary.
Named stale reports are cleared only in the dedicated checkout artifact folder;
symlink report destinations and output outside `.test-tmp/` or `artifacts/` are
refused. Failed tests, timeout, missing coverage or branch-floor failure cannot
produce a passed manifest. Logs/metadata avoid dumping the process environment.

The current local replay passes 225 contracts and measures 97.77% scoped lines,
91.88% scoped branches (96.46% combined). Its scope is the eleven production
modules enumerated in the runner, not the whole editor window/controller or app.
Full application coverage and prior native queue results are separately recorded.
The CI workflow definition is linted locally; no hosted CI run is claimed until
the branch is authorized for publication and its actual checks finish.

Physical mouse/Narrator/mixed-monitor checks can add evidence when available;
their absence does not require a new VM or prevent these CI contracts running.
A green tab target still does not complete the unfinished live scrolling backend
or prove capture preserves foreign/owned popups: those cross-feature contracts
need their own reproducible repository tests and working implementation. The
current private bundle remains disabled for that incomplete integration, not
because a particular laptop must remain unlocked. No merge/release/normal install
is authorized by this CI definition; Mike tests the final isolated build first.
