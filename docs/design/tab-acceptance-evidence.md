> Historical design/evidence retained from the 0.13 development cycle. The corrective release restores the exact 0.12.1 capture implementation with tabs. Broker-based menu preservation, reviewed/scrolling capture and native-pointer experiments described here are not included. See [current corrective scope](../corrective-release-scope.md).

# Tab acceptance evidence

Mike reported: "Done. Manual smoke tests pass. You *do* need to create automated tests for this as well, though."

This is a user-reported manual pass of the isolated editor checklist on commit
193bba384363303dbe8aba4cda7376cf969681c8, Python3.13.5/Qt6.12.0. It is not an
agent-observed native automated pass, a scrolling acceptance result or merge approval.
The delivered snapshot remains unchanged.

| Checklist | Existing automated coverage | Added coverage |
| --- | --- | --- |
| Edit/switch, Undo/Redo | workspace grouping; generated content state sequences | One real editor-smoke journey through all transitions |
| Reorder | workspace page identity; keyboard UX journey | Actual tabMoved signal in the smoke composition |
| Detach/reattach | identity/pixels/selection/zoom/tool preservation; property moves | Same edited page reattached before save/close |
| Escape | tab-bar gesture tests; native queue case pending | Qt Escape through live offscreen tab bar retains both documents |
| Save/close | atomic encoding/commit failure; reentrant save; close cancellation | Save pixels after transfer; cancel close; partial encoder failure retains old file and dirty edits; retry save-and-close |
| Lifecycle/rollback | partial source/destination failure, reentrant close, job ownership, stale callbacks | Existing suite retained unchanged |

The existing simulated-tabs CI command now includes test_editor_smoke.py on both
supported Qt profiles. It refuses native DLL/screen/shell operations and uses
QT_QPA_PLATFORM=offscreen. Native physical input and compositor behavior remain
separate evidence; portable success does not claim those.

## Local automated validation

Both Qt6.11.2 and6.12.0 simulated-tab suites passed314 tests with scoped line
coverage97.77% and branch coverage92.47%. After independent review strengthened
the stale-image oracle with a visibly distinct second edit, the complete smoke
module passed7 tests on each runtime. Ruff and type checks passed. The initial
focused setup lacked its temporary parent directory, and the first failure-path
run waited on an unmocked offscreen warning dialog; neither was counted as a pass.
The test now asserts the warning response without altering production behavior.

No desktop input, installed-app changes, or scrolling qualification occurred.
