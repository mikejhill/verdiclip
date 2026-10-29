# Disposable display runtime preparation

This is an unactivated controller model and a compile-only native lease binding.
There is no native port implementation, process supervisor, runtime entry point
or driver-runtime workflow. It is not ready for an experiment dispatch.
VerdiClip itself has no driver dependency.

## Implemented boundaries

- Require a public, manual, first-attempt, standard hosted Windows x64 context.
- Require an exact reviewed commit identity, no secret references, OIDC,
  credential persistence, untrusted code or privileged downstream consumer.
- Require `cache-mode: none`, enforced through GitHub's scoped cache tokens.
- Atomically claim one attempt, including concurrent callers.
- Record mutation intent before a call that can write then throw.
- Bound normal work at 150 seconds; reserve device cleanup through 210 seconds,
  package cleanup through 240 seconds and independent trust rollback through
  270 seconds. The proposed outer job cap is five minutes.
- Always signal known-handle close, including after a late operation return.
- Verify fresh exact device identity, generation, package hashes, absence from
  baseline and descendant ownership before authorizing exact node removal.
- Never authorize package deletion while device removal is unverified.
- Attempt publisher removal independently of device/package failure; observe
  absence independently of a throwing removal transport.
- Preserve errors and separate each cleanup verdict from the final pass.

The injected port contract requires baseline inventory, exact exported package
verification, owned configuration, device health/target correlation, exact node
cleanup, package rollback, publisher rollback and final baseline comparison.
No default implementation can perform those operations yet.

## Validation and source identity

`../test_runtime_probe.ps1` compiles declarations and runs managed fake tests only.
It never constructs the native binding or invokes Windows device/display/trust
APIs. The PR-only CI job uses read-only checkout permissions, no persisted Git
credentials, no caches and no artifacts. No runtime experiment is dispatched.

`source-manifest.json` pins the four C# source/test files. The pure checker
rejects changed bytes, malformed entries, direct reparse files and unequal commit
identities. A matching mutable manifest alone is not an independent trust anchor.
The host must obtain the reviewed commit from the specifically approved bundle,
verify the actual checkout and all executable support scripts, then load source.
The synthetic identity in the self-test does not approve runtime execution.

The independent source review identified and corrected late close suppression,
dependent publisher verification, concurrent admission and empty failed-linter
results. Managed tests cover those failures. They do not establish native
marshalling, real callback formatting, driver health or cleanup.

## Remaining implementation before approval request

1. Implement the hosted native ports, using existing package/signature and
   publisher verification rather than weakening or duplicating those checks.
2. Implement an independent process supervisor and cleanup ledger. Elapsed-time
   checks cannot interrupt synchronous device calls or port operations. Never
   kill shared Wudfhost processes or infer cleanup from worker/VM termination.
3. Independently review and pure-test that composition and its failure paths.
4. Bind every executable input to the specifically reviewed immutable commit.
   Do not accept a caller's boolean or environment variable as approval evidence.
5. Supply the exact reviewed commit and one-attempt approval bundle to the user.
   A general continuation or an earlier staging approval does not authorize
   publisher trust, device creation or driver execution.

## Proposed approval scope, not authorization

One fresh standard public GitHub Windows x64 VM; one adapter and one target's
health/enumeration only. Five-minute outer cap, 30-second final reporting reserve.
No second display, explicit topology configuration, pixels, input, hotkeys,
UIA, RDP, prompts, reboot, paid infrastructure or local computer interaction.

The unchanged candidate is VirtualDrivers display-only release 25.7.23:

- Archive SHA-256:
  `e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a`.
- Exact SignPath Foundation leaf DER SHA-256:
  `69dfd3e1cbd49f039463edfe4c0d876c1dc7fdcd3aef08700a41355dc6c15fdb`.
- Leaf thumbprint: `3CF8CF26D8BA266C3A483AB7D26D4A818E317D76`.
- Any new approval must name temporary LocalMachine TrustedPublisher addition,
  staging, owned device creation and actual execution of the signed driver.
  No root/CA import or security disabling is proposed.

The source's broad mutable named pipe raises a same-VM interference concern;
remote access, exploitation and host escape have not been demonstrated. A fresh
VM reduces persistence and separates this work from the user's PC. It does not
remove current-job credentials, network access, output integrity or hang risks.
Conditional acceptance for a bounded experiment is distinct from routine CI
adoption. Package, certificate, files, devices and topology need independent
cleanup evidence; VM disposal provides containment, not proof of cleanup.

## Workflow linter compatibility

GitHub documents job-level `cache-mode: none` and scoped-token enforcement.
The latest published actionlint 1.7.12 rejects the new key. The compatibility
checker permits only source-correlated diagnostics for the exact two owned
`cache-mode: none` job lines. Invalid values, duplicates, missing restrictions,
other diagnostics and failed tools without structured results still fail.
It neither disables syntax checking globally nor changes the workflow before
GitHub executes it. Remove this bridge when upstream supports the key.

References:

- [GitHub cache-mode enforcement](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#controlling-cache-access-with-cache-mode)
- [GitHub hosted VM and administrator scope](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [Microsoft software-device creation](https://learn.microsoft.com/en-us/windows/win32/api/swdevice/nf-swdevice-swdevicecreate)
- [Microsoft software-device close semantics](https://learn.microsoft.com/en-us/windows/win32/api/swdevice/nf-swdevice-swdeviceclose)
