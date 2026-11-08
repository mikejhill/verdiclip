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
64 top-level window limit. It refuses any pre-existing Settings process and
requests closure only for the newly identified Settings window when its process
identity still matches. It records confirmed closure only after the retained
UIA element becomes unavailable within two seconds; otherwise closure remains
unverified and the setup fails. If ownership cannot be verified, it leaves the
window alone. Python independently requires the current attempt's successful
cleanup receipt, even if the child exits zero and native scale reaches 200%.
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

## First workflow experiment

The **one** `display-setup` job is restricted to the same-repository draft
diagnostic branch condition, pinned to `windows-2025`, with `contents: read`,
exact-head checkout, uv setup and locked dependency installation. Its probe and
artifact steps are equivalent to:

```yaml
- name: Probe actual hosted 200 percent display scaling
  timeout-minutes: 2
  run: uv run python -m scripts.hosted_display_scale
- uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9
  if: always()
  with:
    name: scrolling-display-setup-200
    path: artifacts/simple-scroll/display-scale
    if-no-files-found: warn
    retention-days: 14
```

No matrix is needed until availability is established. Each hosted job receives
a different VM, so a later capture job cannot inherit this setup. Once setup is
proven, put it inside each selected diagnostic job before launching Qt and the
target application. Require monitor scale 200 and target HWND DPI 192 there too,
before and after the actual scrolling capture. A setup probe alone is not Edge,
Explorer, scrolling, or physical hardware qualification.

Current limitations: the exact hosted image's Settings labels, UIA hierarchy,
available scale options, and display-resolution limits are not known until the
first Windows execution. The Linux tests exercise guards, measurements,
subprocess boundaries, failure handling, and cleanup with doubles. They do not
execute PowerShell, Settings, or Windows APIs. An unavailable 200% selection is
a platform/setup limitation to report, not a reason to use an undocumented API.

## References

- [Microsoft: launch Display settings](https://learn.microsoft.com/windows/apps/develop/launch/launch-settings)
- [Microsoft: monitor-scale result and failure semantics](https://learn.microsoft.com/windows/win32/api/shellscalingapi/nf-shellscalingapi-getscalefactorformonitor)
- [Microsoft: window DPI and awareness](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getdpiforwindow)
- [Microsoft: ComboBox UI Automation patterns](https://learn.microsoft.com/windows/win32/winauto/uiauto-supportcomboboxcontroltype)
- [GitHub: hosted runner isolation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
