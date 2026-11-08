# Hosted display-scale availability probe

`uv run python -m scripts.hosted_display_scale` is a test-only setup experiment
for one disposable GitHub-hosted Windows display. It must not run on a user's
computer. The `display-setup` job in `.github/workflows/simple-scroll-native.yml`
runs this availability probe only for the exact head of the same-repository
draft diagnostic branch.

The helper uses the supported Windows Settings Display > Scale dropdown. It
requires exactly one enabled, explicitly labeled Scale combo and an existing
200% option inside that combo. Missing, ambiguous, disabled, or differently
structured UI fails closed. No guessed coordinates, custom scale, registry
writes, private display APIs, sign-out, reboot, RDP configuration, or driver
installation are used. It does not override PowerShell execution policy.

The Settings companion runs once in an MTA child with a 60-second process
timeout, a 45-second traversal budget, 256-node/depth-8 Settings traversal, and
64-window limits for native top-level/child enumeration. It refuses any
pre-existing Settings process and snapshots all native top-level HWNDs before
launch, including windows not exposed by UIA.

Only one newly appeared visible root can be adopted. It must belong to the
uniquely new verified SystemSettings process, or to the verified Windows
ApplicationFrameHost executable with exactly one topmost Settings child from
that new process. The child's native root must be that exact new frame; a
shared host process does not authorize another window. Pre-existing HWNDs,
multiple candidates, unrelated hosted children, and process replacement fail
closed. Discovery records at most 16 numeric root/child observations.

Controls are read through `AutomationElement.FromHandle` on the retained
Settings child. Before expanding/selecting a control or closing the frame, the
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
succeeds, it runs the existing Edge prose and Explorer diagnostics through both
click and Enter in that same job. Each case requires fresh independent monitor
readback before and after capture, a captured target reporting 192 DPI, and all
existing image/restoration checks. No successful setup receipt substitutes for
these reads. Failure stops the sequence and uploads its bounded observations as
`scrolling-display-setup-200` from `artifacts/simple-scroll`.

Each hosted job receives a different VM, so another job cannot inherit this
setup. The bordered panel needs fully visible responsive geometry and a separate
native 200% result before it is qualified; clipping guards remain unchanged.
A setup probe alone is not Edge, Explorer, scrolling, or hardware qualification.

Current limitations: an initial hosted run confirmed actual 100%/96-DPI
readback and visibly opened Display settings, but exposed a cross-process root
discovery gap before any selection. That is not evidence that 200% is
unavailable. The corrected ownership path, Settings labels/UIA hierarchy,
available scale options, and display-resolution limits still require a reviewed
native run. The Linux tests exercise guards, measurements,
subprocess boundaries, failure handling, and cleanup with doubles. They do not
execute PowerShell, Settings, or Windows APIs. An unavailable 200% selection is
a platform/setup limitation to report, not a reason to use an undocumented API.

## References

- [Microsoft: launch Display settings](https://learn.microsoft.com/windows/apps/develop/launch/launch-settings)
- [Microsoft: monitor-scale result and failure semantics](https://learn.microsoft.com/windows/win32/api/shellscalingapi/nf-shellscalingapi-getscalefactorformonitor)
- [Microsoft: window DPI and awareness](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getdpiforwindow)
- [Microsoft: ComboBox UI Automation patterns](https://learn.microsoft.com/windows/win32/winauto/uiauto-supportcomboboxcontroltype)
- [GitHub: hosted runner isolation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
