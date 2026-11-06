# Test-only identityless first-hit ancestry

Base: reviewed PR99 runtime comparison and Qt-preservation correction (local
ed995fa). A new draft-only same-repository branch selector
`test/native-identityless-ancestry` runs the WinForms/WPF owned fixture experiment.
The prior workflow is retained unchanged and is not selected on this branch.
Production source and Python discovery protocol stay byte-identical. No IDs are
fabricated and no production discovery, reference, capture or mutation succeeds
because of this diagnostic.

## ABI check

Microsoft's [GetRuntimeId declaration](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getruntimeid)
is HRESULT with an output SAFEARRAY** containing integers. The existing COM
method is void (normal HRESULT-to-exception interop) with `out int[]`, explicitly
SafeArray and VT_I4. This matches pointer direction and element representation.
.NET's [array-marshalling documentation](https://learn.microsoft.com/en-us/dotnet/standard/native-interop/default-marshalling-for-arrays)
describes typed SAFEARRAY conversion and rank/type-mismatch exceptions. Thus no
signature defect was found that explains a successful empty result; this is not
a claim that all provider behaviors or raw SAFEARRAY shapes have been proved.

[GetParentElement](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtreewalker-getparentelement)
takes an interface pointer and output interface-pointer pointer. Existing
`IWalker.GetParentElement(IElement, out IElement)` matches that declaration.
Windows tests compile the real interfaces and inspect their marshaling metadata.
The SDK-ordered declarations remain unchanged. CompareElements is deliberately
unused: its documented result compares runtime identifiers, so it is not an
independent missing-identity oracle.

## What is observed

The existing method/property comparison still makes one query each for root and
first hit. These initial arrays remain only in memory and are reused when entering the
trace. Each subsequently encountered parent gets one runtime-ID query, including
the root when reached through the raw parent chain; that is a fresh root identity
check, not a once-per-object claim. The trace validates root identity, same-process hit, strict
control type and ScrollPattern availability. It exports only fixed control
categories (`list_item`, `scrollbar`, `window`, `other`), the availability boolean,
fixed walk outcome, root-reached boolean, and bounded ancestor count.

An identified hit stops without a walk. A missing-ID scrollbar, window or
ScrollPattern-available hit also stops with candidate classification, never
claiming verified ancestry. Oversized/malformed IDs and property errors fail.
A null/empty first hit can be walked only when it is not one of these candidates
and its strict finite inset bounds contain the requested pixel.

The initial leaf consumes one of the existing32 slots. Every subsequent raw
parent must be nonnull, same-process and have a nonempty <=64 signed-int runtime
ID. Repeated IDs fail. A second missing ID fails, including a cycle back to the
original missing leaf. Success requires the exact stored root ID AND original
root HWND within at most31 parent calls. No gaps, normalization, retries,
CompareElements, or inferred parent identity are used. Every call retains
native-only/no-focus, foreground, ownership/generation and physical-DPI guards.

Candidate stop is a successful *observation*, not successful discovery. A
verified suffix describes only the identified parents after the unresolved
leaf, never a complete production hit chain. It does not establish proxy
implementation, cross-request stability or a safe protocol repair. The UIA tree
can change between calls. Native type/proxy names and provider strings are not
queried or exported.

## Tests and gates

Pure Windows C# tests cover direct candidate stops, null/empty initial leaves,
1/2/31-parent success, second missing/oversized IDs, self/cyclic parents, foreign
PID, wrong/missing root, budget exhaustion, malformed properties, out-of-bounds
hits and an injected exception at every provider/guard call. Existing independent
cleanup aggregation tests still cover operation/release/DPI/final-guard failures.
Portable tests verify fixed receipt decoding, candidate/identified stop semantics,
strict branch admission, and independent helper/fixture cleanup. Actual native
compilation and owned fixture execution remain Windows-CI requirements.

Prior budgets, cleanup, alias-aware COM ownership and unexpected direct COM-valued
VARIANT retention are unchanged. Selected Qt remains pinned with `--no-sync`.
No input, focus-setting, screenshots, clipboard work, provider text, or user
machine use occurs. No physical/cursor/performance release gate is relaxed.
