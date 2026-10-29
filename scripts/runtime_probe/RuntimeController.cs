// Pure orchestration. No default/native port, entry point or runtime workflow.
using System;
using System.Collections.Generic;

namespace VerdiClip.RuntimeDesign
{
    public sealed class RuntimeAdmission
    {
        public string Repository, SourceSha, ApprovedSha, CacheMode, Attempt;
        public bool Hosted, WindowsX64, PublicRepository, Manual, RuntimeApproved;
        public bool SecretsReferenced, Oidc, PersistCredentials, UntrustedCode, PrivilegedConsumer;
        public void Validate()
        {
            if (!Hosted || !WindowsX64 || !PublicRepository || !Manual || !RuntimeApproved ||
                Repository != "mikejhill/verdiclip" || Attempt != "1" || CacheMode != "none" ||
                SourceSha == null || SourceSha.Length != 40 || SourceSha != ApprovedSha ||
                !System.Text.RegularExpressions.Regex.IsMatch(SourceSha, "\\A[0-9a-f]{40}\\z") ||
                SecretsReferenced || Oidc || PersistCredentials || UntrustedCode || PrivilegedConsumer)
                throw new InvalidOperationException("Unapproved or non-isolated runtime context.");
        }
    }

    public sealed class DeviceEvidence
    {
        public string InstanceId, Enumerator, Parent, HardwareId, Package, PackageDigest, Generation;
        public bool Healthy, OneConnectedTarget, DescendantsVerified, BaselineAbsent;
        public bool Owned(string nonce, string callbackId, string package, string digest)
        {
            return InstanceId == callbackId &&
                String.Equals(InstanceId, "SWD\\VerdiClipNativeProbe\\" + nonce, StringComparison.OrdinalIgnoreCase) &&
                Enumerator == "VerdiClipNativeProbe" && Parent == "HTREE\\ROOT\\0" &&
                HardwareId == "MttVDD" && Generation == nonce && BaselineAbsent &&
                Package == package && PackageDigest == digest && DescendantsVerified;
        }
    }

    // All operations must be bounded by an independent future process supervisor.
    // Strings are opaque snapshot/manifest identities, not shell command fragments.
    public interface IRuntimePorts
    {
        long ElapsedMilliseconds { get; }
        string Baseline();
        void VerifyPreconditions();
        void AddPublisher();
        void StagePackage();
        string VerifyOwnedPackage();
        void CreateOwnedConfiguration();
        DeviceEvidence ObserveDevice(string exactCallbackId);
        void WaitBounded();
        void RemoveExactDevice(DeviceEvidence evidence);
        void VerifyDeviceAbsent(string exactId);
        void RemoveExactPackage(string verifiedPackage);
        void VerifyPackageBaseline();
        void RemoveOwnedPublisher();
        void VerifyPublisherAbsent();
        void RemoveOwnedConfiguration();
        void VerifyBaseline(string baseline);
    }

    public sealed class RuntimeReceipt
    {
        public bool Admitted, RuntimeObserved, HandleClosed, DeviceRemoved, PackageRemoved;
        public bool PublisherRemoved, FilesRemoved, BaselineRestored, Passed;
        public bool PublisherAttempted, StageAttempted, ConfigurationAttempted, DeviceAttempted;
        public readonly List<string> Errors = new List<string>();
    }

    public sealed class RuntimeController
    {
        public const long WorkDeadline = 150000, DeviceCleanupDeadline = 210000;
        public const long PackageCleanupDeadline = 240000, CleanupDeadline = 270000;
        // The digest describes the pinned INF/CAT/DLL tuple; the port must verify
        // the actual exports before returning a package identity and before delete.
        public const string PackageDigest = "550d211fe481e74dfe3f9d724ed78be48b3a9113405965d683d9373e8d672f5d:" +
            "08a0093fc9b2e32b287a6f8a77ca4de0a31830d29fc33d2b13a918dc859468f6:" +
            "c9ca837f57a98fbd43bc416a7f535a95843626e7759eaf85cf0cd7ce334dbb05";
        private readonly IRuntimePorts ports;
        private readonly SoftwareDeviceLease lease;
        private readonly string nonce;
        private int used;
        public RuntimeController(IRuntimePorts ports, ISoftwareDeviceApi api, string nonce)
        {
            if (ports == null) throw new ArgumentNullException("ports");
            this.ports = ports; this.nonce = nonce;
            lease = new SoftwareDeviceLease(api, nonce, WorkDeadline);
        }
        private void Before(long deadline)
        { if (ports.ElapsedMilliseconds < 0 || ports.ElapsedMilliseconds >= deadline) throw new InvalidOperationException("Phase deadline expired."); }
        private void Cleanup(RuntimeReceipt receipt, string phase, long deadline, Action action)
        {
            try { Before(deadline); action(); Before(deadline); }
            catch (Exception error) { receipt.Errors.Add(phase + ": " + error.GetType().Name); }
        }
        public RuntimeReceipt Run(RuntimeAdmission admission)
        {
            if (System.Threading.Interlocked.CompareExchange(ref used, 1, 0) != 0)
                throw new InvalidOperationException("One attempt only, including concurrent callers.");
            var receipt = new RuntimeReceipt();
            string baseline = null, package = null;
            bool preconditionsVerified = false;
            try
            {
                admission.Validate(); Before(WorkDeadline);
                baseline = ports.Baseline();
                if (String.IsNullOrEmpty(baseline)) throw new InvalidOperationException("Missing baseline.");
                ports.VerifyPreconditions(); Before(WorkDeadline); preconditionsVerified = true;
                // Claim intent before each mutation: a throwing transport may have written.
                receipt.PublisherAttempted = true; ports.AddPublisher(); Before(WorkDeadline);
                receipt.StageAttempted = true; ports.StagePackage(); Before(WorkDeadline);
                package = ports.VerifyOwnedPackage();
                if (!System.Text.RegularExpressions.Regex.IsMatch(package ?? "", "\\Aoem[0-9]+\\.inf\\z"))
                    throw new InvalidOperationException("Unverified package identity.");
                Before(WorkDeadline); receipt.ConfigurationAttempted = true;
                ports.CreateOwnedConfiguration(); Before(WorkDeadline);
                receipt.DeviceAttempted = true; lease.Begin();
                for (;;)
                {
                    Before(WorkDeadline); lease.Poll(ports.ElapsedMilliseconds);
                    LeaseReceipt state = lease.Inspect();
                    if (state.Failure != null || state.OwnershipUncertain) throw new InvalidOperationException("Device lease failed.");
                    if (state.CreationAccepted)
                    {
                        DeviceEvidence evidence = ports.ObserveDevice(state.CallbackInstanceId);
                        if (evidence != null && evidence.Owned(nonce, state.CallbackInstanceId, package, PackageDigest) &&
                            evidence.Healthy && evidence.OneConnectedTarget)
                        { Before(WorkDeadline); receipt.Admitted = true; receipt.RuntimeObserved = true; break; }
                    }
                    ports.WaitBounded();
                }
            }
            catch (Exception error) { receipt.Errors.Add("work: " + error.GetType().Name); }
            finally
            {
                // Independent cleanup blocks: device/package failures never suppress leaf rollback.
                if (receipt.DeviceAttempted)
                {
                    // Always signal known-handle close, even if a blocking native operation
                    // returned after its work/observation budget. A future outer process
                    // watchdog must bound this synchronous call; it is not cancellable here.
                    try { lease.RequestStop(); }
                    catch (Exception error) { receipt.Errors.Add("handle: " + error.GetType().Name); }
                    Cleanup(receipt, "device", DeviceCleanupDeadline, () => {
                        LeaseReceipt state = lease.Inspect();
                        receipt.HandleClosed = state.CloseSucceeded && !state.OwnershipUncertain;
                        if (!receipt.HandleClosed || state.CallbackInstanceId == null)
                            throw new InvalidOperationException("Uncertain handle/instance ownership.");
                        DeviceEvidence current = ports.ObserveDevice(state.CallbackInstanceId);
                        if (current == null || !current.Owned(nonce, state.CallbackInstanceId, package, PackageDigest))
                            throw new InvalidOperationException("Fresh exact device ownership required.");
                        ports.RemoveExactDevice(current); ports.VerifyDeviceAbsent(current.InstanceId);
                        Before(DeviceCleanupDeadline); receipt.DeviceRemoved = true;
                    });
                }
                if (receipt.StageAttempted)
                    Cleanup(receipt, "package", PackageCleanupDeadline, () => {
                        if (receipt.DeviceAttempted && !receipt.DeviceRemoved)
                            throw new InvalidOperationException("Device removal unverified; no package deletion.");
                        // Rediscover and reverify even after a partial staging failure.
                        string current = ports.VerifyOwnedPackage();
                        if (!System.Text.RegularExpressions.Regex.IsMatch(current ?? "", "\\Aoem[0-9]+\\.inf\\z") ||
                            (package != null && current != package)) throw new InvalidOperationException("Package ownership changed.");
                        ports.RemoveExactPackage(current); ports.VerifyPackageBaseline();
                        Before(PackageCleanupDeadline); receipt.PackageRemoved = true;
                    });
                if (receipt.PublisherAttempted && preconditionsVerified)
                {
                    Cleanup(receipt, "publisher", CleanupDeadline, () => {
                        ports.RemoveOwnedPublisher();
                    });
                    // A write can succeed and its transport then throw. Observe absence
                    // independently rather than hide that evidence behind the remove call.
                    Cleanup(receipt, "publisher-observation", CleanupDeadline, () => {
                        ports.VerifyPublisherAbsent();
                        Before(CleanupDeadline); receipt.PublisherRemoved = true;
                    });
                }
                if (receipt.ConfigurationAttempted)
                    Cleanup(receipt, "files", CleanupDeadline, () => {
                        ports.RemoveOwnedConfiguration(); Before(CleanupDeadline); receipt.FilesRemoved = true;
                    });
                if (baseline != null)
                    Cleanup(receipt, "baseline", CleanupDeadline, () => {
                        ports.VerifyBaseline(baseline); Before(CleanupDeadline); receipt.BaselineRestored = true;
                    });
            }
            receipt.Passed = receipt.Admitted && receipt.RuntimeObserved && receipt.HandleClosed &&
                receipt.DeviceRemoved && receipt.PackageRemoved && receipt.PublisherRemoved &&
                receipt.FilesRemoved && receipt.BaselineRestored && receipt.Errors.Count == 0;
            return receipt;
        }
    }
}
