#Requires -Version 7.0
param([switch]$Child)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip') {
    throw 'This canary requires an explicit dispatch on standard hosted Windows x64.'
}

if (-not $Child) {
    # The child owns its window/class/GDI objects. A separate watchdog also bounds
    # a stuck native call. Killing this one child never targets another process.
    $stem = Join-Path $env:RUNNER_TEMP ('owned-window-' + [guid]::NewGuid().ToString('N'))
    $process = $null
    try {
        $arguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File',
            ('"' + $PSCommandPath + '"'), '-Child')
        $process = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -ArgumentList $arguments `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput ($stem + '.out') `
            -RedirectStandardError ($stem + '.err')
        if (-not $process.WaitForExit(30000)) { throw 'Owned-window child exceeded 30 seconds.' }
        $output = Get-Content -Raw -LiteralPath ($stem + '.out')
        if ($output) {
            Write-Output $output
            if ($env:GITHUB_STEP_SUMMARY) {
                @('### Bounded owned native window canary', '', '```json', $output, '```',
                    'Capability only; no input, activation, Qt, broker, UIA, hotkey or parity proof.') `
                    -join "`n" | Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8
            }
        }
        if ($process.ExitCode -ne 0) { throw 'Owned-window child failed; inspect its bounded receipt.' }
    } finally {
        if ($null -ne $process) {
            if (-not $process.HasExited) {
                $process.Kill($true)
                if (-not $process.WaitForExit(5000)) { throw 'Owned-window child cleanup timed out.' }
            }
            $process.Dispose()
        }
    }
    exit 0
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

public static class OwnedWindowCanary {
    [StructLayout(LayoutKind.Sequential)] struct Point { public int X,Y; }
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left,Top,Right,Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct Message {
        public IntPtr Window; public uint Id; public UIntPtr WParam; public IntPtr LParam;
        public uint Time; public Point Position; public uint Private;
    }
    [StructLayout(LayoutKind.Sequential)] struct Paint {
        public IntPtr DC; public int Erase; public Rect Bounds; public int Restore,Update;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst=32)] public byte[] Reserved;
    }
    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    delegate IntPtr Procedure(IntPtr window,uint message,UIntPtr wparam,IntPtr lparam);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct WindowClass {
        public uint Style; public Procedure Proc; public int ClassExtra,WindowExtra;
        public IntPtr Instance,Icon,Cursor,Background;
        public string Menu,Name;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandleW(string name);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern ushort RegisterClassW(ref WindowClass cls);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool UnregisterClassW(string name,IntPtr instance);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CreateWindowExW(uint ex,string cls,string title,uint style,
        int x,int y,int width,int height,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr DefWindowProcW(IntPtr w,uint m,UIntPtr a,IntPtr b);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr w);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr w);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr w);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr w,IntPtr after,
        int x,int y,int width,int height,uint flags);
    [DllImport("user32.dll")] static extern bool UpdateWindow(IntPtr w);
    [DllImport("user32.dll")] static extern bool InvalidateRect(IntPtr w,IntPtr r,bool erase);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Point p);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr w);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr w,IntPtr dc);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr w,out Rect r);
    [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr w,ref Point p);
    [DllImport("user32.dll")] static extern bool SystemParametersInfoW(uint action,uint param,out Rect r,uint flags);
    [DllImport("user32.dll")] static extern bool PeekMessageW(out Message msg,IntPtr w,uint first,uint last,uint remove);
    [DllImport("user32.dll")] static extern IntPtr DispatchMessageW(ref Message msg);
    [DllImport("user32.dll")] static extern uint MsgWaitForMultipleObjectsEx(uint count,IntPtr handles,uint ms,uint mask,uint flags);
    [DllImport("user32.dll")] static extern IntPtr BeginPaint(IntPtr w,out Paint paint);
    [DllImport("user32.dll")] static extern bool EndPaint(IntPtr w,ref Paint paint);
    [DllImport("user32.dll")] static extern int FillRect(IntPtr dc,ref Rect r,IntPtr brush);
    [DllImport("gdi32.dll")] static extern IntPtr CreateSolidBrush(uint color);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr obj);
    [DllImport("gdi32.dll")] static extern uint GetPixel(IntPtr dc,int x,int y);
    [DllImport("dwmapi.dll")] static extern int DwmFlush();

    static readonly uint[][] Colors = { new uint[]{0x0000ff,0x00ff00,0xff0000},
        new uint[]{0x00ffff,0xffff00,0xff00ff} };
    static int phase,paintCount;
    static string callbackError;
    static bool gdiReleased=true;
    static IntPtr OnMessage(IntPtr w,uint m,UIntPtr a,IntPtr b) {
        if (m!=15) return DefWindowProcW(w,m,a,b); // WM_PAINT; no input handlers.
        Paint paint;
        IntPtr dc=BeginPaint(w,out paint);
        try {
            Rect client;
            if(dc==IntPtr.Zero || !GetClientRect(w,out client)) throw new Exception("paint_dc");
            for(int i=0;i<3;i++) {
                Rect band=new Rect{Left=client.Right*i/3,Right=client.Right*(i+1)/3,Bottom=client.Bottom};
                IntPtr brush=CreateSolidBrush(Colors[phase][i]);
                if(brush==IntPtr.Zero) throw new Exception("brush_create");
                try { if(FillRect(dc,ref band,brush)==0) throw new Exception("fill_rect"); }
                finally { if(!DeleteObject(brush)) gdiReleased=false; }
            }
            paintCount++;
        } catch(Exception) { callbackError="paint_failed"; }
        finally { if(!EndPaint(w,ref paint)) callbackError="end_paint_failed"; }
        return IntPtr.Zero;
    }
    static bool Admit(IntPtr expected,IntPtr observed) {
        return expected!=IntPtr.Zero && expected==observed && IsWindow(expected);
    }
    static void Require(bool condition,string reason) {
        if(!condition) throw new Exception(reason);
    }
    public static Dictionary<string,object> Run() {
        var result=new Dictionary<string,object>();
        var errors=new List<string>();
        IntPtr window=IntPtr.Zero,instance=GetModuleHandleW(null);
        string name="VerdiClipOwnedCanary"+Guid.NewGuid().ToString("N");
        Procedure procedure=OnMessage;
        bool registered=false,destroyed=false,unregistered=false;
        int phases=0,samples=0;
        var timer=Stopwatch.StartNew();
        try {
            IntPtr foreground=GetForegroundWindow();
            Require(Admit(foreground,foreground),"no_foreground");
            Rect work;
            Require(SystemParametersInfoW(48,0,out work,0),"work_area_query"); // SPI_GETWORKAREA.
            Require(work.Right-work.Left>=240 && work.Bottom-work.Top>=180,"work_area_small");
            var cls=new WindowClass{Proc=procedure,Instance=instance,Name=name};
            Require(RegisterClassW(ref cls)!=0,"register_class"); registered=true;
            // TOPMOST | TOOLWINDOW | NOACTIVATE. Only this VM's owned popup is shown.
            window=CreateWindowExW(0x08000088,name,"VerdiClip synthetic canary",0x80000000,
                work.Left+(work.Right-work.Left-120)/2,work.Top+(work.Bottom-work.Top-90)/2,
                120,90,IntPtr.Zero,IntPtr.Zero,instance,IntPtr.Zero);
            Require(window!=IntPtr.Zero,"create_window");
            result["windows_created"]=1;
            // SHOWWINDOW | NOACTIVATE | NOMOVE | NOSIZE. Unlike the first
            // ShowWindow call, this cannot inherit the child's hidden startup mode.
            Require(SetWindowPos(window,new IntPtr(-1),0,0,0,0,0x0053),"show_window");
            Require(IsWindowVisible(window),"not_visible");
            for(phase=0;phase<2;phase++) {
                Require(InvalidateRect(window,IntPtr.Zero,false),"invalidate");
                UpdateWindow(window);
                bool matched=false;
                while(timer.ElapsedMilliseconds<8000) {
                    Message msg;
                    while(PeekMessageW(out msg,IntPtr.Zero,0,0,1)) DispatchMessageW(ref msg);
                    Require(callbackError==null,callbackError ?? "callback");
                    Require(Admit(foreground,GetForegroundWindow()),"foreground_changed");
                    Require(!Admit(window,GetForegroundWindow()),"nonforeground_admission_failed");
                    Require(DwmFlush()==0,"dwm_flush");
                    Rect client;
                    Require(GetClientRect(window,out client),"client_rect");
                    bool equal=true;
                    // Precheck three owned fixture coordinates; never publish pixel values.
                    IntPtr dc=GetDC(IntPtr.Zero);
                    Require(dc!=IntPtr.Zero,"screen_dc");
                    try {
                        for(int i=0;i<3;i++) {
                            var p=new Point{X=client.Right*(2*i+1)/6,Y=client.Bottom/2};
                            Require(ClientToScreen(window,ref p),"client_mapping");
                            Require(WindowFromPoint(p)==window,"owned_pixel_occluded");
                            uint color=GetPixel(dc,p.X,p.Y);
                            Require(WindowFromPoint(p)==window,"owned_pixel_occluded_after_read");
                            equal &= color==Colors[phase][i];
                        }
                    } finally { if(ReleaseDC(IntPtr.Zero,dc)!=1) gdiReleased=false; }
                    Require(timer.ElapsedMilliseconds<8000,"pixel_deadline");
                    if(equal) { matched=true; samples+=3; break; }
                    MsgWaitForMultipleObjectsEx(0,IntPtr.Zero,10,0x04ff,4);
                }
                Require(matched,"pixel_deadline"); phases++;
            }
            Require(Admit(foreground,GetForegroundWindow()),"foreground_changed_at_completion");
            result["foreground_preserved"]=true;
            result["borrowed_foreground_identity_admitted"]=true;
            result["nonforeground_owned_window_rejected"]=true;
        } catch(Exception ex) { errors.Add(ex.Message); }
        finally {
            if(window!=IntPtr.Zero) destroyed=DestroyWindow(window) && !IsWindow(window);
            if(registered) unregistered=UnregisterClassW(name,instance);
            GC.KeepAlive(procedure);
        }
        result["phases_matched"]=phases; result["matched_samples"]=samples;
        result["paint_count"]=paintCount; result["elapsed_ms"]=timer.ElapsedMilliseconds;
        result["window_destroyed"]=destroyed; result["class_unregistered"]=unregistered;
        result["gdi_released"]=gdiReleased; result["errors"]=errors.ToArray();
        result["passed"]=errors.Count==0 && phases==2 && samples==6 && destroyed && unregistered && gdiReleased;
        return result;
    }
}
'@

$observed = [OwnedWindowCanary]::Run()
$report = [ordered]@{
    schema = 1
    kind = 'bounded-owned-native-window-canary'
    source_sha = $env:GITHUB_SHA
    runner_image = $env:ImageOS
    image_version = $env:ImageVersion
    os_version = [Environment]::OSVersion.Version.ToString()
    canary_passed = $observed.passed
    input_generated = $false
    activation_requested = $false
    hotkeys_registered = $false
    production_broker_verified = $false
    owned_foreground_admission_verified = $false
    qt_verified = $false
    parity_verified = $false
    multi_monitor_profile_verified = $false
    migration_adequacy_verified = $false
    observed = $observed
}
Write-Output ($report | ConvertTo-Json -Depth 5)
if (-not $observed.passed) { exit 2 }
