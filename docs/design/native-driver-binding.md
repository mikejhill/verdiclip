# Local qualification and driver-binding correction

At head `16fa747857318faa105e7e23b6252f3b94429863`, the authorized local
session on October 27 ran from 20:13:35 to 20:15:32 UTC. All six synthetic
delivery cases passed across Qt 6.11.2 and 6.12.0. The first Qt 6.11.2 broker
case failed after 10.96 seconds: no declared Escape pair was observed, and the
capture completion deadline expired. No input was sent: the guarded inventory
returned no declared fixture. Native worker/platform cleanup succeeded, with
no cleanup errors or task-owned Python process left. The matrix stopped there;
no physical-pointer qualification occurred. Original receipts remain under
`.test-tmp/native-broker-5f861ff22b9d40dbac984ad358703185` and delivery receipts
under `.test-tmp/native-broker-89a886cfdb3b401992e9892f1cd48103`.

Subsequent source review found a separate teardown side effect: the shared
`clean_clipboard` fixture cleared the clipboard on native Windows QPA too.
The earlier no-clipboard-change claim therefore cannot be retained. Prior
clipboard content was not recorded, so its loss or prior emptiness is unknown.
Cleanup is now restricted to offscreen QPA, with native-name simulation forbidding
even clipboard access. No current clipboard read or restoration was attempted.

An additional full-suite diagnostic selected legacy keyboard-hook tests even
with `not native_desktop`: those classes previously lacked a native-side-effect
marker. Four tests briefly installed real hooks and stopped them; a fifth read
modifier state. The process exited. No input was injected, and actual user-input
interference was not observed or measured. This exceeded the intended local
noninteractive scope. Hook and disposable registry/Explorer classes now have
explicit `native_hooks` and `native_registry` markers. Normal CI retains their
assertions; local no-interaction selection excludes all three native markers.
Do not use offscreen QPA alone as a side-effect boundary.

The diagnostic full run also had sandbox IPC/registry failures and a Git fixture
that discovered its enclosing checkout. Seven IPC/loopback cases passed in a
bounded offscreen diagnostic outside the sandbox with unchanged assertions and
timeouts. The unreadable-checkout fixture now has an explicitly invalid Git
marker. The failed full-run logs remain diagnostic evidence, not final-head
coverage proof. Final local checks use an audited noninteractive selection;
normal complete Windows CI remains required before merge.

This is not proof of a production capture defect. Saved code confirms that
target discovery previously began only after capture and its ten-second
deadline started. Timing versus popup-only inventory visibility is unproven.
The correction prepares a normal editor-owned popup and a separate, bounded
twenty-second bind-only phase before starting capture. No native rerun has
qualified this correction; Mike's running smoke snapshot is unchanged.

The guarded driver must first observe the offered normal anchor and its owned
popup/modal, then atomically write `driver-bound.json` with the offered nonce,
exact `binding_hwnd`, exact `owned_hwnds` list and integer `physical_rectangle`,
and `phase: bound-no-input`.
No input is permitted during binding. Stale/foreign identities, changed geometry,
lost popups and oversized acknowledgements fail before capture. Partial JSON is
not readiness. Missing binding fails its twenty-second deadline without starting
a native worker. The harness rechecks binding immediately before capture.

Only a later `driver-request.json` with the same nonce and `phase: input-now`
describes the allowed action. A fresh guarded target observation and absence of
`driver-retired.json` are required before each input. A file acknowledgement
alone never passes a case: unchanged native input-pair, terminal, preservation
and cleanup assertions still apply. Teardown retires instructions before hosts
are removed. The production worker/capture deadlines and outer forty-second
case bound are unchanged; the observer accounts separately for binding and the
original fourteen-second completion observation. No driver auto-ack or alternate
input path is added. A new coordinated handoff is required for local execution.
