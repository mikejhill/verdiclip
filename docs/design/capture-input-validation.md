# Capture without changing the foreground

The former service closed Qt popups, released grabs and activated its selection
window. A popup/modal event loop could still own input, producing visible but
unresponsive selection. The replacement separates selection input from Qt's
foreground routing: a finite installer thread publishes semantic commands to
nonactivating, input-transparent overlays. It never closes, hides, activates or
changes the modality of a pre-existing window, and never forwards a selection
click into a target application.

## Contracts

- One queued capture, one input lease, one terminal cleanup receipt. Cancellation,
  stale callbacks, display changes and repeated requests cannot deliver a second
  result. Delivery waits for confirmed hook/raw-input cleanup; failed cleanup
  blocks retry and gives an explicit restart message.
- Mouse/keyboard down/up ownership stays paired. Initial held keys retain their
  original recipient. Escape, Enter and arrows become semantic selection events;
  unrelated keyboard interference cancels. No text is decoded or logged.
- Raw Input provides explicit relative/absolute movement metadata. Relative raw
  deltas do not reproduce Windows cursor acceleration. Unknown injected movement
  semantics refuse selection; the replay profile is explicit and test-only.
- Ordinary input desktop, unchanged foreground and supported recipient integrity
  are checked before readiness and during the lease. Higher-integrity, unknown
  and ambiguous transparent recipients refuse ownership. No elevation, UIAccess,
  security policy, driver or persistent hook is introduced.
- GUI heartbeat, hard deadline, raw-delivery deadline, bounded event queue and
  bounded release draining limit resource ownership independently of GUI health.
- `Capture.preserve_foreground=True` and the service's corresponding property
  require the shell to present delivery and errors without activation. This is
  separate from the canonical `ReviewedCapture` contract. Combined shell behavior
  remains an integration gate.

## Portable primary evidence

The exact staged candidate passed **1,533 tests**, one expected platform skip and
15 supplemental desktop tests deselected. Total coverage was **95.35%**; changed
line coverage was **95.15%** against `origin/main`, above the unchanged 95% gate.
Format, Ruff, strict ty and independent Codex review passed.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
uv run pytest -m 'not native_desktop' --cov --cov-report=xml --randomly-seed=231027
uvx diff-cover@10.6.0 coverage.xml --compare-branch origin/main --fail-under 95
```

`tests/conftest.py` explicitly substitutes the replay broker for portable tests;
production has no environment-based fake-input fallback. Fake Win32 DLL fixtures
exercise native ABI bindings, partial setup, malformed raw packets, bounded
registration reads, cleanup failures and integrity queries without accessing a
desktop. Synthetic topology covers removal, identity/scale change and generation
races. Real Qt fixtures retain existing main/About/settings/modal/menu/submenu/
combo windows, checking zero Hide/Close events and unchanged popup/grab/modality
state through semantic drag or cancellation.

The same `test_public_capture_never_dismisses_existing_popup` fails on the prior
service with recorded **Close and Hide** events, then passes on the replacement.
This is an offscreen regression, not a native-input result. Modal journeys assert
preserved visibility and modality while using the new semantic input route; Qt
may still label an overlay blocked in its ordinary input route.

## Pending compatibility and acceptance gates

The desktop is locked. No production native broker test has been attempted in
that state. Earlier injected prototype traces are feasibility evidence only.
Supplemental native qualification must cover real Qt/native menus, owned modal
dialogs, raw-input delivery, cancellation, drag, repeated requests and confirmed
cleanup. Physical pointer acceleration/precision and actual hardware remain
honestly distinct from deterministic replay. A transparent hit can safely refuse
capture rather than guess its underlying recipient.

This component is not completed scrolling capture. Semantic scrollbar selection,
explicit hidden-bar entry, confirmation/progress/review input routing, broader
registration compatibility and combined shell integration remain separate work.
Native scrolling stays unavailable by default. Mike must verify the completed
isolated build before merge or release. The installed daily 0.12.1 is unchanged.
