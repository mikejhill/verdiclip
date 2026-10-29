using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using VerdiClip.RuntimeDesign;

public static class SoftwareDeviceLeaseTests
{
    private const string Id = "0123456789abcdef0123456789abcdef";
    private const string Actual = "SWD\\VerdiClipNativeProbe\\" + Id;
    private static readonly IntPtr Handle = new IntPtr(123);
    private static int checks;
    private static void Check(bool value, string label)
    { checks++; if (!value) throw new Exception(label); }
    private static void Refused(Action action, string label)
    { bool rejected = false; try { action(); } catch (ArgumentException) { rejected = true; }
      catch (InvalidOperationException) { rejected = true; } Check(rejected, label); }
    private sealed class Fake : ISoftwareDeviceApi
    {
        public Action<CreationEvent> Callback;
        public Func<Fake, StartResult> OnStart;
        public Action<Fake> OnClose;
        public int Starts, Closes;
        public readonly List<IntPtr> Closed = new List<IntPtr>();
        public StartResult Start(string id, Action<CreationEvent> callback)
        { Starts++; Callback = callback; return OnStart == null ? new StartResult(Handle, 0) : OnStart(this); }
        public void Close(IntPtr handle)
        { Closes++; Closed.Add(handle); if (OnClose != null) OnClose(this); }
        public void Success() { Callback(new CreationEvent(Handle, 0, Actual)); }
    }
    private static SoftwareDeviceLease Lease(Fake fake) { return new SoftwareDeviceLease(fake, Id, 100); }
    public static string Run()
    {
        Refused(() => new SoftwareDeviceLease(null, Id, 100), "null API");
        Refused(() => new SoftwareDeviceLease(new Fake(), "not-guid", 100), "invalid identity");
        Refused(() => new SoftwareDeviceLease(new Fake(), new string('0', 32), 100), "empty GUID is not an owned generation");
        Refused(() => new SoftwareDeviceLease(new Fake(), Id, 0), "invalid deadline");
        var fake = new Fake { OnStart = f => { f.Success(); return new StartResult(Handle, 0); } };
        var lease = Lease(fake); lease.Begin();
        Check(lease.Inspect().CreationAccepted, "inline callback must not deadlock or lose identity");
        Check(lease.Inspect().InitiationHResult == 0 && lease.Inspect().CallbackHResult == 0,
            "retain both HRESULTs independently");
        Refused(() => lease.Begin(), "duplicate Begin");
        lease.RequestStop(); lease.RequestStop();
        Check(fake.Closes == 1 && lease.Inspect().CloseSucceeded, "one close only");
        Check(lease.Inspect().CallbackInstanceId == Actual, "use exact callback ID, not guessed ID");

        fake = new Fake(); lease = Lease(fake); lease.Begin();
        Check(!lease.Inspect().CreationAccepted, "initiation is not enumeration");
        fake.Success(); Check(lease.Poll(99).CreationAccepted, "delayed callback before deadline");
        Check(!lease.Poll(100).CreationAccepted && fake.Closes == 1, "deadline inclusive, close once");
        lease.Poll(101); Check(fake.Closes == 1, "repeated timeout cannot recreate/close again");

        fake = new Fake(); lease = Lease(fake); lease.RequestStop();
        Refused(() => lease.Begin(), "stop before start cannot create");
        Check(fake.Starts == 0 && fake.Closes == 0, "cancel before start no operation");

        fake = new Fake { OnStart = f => { throw new Exception("transport"); } };
        lease = Lease(fake); lease.Begin();
        Check(lease.Inspect().Failure != null && fake.Closes == 0, "throw before handle no guessed close");
        fake = new Fake { OnStart = f => { f.Success(); throw new Exception("after callback"); } };
        lease = Lease(fake); lease.Begin();
        Check(fake.Closes == 1 && lease.Inspect().CloseSucceeded, "throw after callback retains own handle");

        foreach (int hr in new[] { -1, 1 })
        {
            fake = new Fake { OnStart = f => new StartResult(Handle, hr) };
            lease = Lease(fake); lease.Begin();
            Check(!lease.Inspect().CreationAccepted && fake.Closes == 0 && lease.Inspect().FailedOutputUnclaimed &&
                lease.Inspect().OwnershipUncertain, "failed initiation output never establishes close ownership");
        }
        fake = new Fake { OnStart = f => { f.Success(); return new StartResult(Handle, -1); } };
        lease = Lease(fake); lease.Begin();
        Check(fake.Closes == 1 && lease.Inspect().Failure != null,
            "independent valid callback can establish own handle despite failed initiation output");
        foreach (var callbackValue in new[] {new CreationEvent(Handle, 0, "foreign-instance"),
            new CreationEvent(Handle, -1, Actual), new CreationEvent(Handle, 1, Actual)})
        {
            fake = new Fake { OnStart = f => { f.Callback(callbackValue); throw new Exception("transport"); } };
            lease = Lease(fake); lease.Begin();
            Check(fake.Closes == 0 && lease.Inspect().CallbackHandleUnclaimed && lease.Inspect().OwnershipUncertain,
                "invalid callback plus transport failure never establishes handle ownership");
        }
        fake = new Fake { OnStart = f => new StartResult(IntPtr.Zero, 0) };
        lease = Lease(fake); lease.Begin(); Check(fake.Closes == 0, "zero handle never closed");

        var badCallbacks = new[] {
            new CreationEvent(Handle, -1, Actual), new CreationEvent(Handle, 1, Actual),
            new CreationEvent(IntPtr.Zero, 0, Actual), new CreationEvent(Handle, 0, "foreign-instance"),
            new CreationEvent(Handle, 0, null), new CreationEvent(Handle, 0, Actual + "\0"),
            new CreationEvent(Handle, 0, new string('x', 1025)),
            new CreationEvent(new IntPtr(999), 0, Actual)
        };
        foreach (var value in badCallbacks)
        {
            fake = new Fake { OnStart = f => { f.Callback(value); return new StartResult(Handle, 0); } };
            lease = Lease(fake); lease.Begin();
            Check(!lease.Poll(1).CreationAccepted && fake.Closes == 1 && fake.Closed[0] == Handle,
                "bad callback never accepts or closes arbitrary second handle");
        }

        fake = new Fake { OnStart = f => { f.Success(); f.Success(); return new StartResult(Handle, 0); } };
        lease = Lease(fake); lease.Begin();
        Check(lease.Inspect().OwnershipUncertain && fake.Closes == 1, "duplicate callback fail closed");
        fake = new Fake { OnClose = f => f.Success() }; lease = Lease(fake); lease.Begin(); lease.RequestStop();
        Check(lease.Inspect().CloseSucceeded && !lease.Inspect().OwnershipUncertain,
            "callback during close is legal until native close barrier returns");
        fake.Success(); Check(lease.Inspect().OwnershipUncertain, "callback after close barrier violates contract");
        fake = new Fake { OnClose = f => { throw new Exception("close"); } };
        lease = Lease(fake); lease.Begin(); lease.RequestStop(); lease.RequestStop();
        Check(!lease.Inspect().CloseSucceeded && lease.Inspect().OwnershipUncertain && fake.Closes == 1,
            "close failure unverified, no silent retry");

        using (var entered = new ManualResetEventSlim())
        using (var release = new ManualResetEventSlim())
        {
            fake = new Fake { OnStart = f => {
                f.Success(); entered.Set(); if (!release.Wait(2000)) throw new Exception("test barrier");
                return new StartResult(Handle, 0);
            } };
            lease = Lease(fake); var task = Task.Run(() => lease.Begin());
            Check(entered.Wait(2000), "test start barrier"); lease.RequestStop();
            Check(fake.Closes == 0, "stop while Start in progress defers close");
            release.Set(); Check(task.Wait(2000), "start completes without callback lock deadlock");
            Check(fake.Closes == 1 && lease.Inspect().CloseSucceeded && !lease.Inspect().CreationAccepted,
                "concurrent stop retains creation ownership");
        }
        fake = new Fake(); lease = Lease(fake); lease.Begin(); lease.Poll(-1);
        Check(fake.Closes == 1 && lease.Inspect().Failure != null, "invalid clock fail closed");

        // Inspect metadata only. Never instantiate the internal native adapter.
        var native = typeof(SoftwareDeviceLease).Assembly.GetType("VerdiClip.RuntimeDesign.NativeSoftwareDeviceApi");
        Check(native != null && !native.IsPublic, "native binding not publicly activatable");
        int imports = 0;
        foreach (var method in native.GetMethods(BindingFlags.NonPublic | BindingFlags.Static))
        {
            var import = method.GetCustomAttribute<DllImportAttribute>();
            if (import == null) continue;
            imports++; Check(import.Value == "Cfgmgr32.dll" &&
                (method.Name == "SwDeviceCreate" || method.Name == "SwDeviceClose"), "native import allowlist");
            Check(method.GetCustomAttribute<DefaultDllImportSearchPathsAttribute>().Paths == DllImportSearchPath.System32,
                "native library resolves only in System32");
        }
        Check(imports == 2, "exactly create/close imports, no pipe/display/trust calls");
        var layout = native.GetNestedType("CreateInfo", BindingFlags.NonPublic);
        Check(IntPtr.Size == 8 && Marshal.SizeOf(layout) == 72, "x64 SDK structure layout size");
        return checks + " managed fake/metadata checks passed; zero native API invocations.";
    }
}
