param([string]$Helper)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Helper,[ref]$tokens,[ref]$errors)
if($errors.Count -gt 0){throw 'Helper parse failed'}
foreach($node in $ast.EndBlock.Statements){
 if($node -is [Management.Automation.Language.FunctionDefinitionAst]){. ([scriptblock]::Create($node.Extent.Text))}
}
# Exercise actual destination validation before replacing only pixel rendering.
$folder=Join-Path ([IO.Path]::GetTempPath()) ('verdiclip-frame-'+[Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($folder)|Out-Null
try {
 $payload=[pscustomobject]@{confirmed=$true;viewport=@(0,0,2,2);image_path=(Join-Path $folder 'frame.png')}
 $resolved=Resolve-DiscoveryFrameDestination $payload
 if($resolved -ne $payload.image_path -or [IO.File]::Exists($resolved)){throw 'Destination validation wrote pixels'}
 foreach($fault in @(
  {param($p)$p.confirmed='true'}, {param($p)$p.confirmed=$false},
  {param($p)$p.viewport=$null}, {param($p)$p.image_path=Join-Path $folder 'other.png'},
  {param($p)$p.image_path=Join-Path (Split-Path $folder) 'frame.png'}
 )) {
  $p=[pscustomobject]@{confirmed=$true;viewport=@(0,0,2,2);image_path=$payload.image_path}
  & $fault $p; $rejected=$false
  try{Resolve-DiscoveryFrameDestination $p|Out-Null}catch{$rejected=$true}
  if(-not $rejected){throw 'Invalid destination admitted'}
 }
 [IO.File]::WriteAllText($resolved,'owned-test-file')
 $rejected=$false;try{Resolve-DiscoveryFrameDestination $payload|Out-Null}catch{$rejected=$true}
 if(-not $rejected){throw 'Existing frame overwritten'}
}finally{[IO.Directory]::Delete($folder,$true)}
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
public static class DiscoveryFake {
 public static string Fault=""; public static List<string> Trace=new List<string>();
 public static void Hit(string stage){Trace.Add(stage);if(Array.IndexOf(Fault.Split(','),stage)>=0)throw new InvalidOperationException("private-provider-text:"+stage);}
}
public static class CaptureDesktop {
 public static bool Unoccluded(IntPtr h,int x,int y,int width,int height){DiscoveryFake.Hit("occlusion");return true;}
}
namespace VerdiClip.Automation {
public sealed class Rectangle {public bool IsEmpty=false;public double Left=-20,Top=30,Width=100,Height=80;public double Right{get{return Left+Width;}}public double Bottom{get{return Top+Height;}}}
public sealed class Pattern {public Pattern Current{get{return this;}}public bool VerticallyScrollable=true;public double VerticalScrollPercent=25,VerticalViewSize=20;public double ReadPosition(){DiscoveryFake.Hit("position");return VerticalScrollPercent;}public double ReadViewSize(){DiscoveryFake.Hit("view");return VerticalViewSize;}}
public sealed class Node {
 public int Index;public Node(int i){Index=i;}public Node Current{get{return this;}}
 public int ProcessId=42,ControlType=50014,Orientation=2;public bool IsOffscreen=false,IsEnabled=true;
 public Rectangle BoundingRectangle{get{DiscoveryFake.Hit("bounds");return new Rectangle();}}
 public string RuntimeIdentity{get{return Index.ToString();}}
 public Rectangle ReadBounds(){DiscoveryFake.Hit("bounds");return new Rectangle();}
 public string ReadRuntime(){DiscoveryFake.Hit("runtime");return RuntimeIdentity;}
 public int ReadControlType(){DiscoveryFake.Hit("control");return ControlType;}
 public int ReadOrientation(){DiscoveryFake.Hit("orientation");return Orientation;}
 public bool ReadOffscreen(){DiscoveryFake.Hit("offscreen");return IsOffscreen;}
 public bool ReadEnabled(){DiscoveryFake.Hit("enabled");return IsEnabled;}
 public int[] GetRuntimeId(){return new[]{Index};}
 public Pattern GetScroll(){return Index==2?new Pattern():null;}
}
public sealed class Resolver {
 public Node Root=new Node(1);public int ProcessId=42;
 public Node[] FindAncestors(double x,double y){DiscoveryFake.Hit("resolve");return new[]{new Node(3),new Node(2),Root};}
}
public sealed class DiscoverySession : IDisposable {
 public Resolver Resolver=new Resolver(); public long StartedTicks=30;
 public void Open(long h,bool diagnostics){DiscoveryFake.Hit("open");if(h!=100)throw new Exception("wrong handle");}
 public void Verify(long h){DiscoveryFake.Hit("verify");}
 public void Dispose(){
  var failures=new List<Exception>();
  try{DiscoveryFake.Hit("release");}catch(Exception e){failures.Add(e);}
  try{DiscoveryFake.Hit("dpi");}catch(Exception e){failures.Add(e);}
  if(failures.Count>0)throw new AggregateException(failures);
 }
}
}
'@
# The fake compilation uses no Windows assemblies or provider API. No screen I/O.
function Get-Process([int]$Id){
 if($Id -ne 42){throw 'Wrong process'}
 $p=[pscustomobject]@{Id=42}
 $p|Add-Member ScriptMethod Dispose {[DiscoveryFake]::Hit('process')}
 return $p
}
function Save-DiscoveryFrame($payload,$area){[DiscoveryFake]::Hit('sample');return $payload.image_path}
function Request([string]$action){
 $payload=[pscustomobject]@{window_handle=100;x=-1.5;y=2.5}
 if($action -ne 'inspect'){
  $payload=[pscustomobject]@{window_handle=100;reference=[pscustomobject]@{window_handle=100;process_id=42;started_ticks=30;runtime_id='2';bounds=@(-20.0,30.0,100.0,80.0);vertical_percent=0.0;view_size=20.0}}
 }
 if($action -eq 'sample'){
  $payload|Add-Member NoteProperty viewport @(-20,30,100,80)
  $payload|Add-Member NoteProperty confirmed $true
  $payload|Add-Member NoteProperty image_path 'owned-frame-path'
 }
 return [pscustomobject]@{request_id=('a'*32);action=$action;payload=$payload}
}
function Get-FailureLeaves($failureValue) {
 if($failureValue -is [AggregateException]){foreach($inner in $failureValue.InnerExceptions){Get-FailureLeaves $inner}}
 elseif($null -ne $failureValue.InnerException){Get-FailureLeaves $failureValue.InnerException}
 else{$failureValue.Message}
}
foreach($action in @('inspect','observe','sample')){
 [DiscoveryFake]::Fault='';[DiscoveryFake]::Trace.Clear()
 $result=Invoke-ReadOnlyDiscoveryRequest (Request $action)
 $trace=[DiscoveryFake]::Trace
 if($trace[$trace.Count-1] -ne 'dpi' -or $trace[$trace.Count-2] -ne 'release'){throw 'Result preceded retirement'}
 if($action -eq 'inspect'){
  if($result.nodes.Count -ne 3 -or $result.hit_identity -ne '100:42:30/3' -or $result.nodes[0].parent_identity -ne '100:42:30/2' -or $result.nodes[0].kind -ne 'vertical_scrollbar' -or $result.nodes[1].kind -ne 'content' -or $result.nodes[1].parent_identity -ne '100:42:30/1'){throw 'Inspect schema changed'}
 }else{
  if($result.reference.vertical_percent -ne 25 -or $result.reference.runtime_id -ne '2' -or $result.at_end){throw 'Observe rejected legitimate position change'}
 }
 if($action -eq 'sample'){
  if($result.image_path -ne 'owned-frame-path' -or $result.viewport.Count -ne 4 -or -not $trace.Contains('sample')){throw 'Sample schema changed'}
 }elseif($trace.Contains('sample')){throw 'Read-only action wrote pixels'}
 $faultCases=@('open','resolve','bounds','runtime','position','view','verify','process','release','dpi','resolve,process,release,dpi')
 if($action -ne 'inspect'){$faultCases+=@('offscreen','enabled')}else{$faultCases+=@('control','orientation')}
 foreach($fault in $faultCases){
  [DiscoveryFake]::Fault=$fault;[DiscoveryFake]::Trace.Clear();$result=$null;$failure=$null
  try{$result=Invoke-ReadOnlyDiscoveryRequest (Request $action)}catch{$failure=$_.Exception}
  if($null -eq $failure -or $null -ne $result){throw ('Failure returned result: action='+$action+';fault='+$fault)}
  if(-not [DiscoveryFake]::Trace.Contains('release') -or -not [DiscoveryFake]::Trace.Contains('dpi')){throw 'Independent cleanup skipped'}
  if($fault -eq 'resolve,process,release,dpi'){
   $leaves=@(Get-FailureLeaves $failure)
   $expected=@('resolve','process','release','dpi')|ForEach-Object{'private-provider-text:'+$_}
   if(($leaves -join ',') -ne ($expected -join ',')){throw 'Combined failure lost primary or cleanup exceptions'}
  }
 }
}
$faults=@(
 {param($r)$r.request_id=123}, {param($r)$r.action='SAMPLE'},
 {param($r)$r.payload.window_handle=$true}, {param($r)$r.payload.window_handle='100'},
 {param($r)$r.payload.reference.process_id=42.0}, {param($r)$r.payload.reference.started_ticks='30'},
 {param($r)$r.payload.reference.runtime_id='01'}, {param($r)$r.payload.reference.runtime_id='2147483648'},
 {param($r)$r.payload.reference.bounds=@(-20,30,$true,80)},
 {param($r)$r.payload.reference.view_size=[double]::NaN},
 {param($r)$r.payload.viewport=@(-20,30,100.5,80)}, {param($r)$r.payload.viewport=@(-20,30,100000,100000)},
 {param($r)$r.payload.confirmed='true'}, {param($r)$r.payload.confirmed=$false},
 {param($r)$r.payload.image_path=123}, {param($r)$r.payload.reference|Add-Member NoteProperty extra 1}
)
[DiscoveryFake]::Fault=''
foreach($fault in $faults){
 $r=Request 'sample'; & $fault $r; [DiscoveryFake]::Trace.Clear();$failed=$false
 try{Invoke-ReadOnlyDiscoveryRequest $r|Out-Null}catch{$failed=$true}
 if(-not $failed -or [DiscoveryFake]::Trace.Count -ne 0){throw 'Malformed request reached native session'}
}
foreach($value in @($true,'1',$null,[double]::PositiveInfinity)){
 $r=Request 'inspect';$r.payload.x=$value;[DiscoveryFake]::Trace.Clear();$failed=$false
 try{Invoke-ReadOnlyDiscoveryRequest $r|Out-Null}catch{$failed=$true}
 if(-not $failed -or [DiscoveryFake]::Trace.Count -ne 0){throw 'Invalid coordinate reached native session'}
}
[Console]::WriteLine('discovery-operation-policy-passed')
