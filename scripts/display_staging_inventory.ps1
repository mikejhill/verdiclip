#Requires -Version 7.0
param([switch]$VerifyPnPTool)
# Hosted-only read operations. No device creation, UI, input or policy changes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch') { throw 'Hosted-only inventory.' }
if ($VerifyPnPTool) {
    $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $env:SystemRoot 'System32/pnputil.exe')
    if ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Existing PnPUtil signature refused.' }
    Write-Output 'Existing PnPUtil validly Microsoft-signed.'
    return
}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;
using System.Text;
public static class StagingReadOnlyInventory {
    [StructLayout(LayoutKind.Sequential)]
    struct Info { public uint Size; public Guid Class; public uint DevInst; public IntPtr Reserved; }
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode, ExactSpelling=true)]
    static extern IntPtr SetupDiGetClassDevsW(IntPtr c, string e, IntPtr p, uint f);
    [DllImport("setupapi.dll", SetLastError=true)]
    static extern bool SetupDiEnumDeviceInfo(IntPtr s, uint i, ref Info d);
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode, ExactSpelling=true)]
    static extern bool SetupDiGetDeviceInstanceIdW(IntPtr s, ref Info d, StringBuilder b, uint n, out uint r);
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode, ExactSpelling=true)]
    static extern bool SetupDiGetDeviceRegistryPropertyW(IntPtr s, ref Info d, uint p, out uint t, byte[] b, uint n, out uint r);
    [DllImport("setupapi.dll", SetLastError=true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr s);
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int L,T,R,B; }
    [StructLayout(LayoutKind.Sequential)] struct Monitor { public uint Size; public Rect Bounds,Work; public uint Flags; }
    delegate bool Callback(IntPtr m, IntPtr h, ref Rect r, IntPtr d);
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool EnumDisplayMonitors(IntPtr h, IntPtr c, Callback cb, IntPtr d);
    [DllImport("user32.dll", SetLastError=true, ExactSpelling=true)] static extern bool GetMonitorInfoW(IntPtr h, ref Monitor m);
    [DllImport("shcore.dll")] static extern int GetScaleFactorForMonitor(IntPtr h, out int scale);
    static string[] Property(IntPtr set, ref Info info, uint prop, uint expectedType) {
        uint type, needed;
        bool found = SetupDiGetDeviceRegistryPropertyW(set,ref info,prop,out type,null,0,out needed);
        int error = Marshal.GetLastWin32Error();
        if (!found && error==13) return new string[0]; // documented absent property
        if (found || error!=122 || needed==0 || needed>65536) throw new Win32Exception(error,"Property size");
        byte[] bytes=new byte[needed];
        if (!SetupDiGetDeviceRegistryPropertyW(set,ref info,prop,out type,bytes,needed,out needed)) throw new Win32Exception();
        if (type!=expectedType || needed>bytes.Length || (needed%2)!=0) throw new Exception("Property type/size");
        string[] values=Encoding.Unicode.GetString(bytes,0,(int)needed).Split(new[]{'\0'},StringSplitOptions.RemoveEmptyEntries);
        Array.Sort(values,StringComparer.OrdinalIgnoreCase); return values;
    }
    public static object Read() {
        var devices=new SortedDictionary<string,object>(StringComparer.OrdinalIgnoreCase);
        IntPtr set=SetupDiGetClassDevsW(IntPtr.Zero,null,IntPtr.Zero,4); // ALLCLASSES, deliberately not PRESENT
        if (set==new IntPtr(-1)) throw new Win32Exception();
        try {
            for (uint i=0;;i++) {
                var info=new Info{Size=(uint)Marshal.SizeOf(typeof(Info))};
                if (!SetupDiEnumDeviceInfo(set,i,ref info)) { if(Marshal.GetLastWin32Error()==259)break; throw new Win32Exception(); }
                if(i>=4096) throw new Exception("Device count bound");
                var id=new StringBuilder(4096); uint needed;
                if(!SetupDiGetDeviceInstanceIdW(set,ref info,id,4096,out needed))throw new Win32Exception();
                devices.Add(id.ToString(),new SortedDictionary<string,object>{
                    {"hardware",Property(set,ref info,1,7)}, {"compatible",Property(set,ref info,2,7)},
                    {"driver",Property(set,ref info,9,1)}, {"class",info.Class.ToString()}});
            }
        } finally { if(!SetupDiDestroyDeviceInfoList(set))throw new Win32Exception(); }
        var deviceRows=new List<object>();
        foreach(var d in devices) { var row=(SortedDictionary<string,object>)d.Value; row.Add("instance",d.Key); deviceRows.Add(row); }
        var trust=new SortedDictionary<string,object>();
        foreach(StoreLocation loc in new[]{StoreLocation.LocalMachine,StoreLocation.CurrentUser})
        foreach(string name in new[]{"Root","CA","TrustedPublisher","TrustedPeople","AuthRoot","Disallowed"}) {
            using(var store=new X509Store(name,loc)) {
                try { store.Open(OpenFlags.ReadOnly|OpenFlags.OpenExistingOnly); }
                catch(System.Security.Cryptography.CryptographicException ex) {
                    if(ex.HResult!=unchecked((int)0x80092004) && ex.HResult!=unchecked((int)0x80070002))throw;
                    trust.Add(loc+"/"+name,"absent");continue;
                }
                var certs=new List<string>();
                foreach(var cert in store.Certificates) { using(cert)certs.Add(cert.Thumbprint); }
                if(certs.Count>10000)throw new Exception("Trust count bound");
                certs.Sort(StringComparer.Ordinal); trust.Add(loc+"/"+name,certs.ToArray());
            }
        }
        var monitors=new SortedDictionary<string,object>(); Exception callbackError=null;
        Callback cb=(IntPtr h,IntPtr dc,ref Rect r,IntPtr data)=>{
            try {
                if(monitors.Count>=32)throw new Exception("Monitor count bound");
                var m=new Monitor{Size=(uint)Marshal.SizeOf(typeof(Monitor))}; int scale;
                if(!GetMonitorInfoW(h,ref m))throw new Win32Exception();
                int hr=GetScaleFactorForMonitor(h,out scale); if(hr!=0)throw new Exception("Scale query HRESULT: "+hr);
                string key=m.Bounds.L+","+m.Bounds.T+","+m.Bounds.R+","+m.Bounds.B;
                monitors.Add(key,new int[]{m.Bounds.L,m.Bounds.T,m.Bounds.R,m.Bounds.B,scale,(int)m.Flags});
                return true;
            } catch(Exception ex){callbackError=ex;return false;}
        };
        bool ok=EnumDisplayMonitors(IntPtr.Zero,IntPtr.Zero,cb,IntPtr.Zero); GC.KeepAlive(cb);
        if(callbackError!=null)throw callbackError;
        if(!ok || monitors.Count==0)throw new Win32Exception();
        return new SortedDictionary<string,object>{{"devices",deviceRows},{"trust",trust},{"display",monitors}};
    }
}
'@
[StagingReadOnlyInventory]::Read() | ConvertTo-Json -Depth 10 -Compress
