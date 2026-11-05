# Owned WPF fixture startup diagnosis

This is a diagnostic experiment, separate from native acceptance. It asks where
the existing owned WPF provider spends its startup interval on a disposable
`windows-2025` runner, with Qt 6.12.0 and scale 2. A green experiment does not
qualify native capture, repair latency, prove a cold OS, or permit a merge.

## Scope and trigger

`fixture-startup-diagnostic.yml` runs only on `pull_request` `opened` or
`reopened`, for a same-repository head named `diagnose/013-fixture-startup`
targeting `fix/013-capture-regressions`. It checks out the immutable PR head SHA.
There is no dispatch trigger, retry, trial matrix, fixture warmup, or acceptance
workflow change. A synchronize event does not automatically launch another
native interaction.

The event base must still be exactly
`5a5983275dfbd7e7243bd329ca3d859faca024c2`. An advanced base fails before setup
or the native run. The complete base-to-head delta is restricted to the eleven
reviewed diagnostic workflow, runner, documentation, fixture-sidecar, linter
compatibility and test files. The only actionlint configuration change copies
the existing exact `cache-mode` compatibility exception to this workflow's path;
it does not broaden the error pattern or suppress other workflow checks.
Product source, dependencies, native wrapper, reader, profile and scheduler
reducers must match the pinned repair base. Every bounded report identifies both
the executed immutable head and that repair base. The runner never substitutes
GitHub's generated integration commit for the executed source identity.

The runner invokes `test_capture_broker_native.ps1 -ScrollbarOnly
-ExecuteAfterHandoff` exactly once with one explicit Python interpreter. The
existing complete 20-second readiness window and 65/75/90-second case, wrapper,
and cleanup bounds remain unchanged. An additional wrapper watchdog does not
extend the native 90-second window. All input remains restricted by the existing
owned fixture and hosted native test gates.

## Recorder and ownership

Only already-installed Microsoft-signed WPR, Xperf, compiler, linker, compiler
locator, and command interpreter are used. Nothing is downloaded to add tracing
capability. Before the target starts, the existing `read_scheduler_etl.cpp` is
compiled into the private run directory. `gui_waits.wprp` is reused unchanged:
process/thread, context-switch, and ReadyThread events only.

The runner exclusively creates `RUNNER_TEMP/VerdiClip-<GUID>`. Every recording
uses that exact unique instance name. A private `runner-owner.json` records the
head SHA, directory, native baseline, exact wrapper PID/creation time, and trace
start attempt **before** WPR is started. A failed or interrupted start therefore
still requires retirement. Stop and cancellation always include `-instancename`;
global cancellation and adoption of an existing recording are forbidden.

Before creating that directory, the runner exclusively reserves the fixed
private file `RUNNER_TEMP/verdiclip-fixture-startup-locator.json`. Its bounded,
exact schema binds the random owner, head, repair base, private root, checkout,
temporary root, repository, run ID, attempt and job. Fallback derives this fixed
path and verifies the current context, every ancestor and the recorded root;
it never searches for similarly named directories. The owner and path are not
persisted in `GITHUB_ENV`, which Actions echoes into later step logs. Only fixed
numeric lifecycle flags and bounded native outcome fields cross that boundary.
A missing or partial locator after reservation is unknown, not successful cleanup.

An exclusive `.test-tmp` lock and a strict before/after snapshot identify the
single new `native-broker-<GUID>` directory created by this isolated invocation.
Existing directories are never claimed. More than one new directory is
ambiguous and blocks deletion and publication. Native-tree traversal never
follows reparse entries. Receipt reads skip child symbolic links, including
pytest's `*current` aliases. Cleanup validates the complete no-follow tree, then
unlinks only exact Windows `SymbolicLink` entries with nonrecursive deletion;
their targets are never read or modified. Ordinary files and directories are
deleted bottom-up without recursive deletion. A linked native root, junction,
unknown reparse type or changed entry still blocks cleanup and publication.
An interrupted wrapper can be terminated only using its recorded PID and exact
creation time. Missing/reused identity is unknown, not a reason to kill a process
with a similar name.

Fallback reads require the exact receipt schema, typed booleans and integers,
bounded values, legal state relationships, and non-overlapping owned directory
names. A string such as `"false"` cannot certify trace retirement. Failed cleanup
releases the lock handle but preserves its exact ownership token for recovery.

Optional instrumentation receives `VERDICLIP_FIXTURE_TRACE_DIR` only after the
recorder has started. The fixture writes `owner.json`, `exit.json`, and
`markers.csv` there. These associate its actual child process, nonce, startup
boundaries, owned STA dispatcher-thread QPC, and source identity. The PowerShell
runspace thread may be born after `Popen` returns. Instrumentation failures do
not replace the native result. Missing tools, compiler failures, and failed
trace admission still leave the one original native invocation enabled where
ownership is safe.

## Analysis and disclosure

The finalized trace is independently exported with Xperf, then
`python -m scripts.fixture_startup_summary READER PRIVATE_DIR` validates and aggregates it.
The Python analyzer writes only private `analysis.json`. Its strict QPC,
zero-loss, exact owned lifetime, process/thread identity, marker completeness,
and independent-decoder checks must all remain intact. Unknown scheduler state
stays unknown; scheduled time is not exclusive application CPU and is not a
causal explanation of a delay. Missing or partial markers are unavailable
evidence, never invented phase boundaries.

The PowerShell runner reads the bounded aggregate into memory, then requires:

1. Successful finalized stop or successful cancellation of the exact owned
   recording, including any uncertain start.
2. Verified deletion of its native output directory and all private trace,
   export, scheduler, marker, owner, analysis, compiler, and tool-output files.
3. Verified retirement of the owned lock and deletion of the private locator.

Only after all three does it print the bounded aggregate. The locator remains
available if the main step is interrupted between raw-tree and lock cleanup;
it is deleted last, before any aggregate is printed. Raw ETL, CSV, process
identifiers, handles, nonces, absolute clocks, fixture receipts, native logs, and
tool errors are never printed or uploaded. No artifact-upload step exists.
Failure to prove retirement or cleanup withholds analysis. An `always()` step
uses only the recorded ownership to attempt cleanup after interruption; it does
not rerun the target or publish a pending aggregate. Disposal of a hosted VM is
containment, not proof of successful cleanup.

Failure reporting uses a closed stage/reason vocabulary, such as `native_tree`,
`trace_retirement`, `raw_removal` or `locator_cleanup`. It never includes the
original exception, path, process identity or owner. Evidence failures retain
their failure stage rather than being mislabeled by a later successful cleanup.

The original wrapper exit code is retained separately and reproduced in an
`always()` native-result step, alongside fixed forced/cleanup classifications.
A trace failure cannot turn a native failure into success. Instrumented success
remains diagnostic non-reproduction, not an acceptance result.

## Verification

Portable source/contract tests cover branch and trigger guards, exact head SHA,
one unchanged native invocation, ownership-before-start, named-only retirement,
private output and cleanup-before-publication. PowerShell helper functions can
be dot-sourced with `-FunctionsOnly` for isolated mocks without invoking tools,
creating a GUI, or starting a recording. Linux validation cannot certify WPR,
Windows tool signatures, native focus/input, actual trace decoding, or hosted
cleanup. Those remain explicit limits until the authorized single Windows job
has run against its exact final head.

Actual Windows PowerShell helper and runner-mock tests run only after the single
native invocation and its cleanup, so the tests cannot prewarm the Windows
PowerShell fixture process. Their outcome is separate from the native outcome.
Additional real Windows probes cover pytest-style `*current` aliases, a foreign
symlink target protected by a sentinel, linked-root and junction refusal, and
the private locator's ownership and cleanup gates.

## First run and correction

The first diagnostic head, `a55fb313ed4ad0e0ffa7d8e8ee9277645ecb991d`, ran in
[run 42077662538, job 127422065892](https://github.com/mikejhill/verdiclip/actions/runs/42077662538/job/127422065892).
The native wrapper exited 0 without forced termination, and 105 actual Windows
probes passed. The main diagnostic and fallback cleanup steps failed. Trace
retirement was recorded, raw cleanup was not proved, and no aggregate was
published. That run remains a failed diagnostic after this correction.

The original source rejected every reparse entry while reading the whole native
tree. Pytest creates `*current` symbolic-link aliases in its base directory, so
that path can throw before reading the native verdict and set the sticky
ownership-uncertain flag that blocks cleanup. This is a source-proven mechanism
matching the observed classification, not recovery of the private historical
exception or proof that it was the sole cause. The correction narrows link
handling without weakening owned-root, junction, trace-retirement or publication
checks. It also replaces the echoed ownership environment value with the private
locator. No later run's result is implied by these changes.
