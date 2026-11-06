// SDK-ordered COM interface prefixes, Windows SDK 10.0.22621 UIAutomationClient.h.
// Unused slots are never invoked. No SetFocus/activation/input API is exposed.
using System;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Globalization;
namespace VerdiClip.Automation {
[StructLayout(LayoutKind.Sequential)] internal struct NativePoint { public int X, Y; }
[ComImport, Guid("34723aff-0c9d-49d0-9896-7ab52df8cd8a"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAutomation2 {
    void Reserved_03_CompareElements(); // slot 3
    void Reserved_04_CompareRuntimeIds(); // slot 4
    void Reserved_05_GetRootElement(); // slot 5
    void ElementFromHandle(IntPtr handle, out IElement element); // slot 6
    void ElementFromPoint(NativePoint point, out IElement element); // slot 7
    void Reserved_08_GetFocusedElement(); // slot 8
    void Reserved_09_GetRootElementBuildCache(); // slot 9
    void Reserved_10_ElementFromHandleBuildCache(); // slot 10
    void Reserved_11_ElementFromPointBuildCache(); // slot 11
    void Reserved_12_GetFocusedElementBuildCache(); // slot 12
    void Reserved_13_CreateTreeWalker(); // slot 13
    void Reserved_14_get_ControlViewWalker(); // slot 14
    void Reserved_15_get_ContentViewWalker(); // slot 15
    void get_RawViewWalker(out IWalker walker); // slot 16
    void Reserved_17_get_RawViewCondition(); // slot 17
    void Reserved_18_get_ControlViewCondition(); // slot 18
    void Reserved_19_get_ContentViewCondition(); // slot 19
    void Reserved_20_CreateCacheRequest(); // slot 20
    void Reserved_21_CreateTrueCondition(); // slot 21
    void Reserved_22_CreateFalseCondition(); // slot 22
    void Reserved_23_CreatePropertyCondition(); // slot 23
    void Reserved_24_CreatePropertyConditionEx(); // slot 24
    void Reserved_25_CreateAndCondition(); // slot 25
    void Reserved_26_CreateAndConditionFromArray(); // slot 26
    void Reserved_27_CreateAndConditionFromNativeArray(); // slot 27
    void Reserved_28_CreateOrCondition(); // slot 28
    void Reserved_29_CreateOrConditionFromArray(); // slot 29
    void Reserved_30_CreateOrConditionFromNativeArray(); // slot 30
    void Reserved_31_CreateNotCondition(); // slot 31
    void Reserved_32_AddAutomationEventHandler(); // slot 32
    void Reserved_33_RemoveAutomationEventHandler(); // slot 33
    void Reserved_34_AddPropertyChangedEventHandlerNativeArray(); // slot 34
    void Reserved_35_AddPropertyChangedEventHandler(); // slot 35
    void Reserved_36_RemovePropertyChangedEventHandler(); // slot 36
    void Reserved_37_AddStructureChangedEventHandler(); // slot 37
    void Reserved_38_RemoveStructureChangedEventHandler(); // slot 38
    void Reserved_39_AddFocusChangedEventHandler(); // slot 39
    void Reserved_40_RemoveFocusChangedEventHandler(); // slot 40
    void Reserved_41_RemoveAllEventHandlers(); // slot 41
    void Reserved_42_IntNativeArrayToSafeArray(); // slot 42
    void Reserved_43_IntSafeArrayToNativeArray(); // slot 43
    void Reserved_44_RectToVariant(); // slot 44
    void Reserved_45_VariantToRect(); // slot 45
    void Reserved_46_SafeArrayToRectNativeArray(); // slot 46
    void Reserved_47_CreateProxyFactoryEntry(); // slot 47
    void Reserved_48_get_ProxyFactoryMapping(); // slot 48
    void Reserved_49_GetPropertyProgrammaticName(); // slot 49
    void Reserved_50_GetPatternProgrammaticName(); // slot 50
    void Reserved_51_PollForPotentialSupportedPatterns(); // slot 51
    void Reserved_52_PollForPotentialSupportedProperties(); // slot 52
    void Reserved_53_CheckNotSupported(); // slot 53
    void Reserved_54_get_ReservedNotSupportedValue(); // slot 54
    void Reserved_55_get_ReservedMixedAttributeValue(); // slot 55
    void Reserved_56_ElementFromIAccessible(); // slot 56
    void Reserved_57_ElementFromIAccessibleBuildCache(); // slot 57
    void get_AutoSetFocus(out int enabled); // slot 58
    void put_AutoSetFocus(int enabled); // slot 59
}
[ComImport, Guid("d22108aa-8ac5-49a5-837b-37bbb3d7591e"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IElement {
    void Reserved_03_SetFocus(); // slot 3
    void GetRuntimeId([MarshalAs(UnmanagedType.SafeArray, SafeArraySubType=VarEnum.VT_I4)] out int[] runtime); // slot 4
    void Reserved_05_FindFirst(); // slot 5
    void Reserved_06_FindAll(); // slot 6
    void Reserved_07_FindFirstBuildCache(); // slot 7
    void Reserved_08_FindAllBuildCache(); // slot 8
    void Reserved_09_BuildUpdatedCache(); // slot 9
    void GetCurrentPropertyValue(int property, [MarshalAs(UnmanagedType.Struct)] out object value); // slot 10
    void Reserved_11_GetCurrentPropertyValueEx(); // slot 11
    void Reserved_12_GetCachedPropertyValue(); // slot 12
    void Reserved_13_GetCachedPropertyValueEx(); // slot 13
    void GetCurrentPatternAs(int pattern, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IScroll value); // slot 14
}
[ComImport, Guid("4042c624-389c-4afc-a630-9df854a541fc"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IWalker {
    void GetParentElement(IElement element, out IElement parent); // slot 3
}
[ComImport, Guid("88f4d42a-e881-459d-a77c-73bbbb7e02dc"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IScroll {
    void Reserved_03_Scroll(); // slot 3
    void SetScrollPercent(double horizontal, double vertical); // slot 4
    void Reserved_05_get_CurrentHorizontalScrollPercent(); // slot 5
    void get_CurrentVerticalScrollPercent(out double value); // slot 6
    void Reserved_07_get_CurrentHorizontalViewSize(); // slot 7
    void get_CurrentVerticalViewSize(out double value); // slot 8
    void Reserved_09_get_CurrentHorizontallyScrollable(); // slot 9
    void get_CurrentVerticallyScrollable(out int value); // slot 10
}
// Shared, pure proof for one transient initial hit. Never creates an identity.
public static class TransientHitPolicy {
    private static void Require(bool value) { if(!value)throw new InvalidOperationException("unresolved_hit_unverified"); }
    private static bool Finite(double value) { return !Double.IsNaN(value) && !Double.IsInfinity(value); }
    public static bool Missing(int[] runtime) { return runtime==null || runtime.Length==0; }
    public static double[] Validate(object control,object scroll,object raw,int x,int y) {
        Require(control is int && (int)control>=50000 && (int)control<=50040 && (int)control!=50014 && (int)control!=50032);
        Require(scroll is bool && !(bool)scroll);
        double[] bounds=raw as double[]; Require(bounds!=null && bounds.Length==4);
        foreach(double value in bounds)Require(Finite(value));
        Require(bounds[2]>0 && bounds[3]>0);
        double left=Math.Ceiling(bounds[0]),top=Math.Ceiling(bounds[1]);
        double right=Math.Floor(bounds[0]+bounds[2]),bottom=Math.Floor(bounds[1]+bounds[3]);
        Require(Finite(right) && Finite(bottom) && left>=Int32.MinValue && top>=Int32.MinValue && right<=Int32.MaxValue && bottom<=Int32.MaxValue);
        Require(right>left && bottom>top && right-left<=Int32.MaxValue && bottom-top<=Int32.MaxValue);
        Require(x>=left && x<right && y>=top && y<bottom);
        return new double[]{left,top,right-left,bottom-top};
    }
}
// Pure mutation traversal policy; delegates own COM acquisitions before return.
public static class NativeIdentityPath {
    public static T Resolve<T>(T first,string rootId,string selectedId,int pid,long handle,
        Func<T,object> process,Func<T,int[]> runtime,Func<T,T> parent,Func<T,object> window,
        Action<T> proveMissing,Action guard) where T:class {
        if(String.IsNullOrEmpty(rootId) || String.IsNullOrEmpty(selectedId) || pid<=0 || handle<=0)
            throw new InvalidOperationException("native_scroll_identity_unavailable");
        T target=null; var seen=new HashSet<string>(StringComparer.Ordinal);
        T element=first;
        for(int depth=0;depth<32 && element!=null;depth++) {
            guard(); object actualPid=process(element); guard();
            if(!(actualPid is int) || (int)actualPid!=pid)throw new InvalidOperationException("native_scroll_cross_process");
            int[] raw=runtime(element); guard();
            if(depth==0 && TransientHitPolicy.Missing(raw)) { proveMissing(element); guard(); }
            else {
                if(raw==null || raw.Length==0 || raw.Length>64)throw new InvalidOperationException("native_scroll_runtime_unavailable");
                var parts=new string[raw.Length];
                for(int i=0;i<raw.Length;i++)parts[i]=raw[i].ToString(CultureInfo.InvariantCulture);
                string id=String.Join(".",parts);
                if(!seen.Add(id))throw new InvalidOperationException("native_scroll_ancestry_changed");
                if(id==selectedId)target=element;
                if(id==rootId) {
                    object actualHandle=window(element); guard();
                    if(!(actualHandle is int) || (long)(int)actualHandle!=handle || target==null)
                        throw new InvalidOperationException("native_scroll_root_changed");
                    return target;
                }
            }
            element=parent(element); guard();
        }
        throw new InvalidOperationException("native_scroll_ancestry_changed");
    }
}
}
