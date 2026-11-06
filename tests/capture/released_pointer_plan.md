# Released-source pointer comparison

This isolated experiment runs the unmodified `CaptureService` and overlay from
v0.12.1 commit `a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed`. It measures that source on
the explicit current Python/Qt CI matrix, not the historical packaged executable.
The candidate's previously failed first-terminal observations remain failures.
An old-version mismatch cannot waive a new-version regression or release gate.

The workflow checks out the exact release separately and requires a clean tree.
A fresh spawn child rejects any preloaded `verdiclip` module, prepends the verified
release source, verifies fixed service/overlay hashes and all loaded product
module origins, then attests runtime and its exact retained process PID before
constructing QApplication or a capture service. The service bytes are not patched.

Two fixed cases run in separate disposable jobs per Qt version:

1. Actual region selection, stationary active native CrossCursor proof, normal
   cancellation through the released service, owned HWND retirement, then the
   parent's first independent cursor sample while the child is still alive.
2. Actual region selection, the same fresh live-window/pointer proof on the GUI
   thread, a correlated crash-entry marker immediately followed by self
   TerminateProcess97, exact process/HWND retirement, then the parent's first
   independent cursor sample.

Both use the same externally owned WinForms fixture and constructed monochrome
cursor specification as the candidate test. Before the one pre-scope MOVE,
the fixture arms a fresh observer epoch at an owned client point distinct from
the initial pointer position. After the normal WM_SETCURSOR handler returns at
that target, it publishes one immutable readiness acknowledgment. The parent
waits at most two seconds for arm plus event receipt, then makes one baseline
appearance copy. This is handler readiness, not exact input-packet attribution.
There is no fixed settling delay or polling until a favorable cursor shape. There are no menus, post-start moves, cursor writes, visibility
corrections, recovery samples, retries or input injected into unrelated windows.
The fixture's cursor handle is retained until confirmed exact fixture-process
exit rather than destroyed while potentially in use.

The old release activates its overlays and may dismiss its own Qt popup owners.
This experiment deliberately provides no popup. It records foreground transfer
and first terminal GUI state without imposing the candidate's no-transfer policy.
Appearance, position and visibility mismatches fail. Old focus differences remain
visible in fixed receipts; they are not corrected by the harness.

The first snapshot records only fixed comparison booleans: fixture appearance,
mask/dimensions/hotspot equality, expected shared CrossCursor identity, position,
visibility and GUI preservation. Unsupported/unreadable appearance remains a
failure. No raw cursor bitmaps, handles, private paths or process IDs are logged.
Normal child exit follows the immutable observation; failure cleanup never
counts as crash success. Separate jobs prevent proceeding into another native
case after an uncertain owned-process cleanup.

This is a bounded cancellation/crash comparison, not an exact replay of the
candidate's fourth-session drag/keyboard history, mixed-DPI qualification,
physical input testing, a desktop-rendered uniqueness oracle, or a captured-popup
solution. Release remains held.

### First qualification and readiness correction

PR115's initial six jobs stopped at the ready-receipt prerequisite before either
capture implementation ran. The rejected receipt value was not retained, so the
specific runtime field mismatch is not claimed as directly observed. The
nonzero-message-result requirement is independently invalid: Microsoft's
[WinForms v3.0.0 Control.WmSetCursor source](https://github.com/dotnet/winforms/blob/v3.0.0/src/System.Windows.Forms/src/System/Windows/Forms/Control.cs)
(and current official source) sets the own-client cursor without assigning the
message result. This source establishes the semantic defect, not exact binary
attestation of the installed .NET Framework fixture runtime.

Readiness now records normal-handler completion separately. On the first matching
completed handler, the observer permanently disarms and takes one GetCursorInfo
sample requiring visible state and the exact constructed cursor assigned to this
owned fixture. A failed read/mismatch is latched, never retried. The observer is
still detached before acknowledgment; the independent single baseline appearance
copy and every first-terminal restoration assertion remain unchanged. This adds
no cursor-state writes, movement, deadline extension or production change.

### Separate subsequent interaction

First terminal observations and their original verdicts remain immutable. After
confirmed resource retirement, an appearance-only mismatch (or the last successful
ordinary session) can be followed by one separately labeled normal mouse MOVE.
The same strict owned fixture foreground/client/no-menu/no-capture guards apply;
there is no forced foreground or cursor write. A separate one-use readiness phase
arms the normal handler, observes that single MOVE's target, detaches, and permits
one subsequent cursor/GUI sample. This is simulated subsequent interaction, not
teardown repair or a replacement first sample. A failing ordinary session stops
immediately after its follow-up; no later session benefits from it. Old-source
foreground differences can legitimately make this follow-up unavailable. Physical
input and rendered-pointer behavior remain unqualified.
The original sixteen-command ordinary input cap is retained. The separate phase
has one dedicated additional MOVE allowance, consumed on its first attempt even
if insertion fails; it cannot authorize clicks/keys or a second follow-up MOVE.
