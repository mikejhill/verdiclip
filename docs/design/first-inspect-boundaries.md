# Isolated first-INSPECT child boundaries

This diagnostic branch starts from source a0869f762f8fb47009a04c76ec3ab174817db6c1.
It does not repair a demonstrated COM defect. In PR109 job 127966072880 the parent
observed request dispatch at 4,632 ms, inspect_desktop at 7,248 ms, inspect_root at 7,444 ms
and timeout at 7,633 ms. Most of the 3-second operation budget was consumed before discovery
Open. Parent-observed markers cannot isolate serialization, scheduling or COM time.

One same-repository draft-branch workflow runs an owned WPF fixture on a disposable
hosted VM. It preserves existing calls, operation/startup/cleanup budgets, input
scope and strict ownership. No unchanged rerun, serializer warmup, fallback or
new provider query is added. Ordinary CI has no diagnostic environment flag.

The optional 14-label trace is armed once before first request delivery. It records
child-relative microseconds for read, parse, dispatch, admission and root acquisition
boundaries. Root-return means only ElementFromHandle returned; root-owned records
completion of the existing ownership callback, not nonnull root identity; root-wrapped is not identity validation or cleanup.
Existing later phase/release/DPI markers stay unchanged. Non-INSPECT first requests
stop the trace after parse and cannot qualify an INSPECT result. Failed first requests
retain their first observations. Subsequent operations cannot overwrite them.

Clock failure, backward time and overflow become unavailable evidence. Output is
fixed labels and bounded nonnegative integers or unavailable, never provider strings,
request data, handles or process IDs. Ordinary telemetry errors are nonfatal; controls
propagate. A timeout can prevent final child markers or cleanup: parent process
retirement is not proof of child RCW/DPI restoration. The 32-event parent phase tail
and stderr byte bound remain unchanged; the separate first-child receipt is at most 14
entries and 96 bytes per input line. Timing instrumentation overhead remains a limitation.

This is distinct from issue 89 protocol-only startup/request timeouts, WPF fixture
startup issue 85 and mutation issue 70. No physical input, cursor repair, release or general
performance qualification follows from a successful diagnostic.
