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
