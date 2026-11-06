# Bounded scheduler transition diagnostics

This diagnostic is separate from scrolling implementation and lifecycle changes.
It adds anonymous counts after the existing scheduler validation and does not
change any state transition, phase interval, clock, loss check, completeness
check, exit status, or causal-claim restriction.

The outgoing-state histogram counts only switches away from the owned thread
inside its verified lifetime. Slots 0–255 preserve the reader's numeric byte;
slot 256 combines out-of-range inputs without exposing their values. This is a
raw numeric classification, not a semantic state mapping or signed conversion.
The timestamp-tie histogram counts groups by number of switch and ready events;
each dimension is 0, 1, 2, or 3-or-more. It does not infer ordering within a tie.
There are at most 257 state entries and 16 composition entries. Counts saturate
at 2^31−1 with an explicit saturation flag; zero entries are omitted. No raw
identifiers, timestamps, event records, provider text, or exception text is added.

Microsoft's [CSwitch documentation](https://learn.microsoft.com/en-us/windows/win32/etw/cswitch)
describes the previous-thread state and names values 0–7. Its field is signed;
the existing reader retains a byte. The documentation alone does not establish
which unsupported value occurred in the failed hosted traces, the order of
cross-CPU timestamp ties, or how ready events observed during an apparently
scheduled interval should be reconciled. These remain unknown. This change
collects the missing numeric classifications without changing that conclusion.

The failed profile run 42208757545 had zero event/buffer loss and a passing
independent field crosscheck, but incomplete phase states. Its private raw
traces were retired, so this addition cannot reconstruct that historical run.
All five cases in each Qt job executed and passed their latency assertions;
the aggregate diagnostic still failed. New diagnostics require independent
review and a separate publication plan, not an unchanged rerun or a relaxed
acceptance gate.

## Adjacent-transition diagnostic

The next additive diagnostic counts previous/next roles around outgoing state2
(separating self-switch from genuine outgoing) and a ready record whose incoming
state is scheduled according to the unchanged decoder. It never changes that
state machine. A timestamp-tied anchor reports its own ambiguous group on both
sides; no ordering within a tied group is invented. Untied anchors use the nearest
previous/next timestamp group, with tied neighbors reported as tied.

Each entry has fixed anchor/side/neighbor-role, same/different/unknown logging-CPU
relation, and a coarse QPC gap class: absent, same timestamp, at most1/10/100
microseconds, at most1millisecond, or longer. The756 possible bins saturate at
2^31−1. No raw CPU index, thread/process identifier, timestamp or event enters
public output. CPU encodings that differ are not compared. Missing CPU evidence
in older receipts is explicitly unknown. Logging CPU is not proof of the target
thread's executing CPU or of causal event ordering.

The private reader emits a CPU metadata row immediately before each owned switch
or ready row. It uses EVENT_HEADER_FLAG_PROCESSOR_INDEX to choose the documented
BufferContext.ProcessorIndex or ProcessorNumber union member, not ExtendedData.
See [Microsoft EVENT_HEADER specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-dtyp/fa4f7836-06ee-4ab6-8688-386a5a85f8c5)
and [ETW_BUFFER_CONTEXT](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/ns-evntrace-etw_buffer_context).
These rows remain only in the existing private CSV, subject to existing budgets
and cleanup; they are not uploaded or printed into the job log. Malformed or
unpaired metadata fails closed. The existing independent Xperf crosscheck checks
kind/identifier/state multisets only: it does NOT check timestamps, order or CPU.
The public receipt now states that limitation explicitly. Existing zero-loss,
clock, lifetime, unknown-state and completeness rules are unchanged.

The existing2,000,000-row and256MiB private-file limits are not increased.
CPU sidecar rows consume that same row allowance, so unusually large traces may
fail the conservative budget earlier. Failure remains explicit; no events are
silently dropped to fit. Portable C++ tests cover flag-dependent field selection
(including16-bit indices and legacy zero/255 values). Compilation against actual
Windows EVENT_RECORD/SDK headers remains a required hosted check.

## Separate switch-pair occupancy evidence

Microsoft's [CPU Analysis](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/cpu-analysis)
defines the switched-in interval using the next switch-out boundary. The additive
occupancy diagnostic independently matches target-thread switch pairs on the
same logging CPU and encoding. Every switch-out closes the pair, including an
outgoing Running state. No migration gap is filled and no ready/wait state is
inferred. This is an independent reconstruction algorithm, not independently
validated evidence: the existing Xperf multiset check still does not validate
CPU identity, timestamps, pairings or order.

Matched intervals overlap phase boundaries using integer QPC arithmetic. Only
regions covered by exactly one unambiguous pair and no exclusion span count as
verified. Overlapping CPU pairs are excluded where they overlap. Missing inputs
produce exclusion spans from lifetime birth; missing outputs extend to lifetime
end. Repeated inputs and tied switch groups invalidate affected pairs. Tied
records are handled as a group, never ordered arbitrarily. Self-switches close
and reopen distinct pairs at the same boundary. Any missing CPU or mixed CPU
encoding makes the lifetime's occupancy unverified. Readiness remains a separate
classification; ready records do not establish or close occupancy.

Public output contains only fixed per-phase aggregates (at most128 phases):
matched switch-pair duration, unverified remainder, clipped segment count and
coverage flag. Count saturation is explicit. The complement is NOT proven idle,
ready or waiting time. Matched occupancy is not exclusive application CPU time:
interrupts, DPCs and other execution on that processor are not subtracted.
Exact private pairs are temporary in memory and never persisted or logged.
Existing loss, timestamp, lifetime, phase, unknown-state, completeness and exit
checks remain unchanged; the new evidence cannot turn a failed analysis green.
