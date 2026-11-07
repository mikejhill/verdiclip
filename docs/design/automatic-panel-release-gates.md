# Automatic panel capture release gates

This feature is not enabled in the application. Draft PR #119 must not be merged
or released on the strength of component or fixture tests alone. Released
0.13.2 ordinary capture behavior is the regression baseline.

## Product journey

- One tray command or registered hotkey, one panel click, automatic top-to-bottom
  capture, exactly one normal output/tab. No configuration dialog.
- A point-only native-arrow selector. No cursor relocation, virtual pointer,
  foreign-process injection, drag-mode behavior, click leakage or extra tab.
- Existing freeze-first menu handoff and ordinary capture paths remain intact.
- Controller admission covers both tray and hotkeys, including duplicate requests,
  repeat capture, settings refresh, application shutdown and updates.
- Scrolling remains reserved from selection through confirmed helper cleanup.
  A scoped Escape lease must not unregister unrelated shortcuts.

## Execution and ownership

The existing tray and updater launchers intentionally detach and must not be
reused for capture. Native startup must atomically place the direct helper in a
non-inheritable kill-on-close Job before its first instruction. Plain QProcess
launch is not currently proven to provide this containment guarantee.

- Verify the created PID, kernel-reported executable and actual IPC peer identity.
- No provider call, synchronous pipe wait or process wait on the GUI thread.
- Bounded single-flight protocol, request/session identities and fixed image
  filenames in a private per-session directory. No arbitrary response paths.
- Independent startup, operation and lifetime watchdogs. Distinguish termination
  requested, process retirement, cleanup confirmed and resource release.
- Cancellation, user interference, timeout or uncertain native writes prohibit
  further scrolling/restoration. No partially qualified output is delivered.
- Parent death, child crashes, descendants, malformed/oversized/duplicate messages,
  stale replies and shutdown must have native cleanup tests.

## Image fidelity and useful coverage

- Complete WPF and browser sweeps, not just top/mid/bottom checkpoints.
- Start at top, middle and bottom; short content, many steps, final one-pixel step,
  nested panels, nonzero horizontal position and bordered/padded panels.
- Independent native provider displacement and extent agree with image motion.
  Image uniqueness alone does not reject every periodic-content alias.
- Every retained row comes from an observed original frame. No padding or
  synthesized pixels to hide missing coverage. Exact original state and pixels
  restore before delivery when restoration remains safe.
- Independent full-width fixture reference verifies labels, boundaries and seams,
  in addition to the color-column oracle. Preserve first failures and raw receipts.
- Static chrome, scrollbars and sticky content require an explicit design.
  Stationary pixels do not prove a region is content-free chrome. Screenshots
  cannot recover content that is permanently occluded in every observed frame.
- Browser usefulness includes realistic long conversations, code, tables and
  nested scrollers in light/dark themes. Owned browser fixtures alone do not
  establish usability in ChatGPT. Streaming content is a separate negative case.
- Native 100/125/150/200% DPI, negative monitor origins and mixed-scale geometry
  require qualification distinct from browser zoom. Initial WPF evidence is 96 DPI.

## Regression and responsiveness

- Exact candidate against 0.13.2: region/window/fullscreen/repeat, owned/foreign
  menus, tray/hotkey journeys, tabs, focus, clipboard and save.
- Preserve existing native lifecycle matrix and both supported Qt profiles.
  Historical branch-name workflow gates must be adapted explicitly; skipped jobs
  are not qualification. Do not weaken old assertions to hide changed behavior.
- No doubled or captured pointer at any seam; test cursor positions inside/outside
  the panel, over the scrollbar and during cancellation.
- Record selector latency, GUI heartbeat, cancellation-to-retirement latency,
  matching time, memory and final image delivery. A 90-second safety ceiling is not
  a responsiveness promise. Preserve the existing 150 ms ordinary overlay budget.
- Maximum supported composites must open, copy and save without truncation,
  duplicate delivery or a frozen editor.

Only enable and release the isolated feature after the actual product journey
and these applicable gates pass at the exact release head. No user action is
currently required to continue the internal design and fixture work.
