# Independent review and corrected local candidate

Independent Codex reviewer `/root/independent_tabs_review` reviewed the original
8616e395 source, the first fixes a7ea5f0, and exact corrected production commit
`1b399ffd7548ab13a506544459f9795d2f8aea53`. This was a separate read-only agent;
the implementation author made the fixes. The reviewer reported **no remaining
confirmed material findings** at the corrected commit. This is local source and
offscreen review, not PR publication or native acceptance.

## Findings and disposition

| Severity | Finding | Resolution |
| --- | --- | --- |
| P1 | Global close could dispose a new edit created after an earlier window's approval. | Exact history-state approvals, all-host retained detachment staging, post-signal inline flush and validation, then disposal. Changed work restores actual edited pages. |
| P1 | Clipboard/save callbacks could switch tabs and mark another document delivered. | Pin initiating document and state token across external callbacks; Enter closes that document only. |
| P1 | Reentrant delivery of the same capture could repeat sinks and corrupt pending receipts. | Per-capture in-flight reservation coalesces duplicate/reentrant attempts. |
| P2 | Failed original output sinks could permanently block exit/update. | Explicit Save/Discard/Cancel for retained originals; remove them only after the complete close succeeds. |
| P2 | A closed or forged EDIT ticket could incorrectly return DEFERRED. | Validate owner lifetime and ticket before considering close-time deferral. |
| Optional | Retried admission allocated a redundant document before ID deduplication. | Reuse admitted session before constructing another document. |

The review of a7ea5f0 found a residual P1: `removeTab` could synchronously create
another edit after approval. The final `CloseTransaction` detaches **all** selected
hosts before disposing any document, commits late inline text, checks receipts,
document/window membership and the external output guard again, then disposes
without further tab-removal signals. Rollback restores page order and selection;
allocation failure retains registry/parking owners and exposes recovery even on
an empty host. Job reservations release the original record snapshot even if
restoration moved or parked its pages. No edit history is rolled back.

## Evidence

- Reviewer independently ran 10 focused minimum-Qt staging/rollback cases and
  all 80 workspace/jobs/reviewed-delivery cases on Qt 6.12.0; all passed.
- Author full suites at corrected production source: Qt 6.11.2 **1,374 passed**,
  1 skipped, 14 deselected, **95.07%** coverage, random seed 1143311299;
  Qt 6.12.0 **1,374 passed**, 1 skipped, 14 deselected, **95.05%** coverage,
  random seed 94529288. Changed executable lines: **2,063/2,121 = 97.27%**.
- Additional test-only strengthening covers inline typing for all three close
  paths and external output appearing during detachment: workspace **46 passed**.
- Formatting, Ruff and strict ty against `.venv-min` pass. All Qt tests use
  `QT_QPA_PLATFORM=offscreen`. Full suites exclude native capture/tab modules,
  real global keyboard hook, registry associations, shortcut COM backend and MSS
  screen freeze. No desktop input, hooks or monitor configuration were operated.
- Initial sandbox full attempts each timed out in the existing localhost release
  feed retry fixture. That fixture and the complete suites pass with local socket
  access; the passing runs above use separate coverage files and Temp roots.
- Six seeded close faults were individually rejected by behavioral assertions in
  isolated source copies: omitted post-signal validation, omitted staged inline
  commit, skipped rollback, ignored external guard, lost restored attachment, and
  releasing only still-hosted job reservations. Durable local evidence:
  `.test-tmp/close-mutants/results.json` and per-case logs. This is **6/6 selected
  faults**, not a new aggregate mutmut score. The historical 264/318 mutation run
  and 54-survivor triage retain their original scope and denominator.

## Clean integration boundary and remaining gates

Use a fresh local combined-preview branch from the capture worker's agreed head,
then apply tabs-only changes and the controller delivery integration. Do not
reapply imported scrolling foundation commits or duplicate the socket fix.
The independently reviewed tabs source currently contains canonical capture
foundation f25091d; capture head 9593a40 and its pending nonmodal target/review UI
have **not** been integrated or qualified here.

The single canonical `capture.reviewed.ReviewedCapture` enters
`AppController.deliver_reviewed_capture`, preserves output/tray-only policy, then
`Workspace.open_document(activate=...)`. Capture keeps target/plans/tiles private.
Cancel any workspace drag before capture. The review/session retains ownership
until an explicit controller success acknowledgement keyed by `capture_id`;
returning no editor window is not failure because tray-only delivery is valid.
Keep failures retained and retryable. No existing popup/dialog/window may be
hidden or closed by capture. Capture owns review/acquisition UI; tabs owns this
controller adapter. The acknowledgement wire requires joint integration tests.

Parent coordinates one exclusive native Windows test window. Final candidate must
qualify physical reorder/detach/reattach, cancellation/input capture loss, real
clipboard reentrancy, keyboard/Narrator, mixed DPI/multiple monitors, and capture
with owned/foreign menus and dialogs preserved. A locked desktop is a blocker,
not permission to bypass the session. Private build manifests remain
`native_ui_ready=false`; no native launcher override exists. Normal installation,
settings and integrations remain untouched. Tabs push is unapproved and held;
no merge/release before independent combined review and Mike's local verification.
