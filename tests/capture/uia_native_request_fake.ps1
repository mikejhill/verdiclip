param([string]$Source)
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
namespace VerdiClip.Automation {
public sealed class Snapshot { public long Handle=17, StartedTicks=30; public int ProcessId=20; public string Runtime="1.2"; public double[] Bounds=new double[]{-20,30,100,80}; public double Position=20, ViewSize=25; }
public static class NoFocusScroll {
 public static int Calls; public static double Destination;
 public static Snapshot Mutate(long h,int p,long t,string r,double[] b,double pos,double view,double d,int[] area) {
  Calls++; Destination=d;
  if(h!=17 || p!=20 || t!=30 || r!="1.2" || pos!=0 || view!=25 || area[0]!=-20 || area[2]!=100) throw new Exception("changed scalar arguments");
  return new Snapshot();
 }
}}
'@
. $Source
function Request {
 return [pscustomobject]@{action='step';payload=[pscustomobject]@{
 window_handle=17;confirmed=$true;viewport=@(-20,30,100,80);restore_percent=0
 reference=[pscustomobject]@{window_handle=17;process_id=20;started_ticks=30;runtime_id='1.2';bounds=@(-20,30,100,80);vertical_percent=0;view_size=25}
 }}
}
$request=Request
$result=Invoke-NativeScrollRequest $request
if($result.reference.runtime_id -ne '1.2' -or $result.reference.vertical_percent -ne 20 -or $result.at_end -or [Math]::Abs([VerdiClip.Automation.NoFocusScroll]::Destination-16.6666666666667) -gt 0.0001){throw ('incorrect step/result: destination=' + [VerdiClip.Automation.NoFocusScroll]::Destination + ';reference=' + ($result | ConvertTo-Json -Compress -Depth 4))}
$request.action='restore'
Invoke-NativeScrollRequest $request | Out-Null
if([VerdiClip.Automation.NoFocusScroll]::Destination -ne 0){throw 'incorrect restore'}
$faults=@(
 {param($r) $r.action='inspect'},
 {param($r) $r.payload.confirmed=$false},
 {param($r) $r.payload.confirmed='true'},
 {param($r) $r.payload.window_handle=18},
 {param($r) $r.payload.reference.vertical_percent=[double]::NaN},
 {param($r) $r.payload.reference.view_size=100},
 {param($r) $r.payload.viewport=@(-20.5,30,100,80)},
 {param($r) $r.payload.viewport=@(-20,30,100)},
 {param($r) $r.action='restore';$r.payload.restore_percent=[double]::PositiveInfinity},
 {param($r) $r.payload.viewport=@(-20,30,100,[double]::MaxValue)}
)
foreach($fault in $faults){
 $request=Request; & $fault $request
 $prior=[VerdiClip.Automation.NoFocusScroll]::Calls;$rejected=$false
 try {Invoke-NativeScrollRequest $request|Out-Null} catch {$rejected=$true}
 if(-not $rejected -or [VerdiClip.Automation.NoFocusScroll]::Calls -ne $prior){throw 'unsafe request touched native mutator'}
}
[Console]::WriteLine('native-request-policy-passed')
