# Test-only native runtime identity comparison

This isolated draft branch investigates the PR97 WinForms refusal. It does not
repair or weaken production identity admission. All production files remain
byte-identical to local PR97 base3168b17. No release or physical qualification is
implied by a diagnostic result, including an `outcome: passed` receipt.

## Question and documented basis

The native GetRuntimeId call returned successfully for the first owned WinForms
hit, but production rejected its array shape before the native-only postguard.
Root identity read and both cleanup guards passed. We do not yet know whether
that first-hit result was null, empty, or oversized.

Microsoft documents [GetRuntimeId](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getruntimeid)
and [RuntimeId property30000](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-automation-element-propids).
The property type is VT_I4|VT_ARRAY, with default VT_EMPTY. General current
property retrieval is documented in
[Properties for Clients](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-propertiesforclients).
Provider-side top-level null conventions are not permission to accept a missing
client identity. IDs stay opaque; no synthesized HWND identity, empty acceptance,
ancestor skipping, managed fallback, or retry is permitted.

## Isolated experiment

The new workflow admits only a same-repository draft PR on
`test/native-runtime-property-comparison`, on separate hosted Windows2025
WinForms/WPF jobs. It checks the pinned head and clean source, disables persisted
credentials and cache, and has read-only repository permissions. No desktop or
user's machine is used. The experiment creates only its owned provider and popup.
It performs no input, focus-setting, scrolling, cursor preparation, or capture.

The new helper compiles unchanged production COM interop, desktop and discovery
sources with the test-only C# probe. It accesses NativeDiscoveryBackend directly,
not DiscoveryResolver/Session, avoiding their automatic runtime reads. After
HWND/PID/start-time and interactive/visible/foreground validation, it acquires
one root and one first-hit element at the fixture viewport's containing center
pixel. Each receives exactly one GetRuntimeId call and exactly one property30000
query, method first. Both element PIDs must match the owned fixture; root HWND
must match. There is no traversal or claim of full ancestry qualification.

Existing AutoSetFocus=0, MTA, native-only and foreground guards remain active.
Physical DPI scope is verified. Every acquisition, including unexpected direct
COM-valued properties and partial out values, uses production ownership and
reverse alias-aware final-zero release. Cleanup, DPI restoration, and final
ownership/foreground/native-only validation all run independently. Any failure
prevents a successful result. Method/property exceptions are terminal, not
unsupported/empty classifications or justification for a fallback call.

Results contain only `null`, `empty`, `oversized`, `wrong_type`, `valid` and nullable
equality booleans for root/hit. Equality is present only when both arrays meet
unchanged nonempty, <=64 signed-int constraints. No IDs, coordinates, provider
names, HWNDs, paths, messages, arrays, HRESULTs, or arbitrary text are exported.
Sanitized source hashes, fixed metadata, bounded worker receipts and cleanup
states accompany these flags. Invalid shapes can be successfully *observed*;
they are never accepted as production identities.

The existing isolated-process budgets remain: fixture20s, helper startup8s,
operation3s, cleanup2s, outer owned case65s/overall75s with tree retirement within
90s. No unchanged rerun, query retry, warmup or hidden fallback is introduced.
Additional property/guard calls change timing: this is not performance parity
or native-versus-managed equivalence evidence.

## Verification and interpretation

Portable injected tests exercise receipt whitelisting, strict guard flags,
source/profile admission and independent helper/popup/fixture retirement faults.
Windows tests compile the actual native ABI plus probe and execute injected
shape/call-count/exception/cleanup policies, including all16 combinations of
operation/release/restore/final-guard failure. Actual PowerShell admission tests
reject coercible identity/coordinate types. The workflow executes these before
its owned fixture run. Local Linux skips the Windows compiler and PS tests;
hosted CI is mandatory before interpreting results.

A valid property where the method is invalid would justify a separately reviewed
primary-retrieval investigation, not an immediate fallback or merge. Both-valid
unequal results require identity compatibility analysis. Missing property means
continued refusal. Even equality here does not establish cross-helper mutation
identity compatibility or satisfy cursor/physical/performance release holds.


## Workflow runtime preservation

The first hosted comparison run passed both sets of52 Windows pure controls but
failed before the fixture receipt. Its log shows that `uv run pytest` after the
explicit Qt6.12.0 install synchronized two packages back to the lockfile runtime
(Qt6.11.2). The unchanged admission requires6.12.0. Controls now use
`uv run --no-sync`, and the exact venv interpreter checks Qt6.12.0 again before
starting the native runner. Admission and constructor failures also produce a
fixed-label failed receipt; no raw pytest/provider output is published.
