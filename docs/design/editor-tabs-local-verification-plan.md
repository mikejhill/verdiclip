# Isolated combined verification build plan

Status: the dedicated `verdiclip.verification` composition and offscreen preflight
are implemented. A private checkpoint bundle is being prepared with native UI
readiness false. No native process, normal installed-app change, push, PR, merge
or release is performed by this plan. Parent owns combined integration,
independent Codex review and the user verification window. The scrolling owner is
delivered the accepted single canonical ReviewedCapture contract in f25091d;
tabs/controller migration is complete. Do not add another reviewed-result class for packaging.

## Source and artifact gate

Build only from the exact combined commit reviewed independently. Record its full
SHA, clean-tree status, base, Python/PySide6/Qt/mss versions, wheel and launcher
SHA-256 hashes, test evidence and artifact creation time in a manifest. If source
changes after review, rebuild and rerun the affected review/checks. A test wheel is
not a release or an instruction to replace the published normal installation.

Use a dedicated writable preview directory containing artifacts/, runtime/, state/,
output/ and logs/. Keep the normal installed environment and normal AppData paths
untouched. Install the local wheel in runtime/, never with uv tool install/upgrade,
pip --user or a global Python. Do not create Start-menu/Startup shortcuts or file
associations. Dependency installation must use approved normal package sources;
no new credentials, persistent permission grants or security changes are needed.

The local build recipe, to execute only after the source gate, is:

```powershell
$previewRoot = '<dedicated absolute preview directory>'
$previewPython = Join-Path $previewRoot 'runtime/Scripts/python.exe'
uv build --wheel --out-dir (Join-Path $previewRoot 'artifacts')
uv venv (Join-Path $previewRoot 'runtime') --python 3.13
# Install the exact local wheel and pinned runtime dependencies recorded in the manifest.
# Qualify minimum 6.11.2 and current/candidate Qt separately; do not silently alter uv.lock.
Get-FileHash -Algorithm SHA256 '<exact wheel path>'
Get-FileHash -Algorithm SHA256 '<verified launcher path>'
```

The implemented entry is `python -m verdiclip.verification --manifest <path>`. It
defaults to noninteractive self-check. `--run-ui` is refused while the bundle
manifest has native_ui_ready=false; no CLI override is supplied. A readiness true
bundle is prepared only after native acceptance and independent review.

These are deliberately source-gated build commands.
There is no placeholder download URL or invented release identity. The final
package must provide an actual checked launcher and manifest before user handoff.

## Dedicated launcher composition

Do not invoke Application.run/_run_gui unchanged: it uses normal settings and
SingleInstance naming, constructs real shell backends/updater and enables Start-menu
integration by default. Changing APPDATA alone also does not isolate the process's
single-instance name or every Windows integration path.

Use a dedicated preview composition root with existing injectable seams:

| Concern | Required behavior | Verification |
| --- | --- | --- |
| Instance | Name includes preview marker and build SHA; never the normal default | Two preview launches forward only to that preview; a normal app is unaffected. |
| Settings/styles | SettingsStore gets state/settings.json explicitly; StyleMemory remains adjacent | File tracing/fake-store tests show no normal settings/styles access. |
| Output/logs | Defaults point inside preview output/ and logs/ | An unsaved capture performs no automatic file output unless chosen; explicit user Save As remains available. |
| Shell | Startup, association and shortcut backends are unavailable in preview; settings controls cannot enable actual registrations | Backend spies fail the test if a real registration/removal is attempted, including after applying settings. |
| Updates | No updater is constructed or scheduled; check/install choices stay disabled | No network release checks or install subprocess can start. |
| Hotkeys | All four bindings start empty; no keyboard hook is created just for settings | Default launch makes zero global registrations and creates no low-level hook. Any native capture backend lease remains capture-owned and separately qualified. |
| Identity | Tray tooltip and every host visibly include Preview + short SHA | Labels survive tab changes/detach without altering document titles or output filenames. |
| Input | Agent automation uses only the parent-assigned exclusive native test window | No concurrent desktop drivers; refusal/cancellation is not bypassed. |
| Shutdown | Pending output/retry/unsaved work uses the production close policy | Cancel keeps the preview and all work; cleanup never forces a process kill. |

Prefer typed injected dependencies and a small presentation-label option over
monkeypatching global production classes/constants. Fail closed if a forbidden
preview integration is invoked; do not simulate a successful Windows registration.
Do not include real credentials, personal screenshots, settings copies or security
configuration in the package. Use ordinary fictional image fixtures for smoke tests.

## Automated preflight without desktop input

Before launching the preview natively, test the composition with fake screen,
window, shell, hotkey and update adapters. Assert exact private paths/namespace,
zero registration/update/hook calls, settings changes cannot enable integrations,
all detached hosts keep the preview label, and incoming accepted documents reach
the canonical ReviewedCapture -> controller -> workspace boundary once.

Validate the wheel's imports/version from its isolated runtime and compare against
the manifest. Import checks must not create QApplication, capture pixels, install
hooks or launch the main app. Native acceptance and user launch stay held until
parent coordinates the desktop slot. A synthetic/offscreen pass is not a replacement
for native input/focus acceptance.

## User verification after independent review

Provide the actual launcher path, short build SHA, output/state folder locations,
a brief list of remaining limitations and a simple way to return to the normal app.
No manual source-code review is requested from Mike.

User journeys should cover: create/open several documents; edit each independently;
undo/redo, selection and zoom across switches; reorder, detach and reattach; save and
copy from each host; cancel tab/window/global close; close the last tab and reopen
from the preview tray; recover from a failed save without losing work. Combined
capture verification must preserve all Qt/foreign popup/modal context, and scrolling
must enter as an ordinary accepted document with truthful completeness/stop evidence.

Physical drag, mixed-DPI/multimonitor placement, keyboard/Narrator and capture
interruption require the agreed native test window and exact candidate runtime.
Record observations against the manifest SHA. If a blocker is found, keep merge
held, fix, independently re-review as needed, rebuild and repeat affected journeys.

## Completion and cleanup

Merge only after independent Codex review and Mike's local verification of the
actual candidate. Release remains a separate coordinated action. Keep the normal
installation unchanged during verification. The user closes the preview through
its normal unsaved-work flow; confirm its capture/input lease is released. Retain
state and outputs until the user no longer needs them. Any later cleanup removes
only verified absolute preview paths and must not touch normal settings, installed
software, registry integration, unrelated windows or monitors.

Tabs publication is still held. Exact proposed action, when specifically approved:
`git push origin codex/editor-tabs`. The approved scrolling push is not reused as
approval for a different branch or as a workaround for a denied action.
