# Bounded parent-observed UIA timing

The 32-event tail still diagnoses recent helper activity. It cannot retain cold
startup after a long inspect or capture. Each helper lifetime now also retains
one immutable first-request snapshot and five fixed action summaries. No request
ID, provider value, endpoint, payload, path or absolute clock is added.

First milestones are queued, launch requested, process started, protocol ready,
request sent and terminal, plus fixed action/outcome labels. First occurrences
remain fixed even if the clock was unavailable (null), later requests succeed,
or the event tail is evicted. A new owned helper resets the lifetime. These are
parent observations, not provider timestamps or end-to-end application launch.
The first snapshot may be incomplete while the operation is active.

Each action records accepted/sent counts and mutually exclusive success, failure,
cancellation and timeout counts. Startup/operation timeout are separate in the
first outcome. Maximum queue-to-terminal, sent-to-terminal and successful
sent-to-terminal durations retain failed samples without presenting them as
successful latency. A never-sent operation has no sent duration. Idle retirement
and repeated cancellation do not manufacture additional terminal observations.
Terminal accounting precedes callbacks so reentrant admission cannot overwrite it.
The pending action remains one fixed enum, never a retained request object.

Counts saturate at 2^31-1 with an explicit flag. Millisecond timestamps are bounded
to 2^31-1; invalid, regressing, overflowing or throwing clocks produce missing
timing and an unavailable flag. Optional retention errors cannot alter operation
results. Partial diagnostic evidence with that flag is not qualification evidence.
Frozen snapshot values cannot change after a caller retains them. Neither the
8-second startup nor 3-second operation deadline changes. The extra observation
work stays inside existing budgets; no wait, retry, provider call or timer is added.

The native receipt's existing dataclass serialization includes these fields.
The historical internal scope label `managed` is not evidence that managed UIA
assemblies were loaded; interpret the recorded source/implementation separately.

Validation covers tail eviction, warm reuse, immutable snapshots, all five action
slots, saturation, missing clocks, cancellation before/after dispatch, failure
replies, child exit, reentrant success and late deadline callbacks. Protocol peers
are owned synthetic processes with no desktop/provider access. Actual hosted
receipts on the final integrated tree remain required; this instrumentation
neither establishes a speedup nor replaces the native preservation/cursor gates.

## Separate proposed workflow coverage

No workflow is changed here. The existing `gui-readiness-phases.yml` five-process
hosted series is PR-path-filtered and has no manual dispatch trigger. A separately
reviewed extension could include `src/verdiclip/capture/scroll/**` and the specific
retention tests in its filters, because capture changes can affect first-capture
costs. Preserve the existing selector, pinned tools, Qt matrix, time limits,
per-trial source/runtime receipts and sticky failed aggregate. This does not
require user-desktop execution and should not be replaced by dummy file changes.
