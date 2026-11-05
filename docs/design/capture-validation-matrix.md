# Reproducible capture validation

Repository-defined CI is the primary correctness and coverage baseline. A laptop
with particular monitors, scale settings, keyboard or mouse is not test
infrastructure. Supplemental Windows desktop checks establish compatibility for
the tested environment; synthetic results do not establish physical-device or
foreign-menu behavior.

## Primary profiles

| Profile | Environment | Evidence |
| --- | --- | --- |
| Full portable baseline | Windows runner, Python 3.13 and 3.14, locked dependencies, Qt offscreen | Unit, widget, platform-boundary and UX contracts; aggregate coverage and unchanged 95% changed-line gate |
| Capture compatibility | Python 3.13, explicit PySide6/shiboken6 6.11.2 and 6.12.0, Qt offscreen | Capture contracts, synthetic geometry/input, fictional frame/provider traces, hotkey dispatch and field behavior; JUnit per profile |
| Real PowerShell IPC | Installed Windows PowerShell 5.1, MTA, private current-user-only ephemeral pipe | Repeated variable-length requests, correlation, deadlines and cleanup, without a window or UIA provider |
| Pure helper policies | Extracted production helper functions in PowerShell | Exact numeric geometry, fractional/negative bounds, position/extent ownership and nonfinite rejection; no native method calls |

The compatibility override changes only each fresh CI job's virtual environment.
It does not change the lockfile or a user's Python environment. Action revisions
remain pinned; workflow permissions and security checks remain intact.

## Hardware-independent command

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
uv sync --locked
uv run pytest -m 'not native_desktop' --cov --cov-report=xml --junitxml=junit.xml
uvx diff-cover@10.6.0 coverage.xml --compare-branch origin/main --fail-under 95
```

`native_desktop` explicitly identifies tests requiring an interactive Windows
desktop. The portable command deselects them rather than relying on Qt offscreen
to disable unrelated desktop APIs such as MSS. Ordinary fake-MSS lifecycle and
pixel tests remain included. Local sandbox denial of disposable registry keys,
localhost or ephemeral IPC is a permission constraint, not a display dependency;
run with those test resources permitted.

Synthetic fixtures cover negative origins and 100/125/150/200% scale mappings,
tracked absolute and relative movement, matched input ownership, cancellation and
stale receipts. The recorded owned-menu injected drag is replayed in
`test_input_routing.py`; its frozen button packets cannot replace movement
endpoints. `FixtureBackend`, `NativeFixture`, `Payload` and `Documents` provide
deterministic callback ordering, semantic/UIA snapshots and uniquely patterned
frames without inspecting user content. Error modes include provider failure,
position/extent drift, malformed pixels, worker startup failure and cleanup IO
failure. These fixtures exercise real registration/storage/decoder/binding code.

## Supplemental compatibility

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run pytest -m native_desktop --require-interactive-capture
```

This explicit command must report a locked/disconnected desktop as unavailable.
It must not bypass secure desktop, UAC or input-target guards. Existing native
capture assertions still describe the released capture path; they are not proof
that the unfinished no-dismissal broker meets the new acceptance criterion.

The bounded owned WPF experiment reached discovery, samples, a provider step,
best-effort restoration and review. It retained only a verified prefix with
`overlap_uncertain` when fractional scrolling resampled the fixture pixels. The
fixture's final scroll offset was zero and its helper/processes were gone. This
result does not qualify complete WPF capture or a physical input device.

## Current feature gates

The production no-dismissal input broker/service integration, convenient
scrollbar selection entry point, broader registration compatibility and combined
preview remain unfinished. Native acquisition remains unavailable by default.
Mike's local acceptance is required before merge or release. The installed daily
0.12.1 app remains unchanged.
