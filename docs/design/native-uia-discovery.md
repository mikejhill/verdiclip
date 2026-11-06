# Native read-only UIA candidate

This unqualified candidate replaces managed UIA discovery for inspect, observe
and sample together. It does not change the step/restore worker, release holds,
operation budgets, fixture readiness limits, cursor policy or physical-input
qualification requirements. Root-only native experiments are feasibility evidence,
not proof of full semantic parity or the cause of historical cold timeouts.

## Ownership and protocol

Each request owns a CUIAutomation8 instance, disables and verifies its AutoSetFocus
policy, and owns its raw walker, elements, patterns and unexpected COM-valued
properties. All out-return acquisitions are recorded in a finally boundary,
including partial output followed by failure. Each marshalled acquisition is
balanced in reverse order. Aliased RCWs can have intermediate positive reference
counts; the final acquired reference for each RCW must reach zero. Independent
release failures are collected, and foreground and DPI cleanup still run.

The PowerShell operation returns its result only after process ownership and
native session cleanup. Release or verified DPI restoration failure becomes a
failed operation. If sample already wrote an owned frame, this failure does not
admit the image: the Python sample destination owner retains cleanup ownership.
This is a change from sending a successful result before unchecked restoration.

The discovery helper does not load UIAutomationClient, UIAutomationTypes or
WindowsBase, and does not compile the mutation implementation. It still loads
System.Drawing for its existing capture code; native qualification must cover its
initialization, not extrapolate from the root-only probe.

Request admission requires exact fields/types before native work. Numeric strings,
bools as identities/coordinates, nonfinite values, malformed runtime identities,
nonintegral bounds and unexpected fields are refused. Sample requires an actual
true boolean, an explicit integral viewport bounded to 16 million pixels, and the
existing private frame destination and encoded-size checks. Observe intentionally
accepts valid changed scroll position; stale mutation-position policy is not
copied into observation.

## Semantics

- Native properties must have the expected VARIANT type. PID/HWND, process start
  ticks, signed bounded runtime ID and nearest-first raw ancestry identify a
  target. Cycles, foreign processes, absent root and depth beyond 32 refuse.
- The optional ScrollPattern uses the typed availability property. False means
  absent; acquisition failure, malformed property or arbitrary COM error fails
  the operation, rather than masquerading as an unsupported area.
- Bounds preserve inset physical geometry: ceiling left/top and flooring right/
  bottom. Nonfinite, empty and overflowing rectangles refuse.
- Hit-test coordinates explicitly use floor to choose a containing physical
  pixel. This is a deliberate native policy, not asserted equivalence to managed
  FromPoint. Tests cover negative fractions and odd-size centers. Native fixtures
  must confirm scrollbar/content boundaries and cross-helper runtime-ID parity.
- Foreground and no-focus policy are checked around discovery provider reads;
  full-area occlusion checks and sample consent remain. Native checks do not prove
  universal visibility of custom compositor overlays or physical-pointer behavior.

## Required evidence before qualification

1. Pure injected policy tests must compile and run on Windows: strict types,
   geometry, runtime IDs, ancestry/cycles/depth, optional patterns, provider errors,
   malformed metrics, partial acquisitions and combined independent cleanup faults.
2. Actual PowerShell operation tests use a fake session, no desktop: strict
   admission before native access, complete response schema, changed observation
   position, sample consent/destination checks, no write for inspect/observe, and
   no result on primary or cleanup failure.
3. Complete CI, package-byte verification, code scanning and independent review
   must pass against the exact candidate. Restricted Linux private-pipe failures
   remain failures and are not counted as Windows qualification.
4. All five existing owned WinForms/WPF profiles must retain native discovery →
   step → observe → sample → explicit restore compatibility, exact screenshot
   pixels, popup/foreground preservation and unchanged cold operation budgets.
   Warm/cancellation/hostile-provider and fractional/negative-coordinate cases
   remain necessary; repeated root successes alone cannot replace them.
5. Cursor rendering/global-state/crash concerns, physical scrolling qualification,
   retained timing/readiness failures and final release acceptance remain held.


## First CI correction

The first candidate's four WPF profiles passed, but WinForms refused during first
ancestor identity inspection before any parent traversal, without a deadline
expiration. Fixed labels now separate the PID read, PID guard, runtime read and
cycle guard, and independently report release/restoration failure. Guards and
budgets remain unchanged; no provider values or raw identities are logged.

Windows tests exposed a fake C# local-name collision and a fake operation failure
that could still return a result. The original test did not label which injected
fault failed, so its exact stage remains unverified. The helper crossed its
PowerShell boundary via
provider-backed .NET property getters, whose failures can be hidden by property
adaptation. It now uses explicit read methods for runtime IDs, bounds, control/
orientation, enabled/offscreen state and scroll metrics. Executable fault tests
require each such failure to propagate with no result and preserved cleanup.
The C# fake naming correction does not alter tested policy. These changes need
fresh Windows validation; neither the earlier failures nor native holds are erased.
