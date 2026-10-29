using System;
using System.Collections.Generic;
using VerdiClip.RuntimeDesign;

public static class RuntimeControllerTests
{
    const string Nonce = "0123456789abcdef0123456789abcdef";
    static int checks;
    static void Check(bool condition, string label)
    { checks++; if (!condition) throw new Exception(label); }
    static RuntimeAdmission Admission()
    { return new RuntimeAdmission { Repository="mikejhill/verdiclip", SourceSha=new string('a',40),
        ApprovedSha=new string('a',40), CacheMode="none", Attempt="1", Hosted=true,
        WindowsX64=true, PublicRepository=true, Manual=true, RuntimeApproved=true }; }
    sealed class Fake : IRuntimePorts, ISoftwareDeviceApi
    {
        public long Time;
        public string FailAt, Mismatch;
        public bool NoCallback, AddWrote, StageWrote, FilesWrote;
        public bool LateStart;
        public readonly List<string> Calls = new List<string>();
        public long ElapsedMilliseconds { get { return Time; } }
        void Hit(string name) { Calls.Add(name); if (name==FailAt) throw new Exception("injected"); }
        public string Baseline() { Hit("baseline"); return "original"; }
        public void VerifyPreconditions() { Hit("preconditions"); }
        public void AddPublisher() { AddWrote=true; Hit("add"); }
        public void StagePackage() { StageWrote=true; Hit("stage"); }
        public string VerifyOwnedPackage() { Hit("package-identity"); return Mismatch=="package" ? "oem*.inf" : "oem12.inf"; }
        public void CreateOwnedConfiguration() { FilesWrote=true; Hit("config"); }
        public DeviceEvidence ObserveDevice(string id)
        {
            Hit("observe");
            return new DeviceEvidence { InstanceId=id, Enumerator="VerdiClipNativeProbe", Parent="HTREE\\ROOT\\0",
                HardwareId="MttVDD", Generation=Nonce, BaselineAbsent=true, Package="oem12.inf",
                PackageDigest=RuntimeController.PackageDigest, Healthy=true, OneConnectedTarget=true,
                DescendantsVerified=Mismatch!="descendants" };
        }
        public void WaitBounded() { Hit("wait"); Time+=10000; }
        public void RemoveExactDevice(DeviceEvidence evidence) { Hit("remove-device"); }
        public void VerifyDeviceAbsent(string id) { Hit("device-absent"); }
        public void RemoveExactPackage(string package) { Hit("remove-package"); }
        public void VerifyPackageBaseline() { Hit("package-baseline"); }
        public void RemoveOwnedPublisher() { Hit("remove-publisher"); AddWrote=false; }
        public void VerifyPublisherAbsent() { Hit("publisher-absent"); }
        public void RemoveOwnedConfiguration() { Hit("remove-files"); FilesWrote=false; }
        public void VerifyBaseline(string baseline) { Hit("restore"); }
        public StartResult Start(string id, Action<CreationEvent> callback)
        {
            Hit("start");
            if (!NoCallback) callback(new CreationEvent(new IntPtr(5),0,"SWD\\VerdiClipNativeProbe\\"+id));
            if(LateStart)Time=220000;
            return new StartResult(new IntPtr(5),0);
        }
        public void Close(IntPtr handle) { Hit("close"); }
    }
    static RuntimeReceipt Run(Fake fake, RuntimeAdmission admission=null)
    { return new RuntimeController(fake,fake,Nonce).Run(admission ?? Admission()); }
    public static string Run()
    {
        var good=new Fake(); var receipt=Run(good);
        Check(receipt.Passed && receipt.Errors.Count==0,"successful lifecycle");
        Check(good.Calls.IndexOf("close")<good.Calls.IndexOf("remove-device"),"handle before devnode");
        Check(good.Calls.IndexOf("remove-device")<good.Calls.IndexOf("remove-package"),"device before package");
        Check(good.Calls.IndexOf("remove-package")<good.Calls.IndexOf("remove-publisher"),"independent publisher reserve");
        Action<RuntimeAdmission>[] invalid = {
            a=>a.RuntimeApproved=false,a=>a.Hosted=false,a=>a.WindowsX64=false,a=>a.PublicRepository=false,
            a=>a.Manual=false,a=>a.Repository="other",a=>a.Attempt="2",a=>a.CacheMode="write",
            a=>a.SourceSha=new string('b',40),a=>a.ApprovedSha=null,a=>a.SourceSha=new string('z',40),
            a=>a.SecretsReferenced=true,a=>a.Oidc=true,a=>a.PersistCredentials=true,
            a=>a.UntrustedCode=true,a=>a.PrivilegedConsumer=true };
        foreach(var change in invalid) {
            var admission=Admission();change(admission); var fake=new Fake();
            Check(!Run(fake,admission).Passed && fake.Calls.Count==0,"refused before observation or mutation");
        }
        foreach(string phase in new[]{"baseline","preconditions","add","stage","package-identity","config","start",
            "observe","close","remove-device","device-absent","remove-package","package-baseline",
            "remove-publisher","publisher-absent","remove-files","restore"}) {
            var fake=new Fake{FailAt=phase}; var result=Run(fake);
            Check(!result.Passed && result.Errors.Count>0,"failure cannot pass: "+phase);
            if(result.PublisherAttempted) Check(fake.Calls.Contains("remove-publisher"),"publisher independent: "+phase);
            if(result.ConfigurationAttempted) Check(fake.Calls.Contains("remove-files"),"files independent: "+phase);
            if(result.DeviceAttempted && !result.DeviceRemoved)
                Check(!fake.Calls.Contains("remove-package"),"no package delete under uncertain device: "+phase);
        }
        var uncertain=new Fake{NoCallback=true}; var timed=Run(uncertain);
        Check(!timed.Passed && uncertain.Time==150000,"inclusive work deadline");
        Check(uncertain.Calls.Contains("remove-publisher") && !uncertain.Calls.Contains("remove-package"),"uncertain device cannot suppress trust rollback");
        var foreign=new Fake{Mismatch="descendants"};var unowned=Run(foreign);
        Check(!unowned.Passed && !foreign.Calls.Contains("remove-device"),"unknown descendants never deleted");
        var malformed=new Fake{Mismatch="package"};Check(!Run(malformed).Passed && !malformed.Calls.Contains("start"),"wildcard package refused");
        var late=new Fake{LateStart=true};var lateResult=Run(late);
        Check(!lateResult.Passed && late.Calls.Contains("close"),"known handle close signalled after expired device budget");
        Check(late.Calls.Contains("remove-publisher") && !late.Calls.Contains("remove-device"),"late return preserves trust reserve and refuses late node deletion");
        var partialRemove=new Fake{FailAt="remove-publisher"};var partial=Run(partialRemove);
        Check(!partial.Passed && partialRemove.Calls.Contains("publisher-absent"),"observe publisher rollback even after remove transport error");
        var expired=new Fake{Time=150000};Check(!Run(expired).Passed && expired.Calls.Count==0,"expired before mutation");
        var api=new Fake();var controller=new RuntimeController(api,api,Nonce);controller.Run(Admission());
        bool refused=false;try{controller.Run(Admission());}catch(InvalidOperationException){refused=true;}
        Check(refused,"controller cannot retry");
        var shared=new Fake();var single=new RuntimeController(shared,shared,Nonce);
        int accepted=0, rejected=0;
        System.Threading.Tasks.Parallel.For(0,32,i=>{
            try { single.Run(Admission());System.Threading.Interlocked.Increment(ref accepted); }
            catch(InvalidOperationException){System.Threading.Interlocked.Increment(ref rejected);}
        });
        Check(accepted==1 && rejected==31 && shared.Calls.FindAll(c=>c=="baseline").Count==1,
            "concurrent callers cannot mutate twice");
        return checks+" controller checks passed (managed fakes only).";
    }
}
