# Installed collector metadata admission

This isolated draft workflow does not collect stacks or start a debugger. It reads
four fixed installed-file locations: Windows SDK x64 CDB, and the Framework64
v4.0.30319 CLR, SOS and DAC. It does not search the filesystem for other tools.
ClrMD is reported as unlocated because no verified fixed installed path is known.

The repository-built SDK helper checks pinned non-reparse ancestry, PE architecture,
numeric version resources and offline cached Authenticode trust. It requests chain
revocation validation with cache-only URL retrieval. Missing revocation cache is
unknown, not trusted or absent. A successful result is cached trust, not a fresh
online revocation check. Publisher comparison uses the validated signer certificate's
organization field and requires exactly Microsoft Corporation. Catalog-only signing
is not separately resolved; an unsigned result is this file verification outcome,
not proof that Windows has no catalog trust for the file.

Each stage writes a fixed flushed record before the operation. The supervisor allows
one helper process per target, at most ten seconds plus an independent two-second
retirement wait. A 4 KiB polled output rejection threshold is not a hard OS output
cap. Output and errors stay in private temporary files; only validated fixed records
are retained. If retirement is unconfirmed, later targets are not inspected. Ordinary
filesystem/reporting failures are reduced without publishing paths or exception text.
Private deletion and process retirement are reported separately and honestly.

Matching architecture, cached signer trust and equal CLR/SOS/DAC file versions are
only a candidate for later compatibility testing. They do not prove DAC compatibility,
managed-stack decoding, safe suspension or attachment permission. There is no target
process, dump, ETW session, symbol-server lookup, candidate DLL load, candidate tool
help invocation, installation, certificate change or physical input in this workflow.
The existing product tests and deadlines are unchanged.

Branch gate: diagnostic/collector-metadata, same repository, draft pull request only.
The workflow checks out the explicit head SHA and uses the installed Windows SDK.
Actual SDK compilation and installed-file results remain hosted validation until run.
