using System;
using System.Collections.Generic;
using VerdiClip.Automation;
public static class InspectClockTests {
    static void Require(bool value) { if(!value) throw new Exception("clock_control_failed"); }
    public static string Run() {
        var lines=new List<string>(); long clock=100;
        var trace=new InspectClockTrace(()=>clock,1000000,lines.Add);
        trace.Mark("armed"); clock=125; trace.Mark("root_call");
        trace.Mark("root_call"); trace.Mark("private unknown label");
        Require(lines.Count==2 && lines[1]=="VERDICLIP_INSPECT_CLOCK=root_call:25");
        clock=99; trace.Mark("root_return"); Require(lines[2].EndsWith(":-1"));
        clock=200; trace.Mark("root_owned"); Require(lines[3].EndsWith(":-1"));
        foreach(string label in InspectClockTrace.Labels) trace.Mark(label);
        Require(lines.Count==14);
        lines.Clear(); int count=0;
        trace=new InspectClockTrace(()=>{if(count++>0)throw new Exception();return 0;},1,lines.Add);
        trace.Mark("armed"); Require(lines[0].EndsWith(":-1"));
        lines.Clear(); trace=new InspectClockTrace(()=>0,0,lines.Add);
        trace.Mark("armed"); Require(lines[0].EndsWith(":-1"));
        lines.Clear(); count=0;
        trace=new InspectClockTrace(()=>count++==0?0:long.MaxValue,1,lines.Add);
        trace.Mark("armed"); Require(lines[0].EndsWith(":-1"));
        trace=new InspectClockTrace(()=>0,1,_=>{throw new Exception();}); trace.Mark("armed");
        bool propagated=false;
        try { new InspectClockTrace(()=>{throw new OperationCanceledException();},1,lines.Add); }
        catch(OperationCanceledException) { propagated=true; } Require(propagated);
        propagated=false; trace=new InspectClockTrace(()=>0,1,_=>{throw new OperationCanceledException();});
        try { trace.Mark("armed"); } catch(OperationCanceledException) { propagated=true; } Require(propagated);
        return "passed";
    }
}
