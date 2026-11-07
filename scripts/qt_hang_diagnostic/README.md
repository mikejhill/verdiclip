> Historical CDB contract below. The active workflow now uses the separate
> [Python-only target contract](target-contract.md), with no debugger initialization.

# Isolated Windows Qt hang diagnosis

Base: `ecb4a4cf675b701259ced8b18c1f5f4c1a32cdfa`. This branch only diagnoses the
first Python 3.13.17 / Qt 6.11.3 timeout at seed 2197208277. It does not change
product code or retry the original CI job. Only a push to
`diagnostic/qt6113-owned-stacks` triggers the new workflow; existing workflows
have no matching non-main push trigger. Do not open a PR to run this experiment.

Before any test execution, fixed Windows SDK x64 paths are checked for a valid
Microsoft Corporation Authenticode signature, AMD64 PE architecture, version and
SHA256. Missing debugger means a recorded skip, with no test launch. Invalid
signature or changed binary fails closed. No installation, elevated permissions,
download, debugger fallback, Qt method patch or alternate attach mode is used.

One archived 157-node prefix preserves seed, order, coverage and the original
Windows platform selection. A child wrapper adds pytest progress/identity files
and the requested five-second Python faulthandler timeout. It does not inspect
Qt objects. All archived product/test sources plus configuration are hashed.
The wrapper and debugger are explicit diagnostic interventions, not proof that
the original run was unmodified.

At eight seconds from before launch, if unfinished, the actual Python PID from
the session-bound receipt is pinned and checked against the owned job and kernel
image. Noninvasive CDB receives only `~* kn 40; qd`: numbered native frames, at
most 64 frames per thread, then detach. No arguments, locals, memory or environment
dumps are requested. Local empty symbol path, ignored symbol environment and
`-netsyms:no` prohibit network symbol fetching. Raw addresses/module offsets can
remain unresolved. Stack capture can perturb the process and is recorded.

The original workload deadline is 15 seconds, including collection. A slow
collection may time out before reaching the final test; this is reported, never
fixed by extending the budget. CDB has a separate three-second deadline capped
by the workload deadline. No timeout or forced retirement counts as a passing
reproduction. Existing native process-creation primitives are reused; a genuinely
stuck kernel process-creation call is not made interruptible by Python polling.
Both owned trees are terminated before shared zero-wait retirement polling. A
separate shared three-second cleanup-only ceiling, capped by the original
launch timestamp plus 18 seconds, confirms empty jobs and closes every
handle; it never extends test or debugger admission and never resets per owner.
Cleanup uncertainty fails and cannot preserve a successful verdict. These
diagnostic-specific ceilings do not change the failed CI or capture deadlines.
The workflow has a separate two-minute step and five-minute job ceiling.

Official command references:
- https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/cdb-command-line-options
- https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/qd--quit-and-detach-
- https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/k--kb--kc--kd--kp--kp--kv--display-stack-backtrace-
