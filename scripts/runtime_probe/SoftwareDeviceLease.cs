// Local unpublished library prototype. No entry point, runtime factory or workflow.
// Tests inject a managed fake; the internal native binding is compiled, NEVER invoked.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace VerdiClip.RuntimeDesign
{
    public sealed class CreationEvent
    {
        public readonly IntPtr Handle;
        public readonly int HResult;
        public readonly string InstanceId;
        public CreationEvent(IntPtr handle, int result, string id)
        { Handle = handle; HResult = result; InstanceId = id; }
    }
    public sealed class StartResult
    {
        public readonly IntPtr Handle;
        public readonly int HResult;
        public StartResult(IntPtr handle, int result) { Handle = handle; HResult = result; }
    }
    public interface ISoftwareDeviceApi
    {
        StartResult Start(string uniqueInstance, Action<CreationEvent> callback);
        void Close(IntPtr handle);
    }
    public sealed class LeaseReceipt
    {
        public bool StartSettled, CallbackObserved, CreationAccepted, StopRequested;
        public bool CloseAttempted, CloseSucceeded, OwnershipUncertain;
        public bool FailedOutputUnclaimed, CallbackHandleUnclaimed;
        public int? InitiationHResult, CallbackHResult;
        public string CallbackInstanceId, Failure;
    }
    public sealed class SoftwareDeviceLease
    {
        private readonly object gate = new object();
        private readonly ISoftwareDeviceApi api;
        private readonly string instance;
        private readonly long deadline;
        private bool began, settled, stop, closeClaimed, closed, uncertain;
        private IntPtr returnedHandle;
        private CreationEvent callbackEvent;
        private bool validCallback, failedOutput;
        private string failure;
        private int? initiationResult;

        // Monotonic milliseconds are supplied by the future owner, not wall-clock time.
        public SoftwareDeviceLease(ISoftwareDeviceApi api, string uniqueInstance, long deadlineMilliseconds)
        {
            if (api == null || uniqueInstance == null ||
                !Guid.TryParseExact(uniqueInstance, "N", out Guid parsed) || parsed == Guid.Empty || deadlineMilliseconds <= 0)
                throw new ArgumentException("A unique GUID, injected API and positive deadline are required.");
            this.api = api; instance = uniqueInstance; deadline = deadlineMilliseconds;
        }
        private void Fail(string reason)
        { if (failure == null) failure = reason; stop = true; }

        public void Begin()
        {
            lock (gate)
            {
                if (began || stop) throw new InvalidOperationException("One creation only; never restart a stopped owner.");
                began = true;
            }
            // Never hold the lock across Start: native callbacks may occur before it returns.
            try
            {
                StartResult result = api.Start(instance, RecordCallback);
                lock (gate)
                {
                    if (result == null) Fail("Missing start result.");
                    else
                    {
                        initiationResult = result.HResult;
                        if (result.HResult != 0 || result.Handle == IntPtr.Zero)
                        {
                            if (result.Handle != IntPtr.Zero) { failedOutput = true; uncertain = true; }
                            Fail("Creation initiation failed or returned no handle.");
                        }
                        else returnedHandle = result.Handle;
                    }
                }
            }
            catch (Exception)
            { lock (gate) { uncertain = true; Fail("Creation transport failed; retain callback evidence for cleanup."); } }
            finally
            {
                lock (gate) { settled = true; CheckAgreement(); }
                CloseIfRequested();
            }
        }
        private void RecordCallback(CreationEvent value)
        {
            // No native calls, waits, exceptions or uninstall operations inside the callback.
            lock (gate)
            {
                if (callbackEvent != null) { uncertain = true; Fail("Duplicate callback."); return; }
                if (value == null) { uncertain = true; Fail("Missing callback data."); return; }
                callbackEvent = value;
                if (closed) { uncertain = true; Fail("Callback observed after close completed."); }
                validCallback = !(value.HResult != 0 || value.Handle == IntPtr.Zero || string.IsNullOrEmpty(value.InstanceId) ||
                    value.InstanceId.Length > 1024 || value.InstanceId.IndexOf('\0') >= 0 ||
                    value.InstanceId.IndexOf(instance, StringComparison.OrdinalIgnoreCase) < 0);
                if (!validCallback)
                    Fail("Callback did not establish a successful owned instance.");
                CheckAgreement();
            }
        }
        private void CheckAgreement()
        {
            if (settled && returnedHandle != IntPtr.Zero && validCallback && callbackEvent != null &&
                callbackEvent.Handle != IntPtr.Zero && callbackEvent.Handle != returnedHandle)
            { uncertain = true; Fail("Handle disagreement: never close a guessed second handle."); }
            if (settled && callbackEvent != null && callbackEvent.Handle != IntPtr.Zero &&
                !validCallback && callbackEvent.Handle != returnedHandle)
                uncertain = true;
        }
        public LeaseReceipt Poll(long elapsedMilliseconds)
        {
            lock (gate)
            {
                if (elapsedMilliseconds < 0) Fail("Invalid monotonic time.");
                else if (elapsedMilliseconds >= deadline) Fail("Creation/observation deadline expired.");
            }
            CloseIfRequested();
            return Inspect();
        }
        public void RequestStop()
        {
            lock (gate) { stop = true; }
            CloseIfRequested();
        }
        private void CloseIfRequested()
        {
            IntPtr handle;
            lock (gate)
            {
                if (!stop || !settled || closeClaimed) return;
                // Prefer the handle returned by our call. A callback-only handle is retained
                // when transport throws after reporting its own creation, never a guessed ID.
                handle = returnedHandle != IntPtr.Zero ? returnedHandle :
                    (callbackEvent == null || !validCallback ? IntPtr.Zero : callbackEvent.Handle);
                if (handle == IntPtr.Zero) return;
                closeClaimed = true;
            }
            try { api.Close(handle); lock (gate) { closed = true; } }
            catch (Exception) { lock (gate) { uncertain = true; Fail("Handle close failed; cleanup is unverified."); } }
        }
        public LeaseReceipt Inspect()
        {
            lock (gate)
            {
                return new LeaseReceipt {
                    StartSettled = settled, CallbackObserved = callbackEvent != null,
                    CreationAccepted = began && settled && callbackEvent != null && failure == null && !stop && !uncertain,
                    StopRequested = stop, CloseAttempted = closeClaimed, CloseSucceeded = closed,
                    OwnershipUncertain = uncertain,
                    FailedOutputUnclaimed = failedOutput,
                    CallbackHandleUnclaimed = callbackEvent != null && callbackEvent.Handle != IntPtr.Zero &&
                        !validCallback && callbackEvent.Handle != returnedHandle,
                    CallbackInstanceId = callbackEvent == null ? null : callbackEvent.InstanceId,
                    InitiationHResult = initiationResult,
                    CallbackHResult = callbackEvent == null ? (int?)null : callbackEvent.HResult,
                    Failure = failure
                };
            }
        }
    }

    // Deliberately internal: no published/native factory or executable exists in this prototype.
    // Future activation needs a separately reviewed, specifically approved hosted worker.
    internal sealed class NativeSoftwareDeviceApi : ISoftwareDeviceApi
    {
        private static int used;
        private static readonly List<object> roots = new List<object>();
        private readonly List<IntPtr> argumentBuffers = new List<IntPtr>();
        private IntPtr returnedHandle, callbackHandle;
        private int closeUsed;
        // One adapter, delegate and small immutable argument bundle remain rooted for the
        // bounded worker's whole lifetime. No async input-copy lifetime is assumed. This
        // intentional process-lifetime allocation is unsuitable for a long-lived app.
        [UnmanagedFunctionPointer(CallingConvention.Winapi, CharSet = CharSet.Unicode)]
        private delegate void NativeCallback(IntPtr handle, int result, IntPtr context,
            [MarshalAs(UnmanagedType.LPWStr)] string instanceId);
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct CreateInfo
        {
            public uint Size;
            public IntPtr Instance, HardwareIds, CompatibleIds, ContainerId;
            public uint Capabilities;
            public IntPtr Description, Location, SecurityDescriptor;
        }
        [DllImport("Cfgmgr32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        private static extern int SwDeviceCreate(IntPtr enumerator, IntPtr parent, IntPtr info,
            uint propertyCount, IntPtr properties, NativeCallback callback, IntPtr context, out IntPtr handle);
        [DllImport("Cfgmgr32.dll", ExactSpelling = true)]
        [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
        private static extern void SwDeviceClose(IntPtr handle);

        private IntPtr KeepText(string text)
        {
            IntPtr value = Marshal.StringToHGlobalUni(text);
            argumentBuffers.Add(value);
            return value;
        }

        public StartResult Start(string uniqueInstance, Action<CreationEvent> callback)
        {
            if (Interlocked.CompareExchange(ref used, 1, 0) != 0)
                throw new InvalidOperationException("At most one native creation per bounded worker.");
            if (callback == null || !Guid.TryParseExact(uniqueInstance, "N", out Guid parsed) || parsed == Guid.Empty)
                throw new ArgumentException("Explicit owner and unique instance required.");
            NativeCallback rooted = (h, hr, context, id) => {
                // Contain managed exceptions at the unmanaged boundary. The owner records
                // its own protocol failures; no device or file operations run here.
                if (hr == 0 && h != IntPtr.Zero && !string.IsNullOrEmpty(id) && id.Length <= 1024 &&
                    id.IndexOf('\0') < 0 && id.IndexOf(uniqueInstance, StringComparison.OrdinalIgnoreCase) >= 0)
                    Interlocked.CompareExchange(ref callbackHandle, h, IntPtr.Zero);
                try { callback(new CreationEvent(h, hr, id)); } catch { }
            };
            lock (roots) { roots.Add(this); roots.Add(rooted); }
            var info = new CreateInfo {
                Size = (uint)Marshal.SizeOf(typeof(CreateInfo)), Instance = KeepText(uniqueInstance),
                HardwareIds = KeepText("MttVDD\0"), // MULTI_SZ plus automatic final NUL
                CompatibleIds = IntPtr.Zero, ContainerId = IntPtr.Zero,
                Capabilities = 0x00000001 | 0x00000008, // removable + driver-required ONLY
                Description = KeepText("VerdiClip disposable one-display capability probe"),
                Location = IntPtr.Zero, SecurityDescriptor = IntPtr.Zero
            };
            IntPtr enumerator = KeepText("VerdiClipNativeProbe"), parent = KeepText("HTREE\\ROOT\\0");
            IntPtr infoBuffer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(CreateInfo)));
            argumentBuffers.Add(infoBuffer);
            Marshal.StructureToPtr(info, infoBuffer, false);
            IntPtr handle;
            int result = SwDeviceCreate(enumerator, parent, infoBuffer,
                0, IntPtr.Zero, rooted, IntPtr.Zero, out handle);
            if (result == 0 && handle != IntPtr.Zero) Interlocked.Exchange(ref returnedHandle, handle);
            GC.KeepAlive(rooted);
            return new StartResult(handle, result);
        }
        public void Close(IntPtr handle)
        {
            if (handle == IntPtr.Zero) throw new ArgumentException("No handle to close.");
            IntPtr known = Interlocked.CompareExchange(ref returnedHandle, IntPtr.Zero, IntPtr.Zero);
            if (known == IntPtr.Zero)
                known = Interlocked.CompareExchange(ref callbackHandle, IntPtr.Zero, IntPtr.Zero);
            if (handle != known || Interlocked.CompareExchange(ref closeUsed, 1, 0) != 0)
                throw new InvalidOperationException("Only the known owned handle can be closed once.");
            SwDeviceClose(handle);
            // Do not clear the process-lifetime callback/argument roots. Closing the handle is not
            // proof of devnode uninstall, package removal, host exit or topology restoration.
        }
    }
}
