# Windows qualification requirements and parity

Define capability requirements before writing parity tests. The discovery probe
is an inventory, not a native test or migration acceptance. No local interactive
execution or native enablement is authorized by a positive inventory.

## Coverage classes

- **B:** required common native baseline, plus existing portable contracts.
- **M:** required additional profile for full native multi-monitor coverage.
- **S:** supplemental physical-device or human assessment.

A runner can qualify B without qualifying M. That is a bounded subset, not full
replacement of product coverage. Missing capabilities are unsupported or unknown,
never silently skipped into a green full-coverage result.

## Capability and coverage matrix

| ID | Class | Required capability | Local | Hosted |
| --- | --- | --- | --- | --- |
| A1 | B | Active input desktop and usable foreground | Held | Discovery |
| I1 | B | Real hook, matched pairs and injected input | Held | Unverified |
| C1 | B | Native capture of generated fixture content | Held | Unverified |
| U1 | B | Real owned UIA ScrollPattern provider | Held | Unverified |
| H1 | B | Real hotkey registration and chord routing | Held | Unverified |
| Q1 | B | Qt 6.11.2 and 6.12.0, exact versions recorded | Held | Unverified |
| L1 | B | Failure cleanup and retained documents | Held | Unverified |
| R1 | B | Identical test/configuration provenance | Planned | Planned |
| T1 | B | Synthetic topology and coordinate oracles | Existing | Existing |
| M1 | M | At least two OS-enumerated monitors | Held | Discovery |
| M2 | M | Negative origins and cross-monitor boundaries | Held | Discovery |
| M3 | M | Mixed scaling, including fractional scaling | Held | Discovery |
| P1 | S | Physical raw-device input | Held | Unsupported |
| P2 | S | Hotplug, HDR/GPU and Narrator usability | Held | Unsupported |

"Held" means no new local inventory or test has run. Historical laptop evidence
does not establish the current state. "Discovery" means a read-only query can
describe available resources, without qualifying application behavior. Existing
T1 evidence is repository-defined simulation, not native display equivalence.

## Acceptance criteria

### A1: desktop and focus

Require an active, unlocked session, a visible process window station and
readable input desktop. Record the session state and caller context. After
separate execution authorization, an owned canary must establish actual exposure,
foreground admission and rendering. WTS success alone is insufficient. A lost
foreground, unknown session or cleanup failure fails qualification.

### I1, C1, U1 and H1: real boundaries

- Exercise the production broker with an explicit fixture-scoped driver. Check
  real input-pair receipts, cancellation, repeated acquisition and zero Hide/Close
  of the preserved fixtures. The existing broker harness generates no input.
- Use generated, identifiable content for actual capture. Assert physical ROI,
  row order, no missing/duplicated content, and the declared pixel oracle. Keep
  acquired frames in RAM except expressly approved synthetic output files.
- Use a separate owned native UIA provider, with stable identity and verified
  vertical ScrollPattern. Exercise discovery, explicit consent, movement,
  settling, cancellation, partial review, selected delivery and restoration.
  A JSON peer or replay provider cannot satisfy U1.
- Register a temporary test chord through the production hotkey service. Verify
  OS registration, exactly-once action dispatch, conflict handling, unregister
  and reacquisition. A posted WM_HOTKEY is not chord-registration evidence.
- The synthetic delivery lane must retain real save/retry/error and passive
  presentation behavior. Separate real-input/RAM and synthetic/real-output lanes
  do not constitute one capture-to-output end-to-end test.

### M1-M3: full native monitor profile

Require at least two independently OS-enumerated displays, a negative virtual
origin case, cross-boundary selection/window transfer, and unequal effective
scales including a fractional scale such as 150%. Record per-monitor bounds,
work areas and successful scale queries, plus the actual capture process's DPI
awareness and Qt/Win32 mapping. Test negative X and negative Y scenarios where
the environment provides them; declare missing scenarios explicitly. Counts
alone are insufficient: mirrored or invisible pseudo-monitors do not establish
independent usable capture surfaces. Cross-check Qt screens and native capture.

Synthetic negative coordinates, display removal/reordering and 100-250% scale
contracts remain required algorithm evidence. They do not prove OS-backed
monitor enumeration, native capture seams or mixed-DPI rounding. One monitor,
resizing windows, system DPI alone or a fake screen list cannot satisfy M.

Do not install virtual display drivers, alter monitor layout or scaling, or
change system/security settings under this plan. If the standard hosted image
lacks M, retain B as a subset and seek a separately approved suitable environment
before claiming full native multi-monitor replacement.

### Q1, L1 and R1: versions, ownership and reproducibility

Use both supported Qt profiles and record exact Python patch, Qt, dependency,
OS and runner-image versions. Run one input/focus case at a time per desktop.
Bound the case and job, and independently prove worker/helper exit before fixture
teardown. Failures must preserve documents, edits and retryable accepted results.
Use only nonce/PID/creation-time-verified fixture owners. Release only owned input
pairs, hotkeys, handles, processes and temporary files.

The original hosted helper-timeout cause remains unproven. Preserve incident and
corrected-run evidence; neither a rerun nor a capability probe diagnoses it.

## Identical-test parity protocol

Before constructing the suite, approve B and M requirements and evidence limits.
Then run the same suite on the local isolated preview and the hosted runner:

1. Match the exact Git commit, clean source, lockfile and dependency profile,
   Python/Qt versions, fixture and driver hashes, seed, input profile, timeouts,
   application flags, output policy and expected-oracle version.
2. Select the same named capability profile and test IDs. Inventory the actual
   environment separately. Fixture placement may follow the same documented
   work-area algorithm; it must not hide missing displays or scaling profiles.
3. Compare pass/fail, supported/unsupported status, event order/cardinality,
   document/history identity, close/retry outcomes, ownership and cleanup,
   target identity, scroll positions, completeness and image-content oracles.
4. Compare normalized logical geometry with explicit per-host physical mapping.
   Raw pixel hashes across different DPI/compositor/font environments are not
   automatically equivalent. Define tolerances and rendering assumptions before
   execution; never relax them after observing failures.
5. Include negative cases: lost foreground, invalid session/provider, stale
   window generation, lease/helper failure, duplicate/late callbacks, save
   failure/retry, cancellation and unsupported topology. Use deterministic fault
   adapters for system transitions that would otherwise require changing the
   desktop. Label these as simulation, not actual lock/hotplug evidence.
6. Require every mandatory case in a claimed profile to pass on both targets.
   A skip, missing capability, unexplained mismatch or flake leaves that profile
   unqualified. Keep unmatched cases and original failures in the report.

Windows Server hosted results and Windows client results are different OS
evidence, even when shared API contracts agree. Injected input is not physical
raw input; provider fixtures do not certify arbitrary third-party applications;
programmatic accessibility does not certify Narrator usability or balloon
appearance. Mike's completed-build local acceptance remains separate.

## Discovery and smallest next steps

The hosted read-only probe records session/station/input-desktop state, virtual
bounds, monitor rectangles/work areas, scale-query results and caller awareness.
It creates no window or input and performs no capture, UIA or hotkey operation.
It does not change DPI awareness or display settings. API failure fallback scale
values are not reported as measurements. No device names, user names, titles,
pixels or credentials are published.

A minimal future local read-only inventory would use the same query script and
schema, recording OS/Qt versions and the same topology/scaling fields. Local
execution is currently held; the current script refuses non-hosted invocation.
Any local mode requires separate authorization and source review first. Do not
interact with Mike's desktop to obtain inventory.

The present job uses standard public Windows x64 compute, a two-minute cap and
logs/job summary only. It invokes no native tests, artifact uploads or new cache.
Normal PR/push/merge CI remains unchanged; explicit capability-only dispatch
skips its upload/cache-bearing jobs. The draft discovery PR uses a skip marker
to avoid automatic pipelines. It is not merge qualification.

After reviewing the actual discovery result, separately authorize an owned
canary and the native baseline suite. If M is absent, report that limitation
before selecting an alternative. No provisioning, paid runner, virtual display,
RDP, autologon or security change is authorized. Native enablement, merge,
release, installed replacement and local-acceptance holds remain.

## Bounded hosted owned-window canary

Discovery run [40567676713][discovery] at commit
`66fff1cace2e1f161bb133befeb92ab4a06a55c4` observed an active, unlocked
desktop with one 1024 by 768 monitor, origin (0,0), 100% scale. This is an
observation of image `win25-vs2026 / 20261021.278.1`, not a runner guarantee.
Its zero-error inventory does not qualify B or M.

The separately authorized `owned_window_canary=true` dispatch still skips
normal CI and uploads. It first repeats discovery on the actual job VM, then
creates one 120 by 90 topmost, nonactivating synthetic Win32 popup. The popup
paints two successive, distinct three-color patterns. Each of six sampled
screen-pixel coordinates must belong to that window immediately before and
after each read, and match their expected colors. These separate calls are not
an atomic ownership guarantee against a new topmost surface. Pixel values are
never published. Showing the window uses nonactivating SetWindowPos, avoiding
ShowWindow's first-call dependence on the child process's hidden startup mode.

The original foreground must remain valid and unchanged throughout admission
checks and at completion. A matching foreground handle is admitted; the owned
nonforeground handle is rejected. This is a small harness predicate, not the
production broker. Positive admission of an owned foreground window remains
unverified under A1: the positive identity belongs to the borrowed preexisting
foreground. No foreground activation is requested. No input is generated,
no hotkey is registered and no UIA or Qt application is launched.

Pass requires both patterns, all six samples, successful window destruction,
class unregistration and GDI/DC release. The paint/message loop has an eight
second deadline. A separate process watchdog waits at most thirty seconds,
then terminates only its owned child tree and waits at most five seconds for
exit. A watchdog timeout is a failed capability result, not a cleanup pass.
The workflow retains its two-minute ceiling and logs/summary-only receipt.

Compilation and static review may run locally without invoking native methods;
the execution script refuses non-hosted invocation. This canary does not prove
input hooks, physical input, real UIA scrolling, application delivery, tabs,
hotkeys, Qt rendering, multiple monitors or parity. Those remain separate gates.

## Multi-monitor options: evidence and permissions

No no-spend, configuration-free hosted M environment is established by current
evidence. Keep portable topology tests running, but do not substitute them for M.

| Option | Evidence | Missing proof / permission |
| --- | --- | --- |
| Current standard VM | One monitor observed | M absent on this image |
| Another standard image | Windows labels documented | M unverified |
| Larger / client / GPU VM | Images documented | M unverified; setup and cost |
| Existing self-hosted M | Custom hardware supported | Availability and access |
| External desktop service | No provider qualified | Topology, access and cost |
| Driver / layout | Windows APIs documented | Driver or settings change |

Standard public compute is free, but its specifications give no monitor-count
or scaling guarantee. Another unchanged image would need its own inventory;
multiple displays or unequal scales are conjecture until demonstrated.
Larger runners are billed even for public repositories and require explicit
setup and cost permission. No suitable existing self-hosted runner is established
here; owner approval, runner access/security review, isolation and maintenance
remain necessary. An external service must establish OS displays rather than
browser viewports; access, costs and data handling need separate approval.
Driver installation and display changes are outside authorization, not attempted.

GitHub's larger-runner reference lists Windows 11 and GPU images, but neither
GPU capacity nor Windows client branding proves multiple independent displays.
Self-hosting can support a prescribed existing physical/virtual environment;
that is infrastructure and access work, not permission granted by this probe.

Microsoft distinguishes visible displays counted by `SM_CMONITORS` from
`EnumDisplayMonitors` enumeration, which can include mirroring pseudo-monitors.
M must demonstrate independently usable OS display surfaces, not merely a
larger count, two windows, a resized viewport or two synthetic rectangles.
Both negative origins and mixed fractional scaling remain explicit requirements.

Recommended sequence: review the actual canary receipt, then separately qualify
the bounded native B suite on standard hosted Windows. In parallel, obtain
documented evidence for a pre-existing M environment before requesting access
or cost approval. Construct parity cases only from the preceding requirements;
run identical approved fixtures/configuration on both targets when authorized.
No local inventory, driver, display change or provisioning is part of this step.

## Display-package signature preflight

VerdiClip requires **no driver installation**. The product uses existing OS APIs;
this virtual-display candidate is optional disposable CI infrastructure only.
It is not a product dependency, distributed component or user-machine prerequisite.
No driver may be installed on Mike's computer. Current authorization permits
read-only inspection only; installation anywhere remains unauthorized.

The separately authorized `driver_signature_preflight=true` dispatch inspects
only the pinned VirtualDrivers 25.7.23 display-only archive. SHA256 must equal
`e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a`
before confined, size-bounded extraction. Downloaded files are data: no package
script, installer, utility, DLL or driver is executed or installed.

The standard disposable Windows VM uses its existing, validly Microsoft-signed
x64 SDK SignTool. It verifies catalog signatures and INF/DLL/SYS membership
under Authenticode and kernel policy. The actual OS version is recorded as context;
these calls do not claim explicit OS-version-targeted verification.
Each verifier process has a fifteen-second deadline and owned-process cleanup;
the whole job is capped at three minutes. A missing verifier, hash mismatch or
uninspectable package fails inspection; no new verifier is installed. Failed
signature policies remain rejected results even if inspection itself completes.
Command-line errors are tool errors, not signature rejections; they and timeouts
leave verification incomplete. The first run exposed incompatible `/o` arguments
and supplies no signature acceptance or rejection evidence. Pure regression checks
cover the corrected vectors and diagnostic classification without invoking tools.

Receipts include package/file hashes, PE architecture and file versions, INF
target/framework declarations, existing OS UMDF files and raw verifier outcomes.
These establish static declarations and trust-policy results on that image,
not runtime driver compatibility, successful loading, a usable display or M.
The script imports no certificates, modifies no catalog database or trust
settings, and performs no reboot or display change. It never invokes the
publisher's installer, including its TrustedPublisher-import behavior.
Results remain logs/summary only, with all normal artifact/cache jobs skipped.
An installation proposal must wait for the precise result and any tool gaps;
installation and readiness are not approved by this preflight.

## Sources and existing evidence

- [Preview CI contract][preview-ci] separates simulation and native evidence.
- [GitHub runner specifications][runners] list standard Windows x64 images.
- [GitHub billing][billing] separates public compute and storage allowances.
- [Scale queries][scale] must check HRESULT; failure still supplies a fallback.
- [DPI query context][dpi] changes results; system DPI is not per-monitor proof.
- [SendInput][input] has integrity and existing-key-state constraints.
- [Larger runners][larger] document compute and images, not M capabilities.
- [Larger-runner billing][larger-billing] is separate from free standard compute.
- [Self-hosted runners][self-hosted] permit custom hardware with owner maintenance.
- [Indirect display drivers][idd] support virtual monitors through drivers.
- [SetDisplayConfig][display-config] changes topology and display modes.
- [EnumDisplayMonitors][enum-monitors] can include mirroring pseudo-monitors.

[preview-ci]: https://github.com/mikejhill/verdiclip/blob/16fa747857318faa105e7e23b6252f3b94429863/docs/design/editor-tabs-ci-contract.md
[runners]: https://docs.github.com/en/actions/reference/runners/github-hosted-runners
[billing]: https://docs.github.com/en/billing/concepts/product-billing/github-actions
[scale]: https://learn.microsoft.com/en-us/windows/win32/api/shellscalingapi/nf-shellscalingapi-getscalefactorformonitor
[dpi]: https://learn.microsoft.com/en-us/windows/win32/api/shellscalingapi/nf-shellscalingapi-getdpiformonitor
[input]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
[discovery]: https://github.com/mikejhill/verdiclip/actions/runs/40567676713
[larger]: https://docs.github.com/en/actions/reference/runners/larger-runners
[larger-billing]: https://docs.github.com/en/billing/concepts/product-billing/github-actions
[self-hosted]: https://docs.github.com/en/actions/concepts/runners/self-hosted-runners
[idd]: https://learn.microsoft.com/en-us/windows-hardware/drivers/display/indirect-display-driver-model-overview
[display-config]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setdisplayconfig
[enum-monitors]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaymonitors
