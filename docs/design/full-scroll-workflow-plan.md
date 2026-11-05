# Pure full scrolling fixture protocol

`scripts/scroll_workflow_plan.py` gates a separately implemented real Qt fixture.
It imports no Qt, invokes no UI/input/backend operation, observes no desktop and
publishes no passing receipt. Control goals refer to existing selection semantics
and actual panel accessible names/button text; they are not automation commands.

Construct immutable `Labels(source_sha, case_id, generation)` and
`Planner(labels, now, bounds)`. Generation must represent the fixture's actual
owned window/process generation. Supply one monotonic clock for observation,
reservation and completion times. The default bounds are 80 seconds total,
15 seconds per ordinary phase, 30 seconds capturing, 10 seconds cleanup,
5 seconds per command, 2 seconds observation freshness and 32 commands.
The fixture still needs its own independent process watchdog and cleanup.

Start with a real `Observation(labels, 1, now, Phase.SELECTING)` via `observe`.
Before one real semantic Qt action, call `reserve(Action, now)` and retain its
`Command`. Only after observing the actual result call
`complete(command.command_id, observation, now)`. Observation sequences advance
exactly by one. Source/case/generation, timestamps, phase and deadlines are all
checked. A reserved descriptor is neither an acknowledgement nor evidence that
the UI operation happened.

The route is BEGIN_SELECTION, optional ADJUST_SELECTION, FINISH_SELECTION,
explicit START, capture, interactive review and optional CHOOSE_ROWS, then one
ACCEPT_WHOLE/ACCEPT_ROWS or CANCEL. STOP transitions capture to review but does
not reserve acceptance. START completion requires actual `start_confirmed=True`;
backend movement before that completed boundary fails. Spontaneous provider end
may be observed as CAPTURING to REVIEW when no command is pending.

Accept/cancel completion enters CLEANUP. Observe actual cleanup confirmation in
CLEANUP before a separate DELIVERED/CANCELLED observation. Delivery requires the
one acceptance reservation; cancellation requires its cancellation reservation.
Real output/pixel identity, preserved window state, cleanup and delivery assertions
belong to the Qt fixture, not this planner. Backend state or inferred success must
never manufacture observation flags. If several real signals occur synchronously
inside an action, retain their actual order/timestamps and submit those observed
facts in order; do not invent intermediate phases.

Only one command can be pending. Unknown/refused outcomes use `unknown` or
`complete(..., certain=False)` and permanently fail the planner. Expiry,
duplicates, skipped sequences, foreign generations, out-of-order phases and an
exhausted budget also fail permanently. A delayed successful result cannot revive
the case. The caller must retire its owned case safely; planner failure itself
does not close windows, release hooks, cancel a backend or prove cleanup.

Run the pure tests directly, avoiding pytest's root Qt conftest:

```powershell
python -B tests/portable/test_scroll_workflow_plan.py -v
```

This protocol is fixture preparation. It does not qualify native input ingress,
OS keyboard mapping, provider compatibility, physical Raw Input or a delivered
scrolling feature. The frozen feature candidate and existing seals are unchanged.


## Executable full workflow fixture

`tests/capture/scroll_journey_fixture.py` composes the real `CaptureService`,
`ScrollWorkflow`, target/progress/review widgets, stitching/session storage,
controller output policy and ordinary editor document admission. Its injected
ports are explicitly `ReplayInputBroker`, `SnapshotWindows`, `FakeDesktop`,
`DiscoveryFixture` and a finite patterned provider. No `binding.start_confirmed`
or review acceptance signal is invoked directly by the journey driver.

The executable `tests/capture/test_scroll_full_journey.py` covers normal scrollbar
convenience plus explicit dragged and Enter-based rectangles with replayed virtual-pointer points; actual keyboard
order for Start; whole and selected-row acceptance; reproducible Stop with a
held fake move; confirmation/review Escape; delayed and unconfirmed input
cleanup; and explicit retry after editor admission fails. Expected output is
compared pixel-for-pixel with independently selected rows of a deep pre-acquisition source snapshot,
then against both the admitted document and the decoded saved PNG. The provider
source must still match that pre-acquisition snapshot after retirement. Retry must
retain one canonical capture identity and leave the saved PNG bytes/timestamp
unchanged. No output or tab may appear before the input-cleanup receipt.

The planner reserves each semantic goal before the operation and admits only
actual post-operation observations. An explicit known delivery failure may
retain its review and retry the same capture; an unknown result, stale identity,
expired deadline or missing cleanup cannot produce a terminal success. Its
labels use a declared **replay generation**, never a fabricated HWND or physical
input receipt. The planner does not emulate success, click UI or acknowledge
production input cleanup by itself.

Enter-based region selection supplies explicit replayed pointer endpoints. It
is not a test of arrow-key packet translation or physical keyboard ingress;
those remain separately labelled input-policy/native tests. Confirmation and
review do exercise the real controls' semantic Tab/arrow/Enter handling.

Cleanup uses independent owned callbacks and always attempts the synthetic
receipt writer even if a cleanup callback fails. Each cleanup attempt keeps a separately numbered receipt, so a later verified
retirement cannot overwrite earlier uncertainty. An injected failing-cancel
case checks that the other owners and the failure receipt are still retired.
A receipt records attempted
versus confirmed cleanup and directs readers to JUnit; a phase label never
asserts that the test passed.

The module overrides the legacy clipboard teardown and fences native MSS freeze,
hook installation, UIA inspection/helper start and Python clipboard access with
immediate failures. All settings/output/spool are test-owned temporary paths.
The normal shell factory creates its owned tray icon and dialogs; those real Qt
objects belong to the disposable hosted job. Qt can internally create a hidden
clipboard listener; that is distinct from clipboard-content operations.

## Hosted execution and retained evidence

`.github/workflows/scroll-workflow-fixture.yml` runs the exact reviewed PR head
on standard Windows x64 runners with Python 3.13.16 and Qt 6.11.2/6.12.0, separately
under offscreen and Windows QPA. The job is bounded to eight minutes, with a
three-minute UI-fixture step and no cancellation of another progressing run.
It verifies checkout/Qt identity, runs the standalone pure protocol tests, then
executes the selected full journey module. Normal aggregate and capture-contract
CI also collect the module; a missing source label skips before fixture setup.
The labels configure the harness and grant no local desktop authority.

Artifacts contain JUnit and bounded synthetic command/identity/retirement JSON.
No desktop frames, user screenshots, clipboard content or generated PNG payloads
are uploaded. `native_input_qualified`, `real_provider_qualified` and
`physical_input_qualified` are explicitly false. A failed/timeout job is not
qualified, and runner disposal is containment rather than a cleanup proof.

## What this does and does not qualify

A passing run supplies reproducible full **semantic workflow** evidence, including
real confirmation/review controls and delivery. It is more than a direct
provider/session component test. It still does not qualify native broker packet
ingress, actual UIA providers, Windows pixel acquisition, physical input,
Narrator, monitor topology or the complete production composition over those
native ports. Those are separately labelled qualification lanes; passing them
individually is not proof that their combination works.

The existing local acceptance candidate remains frozen at
`193bba384363303dbe8aba4cda7376cf969681c8`; these additions are on a separate branch
and do not alter that pending handoff or any delivered smoke snapshot. No local
Qt, capture or input execution is authorized by this preparation. Full native
selection/confirmation/review composition needs its own reviewed bounded fixture
and fresh handoff; do not add extra keys or provider work to the existing
18-minute session. No readiness flag, feature merge, release, driver dependency
or installed-app setting changes follow from this fixture's result.
