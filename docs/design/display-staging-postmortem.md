# Stopped staging experiment and correction design

## Observed result

[Run 40621338936][run] used reviewed source `4b90d55` on one standard
`windows-2025` GitHub-hosted runner. The script stopped after 11.561 seconds;
the job completed with failure at 02:30:59 UTC on October 28, 2026.

The required preflight gates and before/immediate-before inventories completed.
The single add child started. Its polling loop observed a visible-window or
foreground fingerprint difference and raised `Visible window/foreground changed;
possible prompt.` This establishes a changed observation, not a prompt's identity,
signature-policy rejection, successful admission, or driver loading.

All seven recorded owned child processes exited. The receipt contains no accepted
stage result, exact package identity, removal operation or final snapshot.
`partial_operation_uncertainty=true`, `cleanup_verified=false` and `passed=false`
remain authoritative. No retry or runtime experiment is authorized.

## Why the final inventory is absent

The same `Invoke-StagingProcess` window-baseline guard protects every child,
including the cleanup package enumeration. That enumeration also encountered a
fingerprint difference and stopped. One outer cleanup `try` surrounds enumeration,
ownership/export checks, deletion and the final snapshot. Its catch therefore
skipped all later collection, including the independent final snapshot.

The evidence does not distinguish a persistent changed window from two separate
changes. It retains neither before/after window rows nor failed-child output tails.
No retrospective code analysis can reconstruct those missing observations.

## Runner containment evidence

The jobs API identifies runner `GitHub Actions 1000010937`, group `GitHub Actions`,
label `windows-2025`, job `122595849685`. Setup, checkout cleanup and the Complete
job step succeeded. The job ran from 02:30:33 to 02:30:59 UTC. All twelve other
jobs were skipped, and the artifacts API returned zero artifacts.

GitHub documents a [new VM per standard Windows job][reference] and [automatic
decommissioning after a hosted job finishes][lifecycle]. This supports disposable
runner containment. The retained job metadata is not a per-VM destruction or
disk-erasure receipt. Orphan-process cleanup and decommissioning are not proof
of Driver Store removal, unchanged trust, unchanged devices or unchanged displays.
No self-hosted-runner API absence is used as evidence of VM deletion.

## Proposed correction, not wired into execution

The pure prototype in `scripts/display_staging_diagnostics.ps1` has no executor,
native API, driver operation or workflow import. Its fake-state checks are in
`scripts/test_display_staging_diagnostics.ps1`. It prepares this future design:

1. Latch every admission/guard failure, including earlier ownership, signature or
   deadline failures. Pass that persistent history as `AdmissionEverFailed` and
   `GuardEverTripped`; omitted history defaults to failure. A later return to the original fingerprint
   must not clear it or authorize another add. An interrupted staging result must
   remain uncertain even if subsequent observations appear equal.
2. Preserve bounded window witnesses: before/after foreground handles, added and
   removed handles/PIDs, hashed class names and explicit truncation. Retain no
   titles, command lines, screenshots or unbounded inventory. A witness is not
   proof of prompt identity or absence of transient activity between polls.
3. Capture failed-child start/exit/kill/drain states and bounded stdout/stderr tails
   in the exception path, without waiting beyond the operation/cleanup deadline.
   Missing output stays missing; no marker or zero exit may override guard failure.
4. Separate independently bounded read-only postmortem collection from admission
   and deletion. A changed foreground must not suppress safe package enumeration
   or final snapshots. A failed package query must not skip the final snapshot.
   Unexpected UI from the observation worker itself still stops that worker.
5. Keep privileged removal gated by confirmed stage outcome, no latched guard
   event, exact baseline/export ownership and stable device/trust/display state.
   Missing evidence never permits force, uninstall, prompt acceptance or reboot.
   Read-only collection does not grant permission to remove anything. A plan never
   establishes cleanup success, even if it permits an attempt. Actual removal
   and final inventories require independent evidence; a latched failure forbids
   a cleanup-success claim. Report observed facts separately from that claim.
6. Recompute remaining budget before each action. Preserve the five-minute job,
   180-second normal phase, 90-second cleanup reserve and owned-process deadlines.
   Collection can fail independently; report each failure without claiming cleanup.

Before any future execution, wire the structured witness into the native reader,
split cleanup observation blocks, qualify executor modes and failure paths, and
obtain independent review plus new explicit experiment approval. The current
prototype cannot recover this VM's missing state and does not change the consumed
authorization, historical receipt, pinned package, trust policy or native gates.

[run]: https://github.com/mikejhill/verdiclip/actions/runs/40621338936
[reference]: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
[lifecycle]: https://docs.github.com/actions/using-github-hosted-runners/using-github-hosted-runners
