# Cold root acquisition: managed versus native COM

This is a diagnostic-only architecture comparison. It changes no product path
and grants no scrolling, cursor, physical-device, merge or release qualification.

## Fixed experiment

One batch contains four independently fresh hosted Windows 2025 VMs:
WinForms/managed, WinForms/native, WPF/managed, WPF/native. All use Python 3.13,
Qt 6.12.0, scale 1, one immutable fixture definition per provider, a fresh owned
popup, one helper and one request. Never run both clients against the same live
fixture, since the second arm would inherit provider/proxy warming. Separate
VMs avoid intentional process-level reuse but do not make runners identical.

The workflow is restricted to the same-repository draft branch
`diagnose/uia-root-comparison`; normal CI and its five native profiles are
unchanged. Actions remain pinned and permissions read-only. Parent review and
publication precede execution; failures remain failures rather than triggering
fallback, rerun or a longer deadline.

Both clients call directly from Windows PowerShell 5.1's MTA caller; the native
entry does not create another thread. Startup loads/compiles test scaffolding,
but does not create a UIA COM client or query a provider. Native CUIAutomation8
construction, per-client no-focus policy, root acquisition, identity validation,
RCW release and DPI restoration are all inside the existing 3,000 ms operation
budget. Managed reference drop is labeled separately from native RCW release;
their release timings are not equivalent workloads. Neither replaces confirmed
helper process retirement. Startup remains 8,000 ms, fixture readiness 20,000 ms, case 65 seconds,
wrapper work 75 seconds, outer cleanup envelope 90 seconds. No timing threshold
or retry is added. A success reply follows successful release and DPI restore.

The existing fixture and private worker lease provide owned process generation,
finite deadlines and cleanup. Both arms require an unchanged owned popup and
foreground, default interactive desktop, physical DPI context, visible live
HWND, expected PID/process generation, matching returned root PID/native HWND,
and a bounded nonempty runtime ID. There are no clicks, hooks, cursor changes,
scroll pattern calls, captures, provider strings, or raw identifiers in exported
receipts. Fixed phases, bounded durations, source hashes/head/tree, runtime
versions and preservation/retirement booleans are sufficient. A missing receipt
is explicitly recorded as missing with an expected, not verified, source head.

## Decision table

- Native validates and releases within budget while managed stalls: the native
  architecture is feasible for this root case. This does **not** identify CLR
  initialization as the cause, establish API parity, or qualify capture.
- Both stall: no native rewrite is justified by this experiment. Shared provider,
  COM/proxy or runner constraints remain possible.
- Both pass: the original failing context was not reproduced. Do not call it a fix.
- Native stalls during construction: moving construction to excluded startup is
  not a remedy. The proposed path has not met the operation contract.
- Missing, identity-mismatched, foreground-changed, or cleanup-failed result:
  retain the failure; it cannot become a timing success.
- Different popup/foreground owner class or fixture readiness failure: comparison
  is not equivalent. Report the condition rather than manufacturing another arm.

Stop after the four terminal outcomes. A promising result leads to a separately
reviewed implementation proposal for **all read-only resolution operations**:
inspect, observe and sample. Inspect-only replacement could merely move the first
managed delay into observe/sample. Any proposal must preserve nearest-first
bounded ancestry, window/process/runtime identity, optional-pattern versus
provider-failure distinctions, control/orientation, geometry and references,
and the existing Python decoder. This root test proves none of that parity.

Microsoft documents both [native HWND element retrieval](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-elementfromhandle)
and [non-UI MTA threading and apartment lifetime](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading).
Those contracts support the comparison setup, not a claim about the cause of
this particular timeout.
