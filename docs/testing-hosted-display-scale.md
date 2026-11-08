# Hosted display-scale availability probe

`uv run python -m scripts.hosted_display_scale` is a test-only setup experiment
for one disposable GitHub-hosted Windows display. It must not run on a user's
computer. The `display-setup` job in `.github/workflows/simple-scroll-native.yml`
runs this availability probe only for the exact head of the same-repository
draft diagnostic branch.

The helper uses the supported Windows Settings Display resolution and Scale
dropdowns. If setup is needed, it enumerates the owned resolution combo's
offered choices and chooses the first uniquely enabled exact option in this
preference order: **2560 × 1440, 1920 × 1200, 1920 × 1080, 1600 × 1200**. No other
mode is allowed. If none is offered and enabled, setup fails
without selecting a resolution. It selects the chosen option at most once,
only if the display is not already at that resolution. A later failure never
causes a second mode to be tried.
`EnumDisplaySettingsW(ENUM_CURRENT_SETTINGS)` independently reads physical
pixels before selection, before confirmation, and afterward. No mode is forced.

After a resolution selection, the exact `Keep these display settings?` Text and
one enabled `Keep changes` Button must appear within the same retained Settings
subtree within five seconds. Both controls' ownership/ancestry are checked;
the button is invoked at most once and must disappear within two seconds.
The physical mode must remain exactly the chosen allowed dimensions. Missing,
disabled, ambiguous, outside-root, or differently structured confirmation fails without trying another
window or accepting a different dialog. Windows may automatically revert an
unconfirmed change; the helper only records readback and never claims rollback.
Python also requires the resolution/confirmation receipt and independently
checks physical dimensions after the Settings child exits.

Only then does the helper require one enabled, explicitly labeled Scale combo
and an existing 200% option inside that combo. Changing resolution does not
guarantee that 200% is offered. Missing, ambiguous, disabled, or differently
structured UI fails closed. No guessed coordinates, custom scale, registry
writes, private display APIs, sign-out, reboot, RDP configuration, or driver
installation are used. It does not override PowerShell execution policy.

The Settings companion runs once in an MTA child with a 60-second process
timeout, a 45-second traversal budget, 256-node/depth-8 Settings traversal, and
256-window limits for native top-level/child enumeration. It refuses any
pre-existing Settings process and snapshots all native top-level HWNDs before
launch, including windows not exposed by UIA.

Only one newly appeared visible root can be adopted. It must belong to the
uniquely new verified SystemSettings process, or to the verified Windows
ApplicationFrameHost executable with exactly one topmost Settings child from
that new process. The child's native root must be that exact new frame; a
shared host process does not authorize another window. Pre-existing HWNDs,
multiple candidates, unrelated hosted children, and process replacement fail
closed. Discovery records at most 16 numeric root/child observations.
Before adoption, each observation must validate ownership, find exactly one
enabled Scale ComboBox using the existing exact labels and bounded read-only
Settings traversal, then revalidate identity and that control's ancestry. The
same complete ownership identity and Scale control runtime ID must pass in two
consecutive observations at least 100 ms apart. A stable splash without the
Display control is not ready. Missing controls, changed identities, and startup
reparenting reset provisional readiness under the original 45-second deadline.
The uniquely new Settings process and its start time stay fixed. Only then are
the root and validated control retained; there is no post-adoption retargeting.
The helper records the last provisional identity, control observations, and
bounded rejection reason on failure.
Native enumeration retains separate before-launch, after-launch, and host-child
records, including on failure: parent HWND, limit, observed and retained counts,
overflow flag, and at most 16 sample HWNDs. On overflow, the observed count is
257 (a lower bound), not the desktop's total. The original 64-HWND limit was
increased only after a hosted run exceeded it before any selection. Ownership
requirements, UIA control matching, and the existing time budgets are unchanged.

Controls are read through `AutomationElement.FromHandle` on the retained
Settings child. Before expanding/selecting/invoking a control or closing the frame, the
helper rechecks both process start times, native PID/parent links, retained UIA
identities, and the mutation target's Settings ownership/ancestry. Cleanup
closes only that frame, never its host process. Confirmed closure requires the
exact native frame HWND to disappear within two seconds; otherwise setup fails.
Python independently requires a fresh successful cleanup receipt, accepting
Windows PowerShell's UTF-8 BOM, even if native scale reaches 200% after failure.
A child timeout may leave Settings open or the display change applied. The
probe never retries and never converts a late successful readback into a pass.

Success requires all of:

- Confirmed disappearance of the owned Settings window whenever Settings was
  opened. A failed close or an unverified close is never a successful setup.
- One monitor, with `GetScaleFactorForMonitor` returning `S_OK` and 200.
- A newly created invisible PMv2 probe window reporting 192 from
  `GetDpiForWindow` and per-monitor DPI awareness.
- A successful native reading after setup, allowing two seconds of read-only
  settling without repeating the selection. Qt scale overrides are rejected.
  Setting awareness on the measurement thread does not change scale.

`artifacts/simple-scroll/display-scale/display-scale.json` records before/after
numeric evidence and a setup-only result. The companion records its stage and
reason in `settings-outcome.json`, plus up to 16 observed Settings combo labels,
automation IDs, and option labels (each string capped at 128 characters) to
distinguish a selector mismatch from an unavailable setting. Failure attempts
bounded Settings/desktop screenshots; screenshot failure does not hide the
original failed result.
Unsupported setup always exits nonzero. No green skip counts as qualification.

## Same-session workflow experiment

The **one** `display-setup` job is restricted to the same-repository draft
diagnostic branch condition, pinned to `windows-2025`, with `contents: read`,
exact-head checkout, uv setup and locked dependency installation. After setup
succeeds, it runs Edge prose, the responsive bordered panel, and Explorer from
top, middle, and bottom through both click and Enter in that same job. The longer
job/step bounds accommodate sequential cases; product deadlines stay unchanged.
Each case requires fresh independent monitor
readback before and after capture, a captured target reporting 192 DPI, and all
existing image/restoration checks. No successful setup receipt substitutes for
these reads. Failure stops the sequence and uploads its bounded observations as
`scrolling-display-setup-200` from `artifacts/simple-scroll`.

Each hosted job receives a different VM, so another job cannot inherit this
setup. The bordered panel needs fully visible responsive geometry and a successful
native 200% result before it is qualified; clipping guards remain unchanged.
The draft bottom-start Header path now tries the ordinary strict capture path;
success remains unqualified until native evidence passes. Any failed capture
remains a failed diagnostic. All three Explorer starting positions must capture
and restore correctly before release.
A setup probe alone is not Edge, Explorer, scrolling, or hardware qualification.

Current limitations: a hosted run successfully retained the ready owned Settings
window, expanded Scale, observed only 100% and 125% on its 1024 × 768 display,
and confirmed cleanup without selecting a scale. The enabled Display resolution
combo was observed, but its offered modes and the new resolution/confirmation
path still need a reviewed native run. Neither an allowed resolution's
availability nor 200% availability afterward has been demonstrated. The Linux
tests exercise guards, measurements,
subprocess boundaries, failure handling, and cleanup with doubles. They do not
execute PowerShell, Settings, or Windows APIs. An unavailable 200% selection is
a platform/setup limitation to report, not a reason to use an undocumented API.

## References

- [Microsoft: launch Display settings](https://learn.microsoft.com/windows/apps/develop/launch/launch-settings)
- [Microsoft: change display resolution in Settings](https://support.microsoft.com/en-us/windows/hardware/display-graphics/change-your-screen-resolution-and-layout-in-windows)
- [Microsoft: read physical display modes](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-enumdisplaysettingsw)
- [Microsoft: monitor-scale result and failure semantics](https://learn.microsoft.com/windows/win32/api/shellscalingapi/nf-shellscalingapi-getscalefactorformonitor)
- [Microsoft: window DPI and awareness](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getdpiforwindow)
- [Microsoft: ComboBox UI Automation patterns](https://learn.microsoft.com/windows/win32/winauto/uiauto-supportcomboboxcontroltype)
- [GitHub: hosted runner isolation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
