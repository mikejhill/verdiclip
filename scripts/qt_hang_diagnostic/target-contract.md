# Separate target-aware Python-only diagnostic

This replaces the active workflow command, not the historical CDB experiments.
There is no CDB preflight, debugger process, native-stack capture, or dynamic
fallback. Native stacks are unavailable and debugger intervention is false.
The prior two attempts and their artifacts remain valid historical failures.
CDB Extension Gallery startup controls could not be verified from supported
per-invocation documentation; symbol-server blocking alone is insufficient.
No installation or download was demonstrated by those earlier logs.

The source remains the exact 301-file manifest at ecb4a4c, with the same fixed
157-node order, seed 2197208277, Python 3.13.17 and locked Qt 6.11.3. Git archive
explicitly disables autocrlf; hashes are neither normalized nor relaxed. The
child keeps coverage and its threshold unchanged, and uses Python faulthandler
at five seconds. Hooks record test phases only, without wrapping Qt methods.

One owned process runs once. Launch begins a fixed 30-second preparation cap
for the first 156 nodes. This separate diagnostic budget reflects the original
CI's approximately 23.1 seconds from test command start to target entry; it is
not a change to any product or earlier diagnostic deadline. Every prefix node
must complete setup, call and teardown successfully in the exact order.

The last node's logstart atomically publishes a write-once, session- and
source-bound receipt containing the ordered phase evidence and same-machine
Windows monotonic timestamp. That original timestamp starts one 15-second
window covering target setup/call/teardown and process/coverage exit. It must
precede launch+30 seconds. Reads, progress, and failures never reset the clock.
The supervisor rejects altered receipts, future/regressed timestamps, missing
phases, reordered nodes and late entry. There is no retry.

Cleanup has one shared three-second confirmation allowance, capped absolutely
at launch+48 seconds. Only the owned tree is terminated. Cleanup uncertainty
cannot preserve a successful verdict. Target completion means non-reproduction
in this one experiment, never a causal explanation. A partial-suite coverage
exit 1 is retained separately, remains a failing process/workflow exit, and
cannot be reported as a target hang or silently converted to success.

`phases.jsonl` is an append-only, flushed event journal. Each start and phase
report carries the session epoch, source-manifest digest, sequence and monotonic
timestamp. The fixed limits are 628 records, 4,096 bytes per record and
2,572,288 bytes total. A malformed outcome, order violation, failed phase or
journal write stops the ledger permanently. Earlier records are never replaced;
`phases.json` remains only a convenience snapshot. A partial last journal line
after abrupt termination is incomplete evidence, never a completed phase.

Collection is explicitly reduced to these 157 nodes, not the original full
3,691-test collection. Imports and collection hooks from omitted modules may
change the process context. Matching selected source bytes and execution order
does not recreate that broader context. The receipt labels this scope as
`explicit-prefix-not-full-suite`. A passing target is non-reproduction only,
not evidence that the original full-suite hang is resolved.
