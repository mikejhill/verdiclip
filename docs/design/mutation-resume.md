# Incremental mutation qualification

PR mutation reports reuse compatible verdicts; they are not a fresh safety score.
The scheduled and manually dispatched runs still start without a verdict cache,
cover the whole package, and enforce the existing 72% floor. Test execution,
assertions, forkserver isolation, timeout policy and the 150-minute job limit are
unchanged.

## Why the previous runs timed out

Runs 40277302711 and 40293180198 reached GitHub's 150-minute limit. Both retained
partial caches; neither produced a completed score. The second restored the
first's cache but passed explicit module patterns to `mutmut run`. In
[mutmut 3.8's runner](https://github.com/boxed/mutmut/blob/3.8.0/src/mutmut/__main__.py),
explicit mutant names deliberately rerun known verdicts. The displayed counter
counts stored verdicts, so a nearly stationary counter did not measure actual
worker executions.

## Supported resume behavior

`scripts/mutation_ci.py` translates the existing PR module selection to the public
`tool.mutmut.only_mutate` file filter. The workflow calls `mutmut run` without
explicit names. This preserves valid function verdicts and retests changed or new
functions. The translated selection was checked against all 59 changed source
files in the combined preview: both selections name the same files.

Before mutation, CI runs the full portable baseline. It also removes stale copied
Python sources, tests and stats while retaining verdict metadata. Mutmut therefore
collects current test statistics and runs its original-test and forced-failure
checks before using cached verdicts. The temporary configuration changes only
`only_mutate`; restoration is byte-identical and refuses to overwrite subsequent
edits. Available verdicts and explicit completion status are uploaded even on
failure. A partial or timed-out run never becomes a successful score.

The v2 cache key includes the runner and migration descriptor. A one-time v1
fallback is allowed only when all five approved dependency, configuration,
support, workflow and runner inputs match their reviewed normalized SHA-256
values. A changed input disables that fallback. Cached verdicts can predate new
test assertions; fresh whole-package runs and targeted fault replays remain
necessary evidence.

## Independent executable qualification

An isolated Linux Python 3.13.16 / locked mutmut 3.8.0 fixture used two assertion
tests, one selected source file and an unselected file. Four consecutive runs
observed these actual mutant-worker exit receipts:

| Phase | Tool exit | Mutant workers | Result |
| --- | ---: | ---: | --- |
| Initial run | 0 | 2 | Only selected-file metadata generated |
| Unchanged cached run | 0 | 0 | Known verdicts reused |
| Changed source, same assertions | 0 | 2 | Changed function retested |
| Failing original assertion | 1 | 0 | Fresh stats collection rejected baseline before mutation |

The last phase retained two cached verdicts and still failed closed. Earlier
fixture timeouts were retained separately and were not counted as qualification.
These are runner contracts, not product, physical desktop or native-input proof.
Eleven repository unit tests additionally cover scope validation, exact restoration,
concurrent-edit refusal and migration fingerprint invalidation. Hosted exact-head
CI and mutation completion remain the final workflow checks.

## Completed combined run and survivor follow-up

Combined run 40317329447 at `bf5570a1` completed successfully in 52m45s. It
restored the preserved v1 cache from run 40293180198, completed public file
selection and saved the v2 cache. Configuration restoration and result collection
passed. The report covers 59 selected source files: 8,929 of 12,106 checked
mutants detected (73.8%: 8,904 killed, 18 segfaults, seven timeouts), with 3,177
survivors. This is completed incremental evidence, not a fresh whole-package
score or a claim that the survivors are equivalent.

The separate capture-head run 40305362810 reported 73.4% (5,129/6,992 detected,
1,863 survivors). Its selected assessment exposed an IPC payload-preservation
gap: replacing decoded launch arguments with `str(None)` survived. The new
`tests/shell/test_instance_payload.py` exercises production decoding and its
public signal in-process without connecting a socket or opening a pipe. It
checks Unicode, content, order, duplicates, quoted and empty arguments, plus a
fileless launch. The existing subprocess lifetime regression remains separate.

Both supported Qt versions pass these two tests. An isolated fresh mutmut 3.8
run with unchanged copied source, covered-line selection and forkserver
isolation killed the actual generated `str(None)` mutant; the run completed
without timeout in 78.73s. This qualifies the specific regression. Adding the
tests does not retroactively refresh the completed run's cached verdicts or
classify its remaining survivors. Capture's four semantically unmapped service
mutations and presentation-path coverage gaps remain explicit limits.

The next test-only follow-up also checks multiple accepted reviews after their
panels are destroyed: unknown, duplicate and matching acknowledgements can
release only the matching original, preserving the other session, canonical
object and pixels. The original code passes on both Qt versions; the exact
selected `or` to `and` ID-guard fault fails the ownership assertion. Strict
file-picker fakes additionally check the four-argument Qt contract, original
title/timestamp, saved receipt, automatic and fallback paths, and cancellation
retention. A missing-directory argument fault fails all three path cases.
These are targeted test improvements, not production defects or a revised
aggregate score. Independent assessment reviewed 449 selected survivors; 126
semantic mappings remain unqualified, and the broader survivor set is not
classified equivalent.
