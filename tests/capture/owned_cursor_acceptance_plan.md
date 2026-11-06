# Hosted ordinary-owned cursor acceptance boundary

Status: implementation under independent review; no native run, workflow publication or release qualification.
Base: d94a1f0d4f3e70b1ccf3f6d6c4a291accd2f58fa.

## Ownership and protocol

The native adapter owner implements the dedicated GUI thread, natural cursor-message
admission, read-only foreign-state checks and exact one-decrement/one-balance policy.
The harness owner implements a disposable child wrapper, independent supervisor and
restricted hosted workflow. Neither changes product capture behavior.

The supervisor first establishes an existing owned foreign client and independently
records global GetCursorInfo visibility, global shape handle and position, plus
GetForegroundWindow and that foreground thread's GUI state (active/focus/capture/menu/
move-size handles and flags). Class cursor handles are never compared with the global
cursor. An unknown baseline is unsupported, not success.

The component child starts idle. The supervisor retains its exact Popen process handle;
The direct base CPython interpreter and scoped venv launcher environment follow the reviewed PR107 runtime-only pattern. Before start, child PID and independently measured interpreter, image, package and venv fields must match the supervisor. Only after that attestation and recording baseline does it send the private start command. The
child retains the GUI thread and callback owners through retirement. Proposed adapter:
run_owned_surface(cancel_event, emit_fixed_phase, crash_requested=event), which owns and joins that dedicated GUI thread.
The wrapper does not emit terminal retirement until the thread has actually exited.

Bounded phase callbacks: baseline-admitted, surface-created, admitted, refused, revoked,
retired, crash-entry. Admission requires the native adapter's real WM_SETCURSOR ownership evidence;
no synthetic cursor message, input movement, SetCapture, AttachThreadInput, global
visibility override, foreign-process injection or focus manipulation is allowed.

Proposed terminal fields: admitted, reason (fixed enum), balance_attempted,
balance_confirmed, hwnd_destroyed, thread_retired, foreign_preserved, cleanup_confirmed.
An exception or incomplete phase does not imply retirement. Callback messages are not
substitutes for independent OS observations. Native interface remains subject to the
implementer's agreement and independent source review.

## Finite cases

1. Normal retirement after stationary admission, using an existing owned WinForms
   client. The observer records logical hidden state during admission. One terminal
   request, then the first OS observation after confirmed HWND, GUI-thread and input-broker retirement is the
   immutable verdict against the original baseline.
2. Cancellation with the same baseline/first-observation rules.
3. Two repeated sessions per owned client, only after all earlier cleanup is confirmed.
   A second independent owned client establishes that state is not tied to one owner.
4. Preexisting foreign MenuPeer: no hide admission; correlated post-refusal menu
   snapshot must preserve the foreign popup and capture/foreground state.
5. Mid-session MenuPeer intervention: opening a menu legitimately changes foreign GUI
   state, so the pre-entry baseline must not be used as a false preservation oracle.
   The adapter must revoke. The harness must establish the new menu's correlated state
   and require a post-retirement correlated acknowledgement to preserve that menu.
   Cursor equality across this intervention is inconclusive unless an independent
   observation boundary can separate fixture changes from component teardown.
6. Forced death of a fresh exact attested component process, only after prior cases are
   clean. A crash command must be consumed on the same GUI owner thread while its
   decrement is still outstanding and before any balancing. That thread publishes
   the correlated crash-entry marker and immediately self-TerminateProcess(97).
   The supervisor requires that marker and exact child exit97, never normal exit.
   A late command is unsupported. External kill is failure cleanup only.
   No child finally or supervisor cursor repair is available. The first sample
   after confirmed process exit determines restoration; failure remains failure.

No retry until hidden/restored, no correction after an adverse first sample, and no
continuation after unconfirmed owned cleanup. A natural-admission timeout is explicitly
unsupported, never a passing suppression or restoration result. Fixed later samples
may detect delayed interference but cannot erase the first verdict.

## Evidence and unresolved boundaries

All these observations concern Win32 logical cursor metadata, not rendered pointer
pixels or the hardware pointer plane. No green result implies full popup cursor repair,
physical-input qualification, or release readiness.

Before hosted execution: independent source
review, cleanup faults, first-sample immutability controls and hosted identity gates.


## Initial hosted result and narrowly scoped setup amendment

Original PR112 head358eb5d, workflow42288213927: both Qt lanes passed100 portable
controls but failed before component start because the logical visible baseline was
false. Exact flags and handle presence were not retained; touch/pen suppression
cannot be inferred. No hide, balance, restoration or crash case ran.

The amended harness performs one injected MOVE per fresh owned client's initial
setup, before any component child, surface or broker. Exact owned foreground,
client destination/root/process and absence of menu/capture/move-size state are
required. It neither clicks nor changes visibility counters or focus. A correlated
fixture snapshot has a two-second acknowledgment bound; it proves liveness, not
mouse delivery. One post-ack cursor baseline must confirm destination and preserved
GUI state; visibility/handle eligibility is then required before child launch.
Normal/cancel/repeat intervals receive no further setup movement. No after-teardown
normalization or retry exists. Fixed receipt records simulated setup provenance,
showing/hidden/suppressed/unknown and handle-presence without raw IDs/coordinates.
This does not qualify physical input or rendered visibility.


## During-state oracle correction

Changed-source setup run42289425470 established showing/handle-present baselines
and actual admission in both Qt lanes. Logical hidden, stationary position and
foreign GUI checks passed, but the during global-handle equality failed. The run
stopped before normal restoration or crash qualification; it does not establish
either restoration success or failure.

Independent review identified the during global-handle equality as unsupported
across natural thread ownership. It is now retained as information only. During
acceptance requires flags exactly0 (not touch/pen suppression), stationary position,
and unchanged foreign GUI/foreground/process/thread identity. First post-retirement
baseline flags, shape, position and foreign state equality remain mandatory and
unchanged, including after the causally armed crash. No native/API behavior changed.
