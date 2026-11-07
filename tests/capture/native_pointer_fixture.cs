// Observe the owned fixture's normal handler. Never write cursor state or input.
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public sealed class OwnedCursorReadiness : NativeWindow {
    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X, Y; }
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(Point point);
    [StructLayout(LayoutKind.Sequential)]
    private struct CursorInfo {
        public int Size, Flags;
        public IntPtr Cursor;
        public Point Position;
    }
    [DllImport("user32.dll")] private static extern bool GetCursorInfo(ref CursorInfo info);
    private readonly IntPtr expectedCursor;
    private readonly int targetX, targetY;
    private bool armed;
    public int Count { get; private set; }
    public bool HandlerCompleted { get; private set; }
    public bool AssignedCursorObserved { get; private set; }

    public OwnedCursorReadiness(IntPtr window, int x, int y, IntPtr assignedCursor) {
        Point initial;
        if (window == IntPtr.Zero || assignedCursor == IntPtr.Zero || !GetCursorPos(out initial) ||
            (initial.X == x && initial.Y == y))
            throw new InvalidOperationException("Owned cursor setup target unavailable");
        targetX=x; targetY=y; expectedCursor=assignedCursor;
        AssignHandle(window);
        armed=true;
    }

    protected override void WndProc(ref Message message) {
        bool candidate = armed && message.Msg == 0x20 && message.WParam == Handle &&
            (message.LParam.ToInt64() & 0xffff) == 1;
        base.WndProc(ref message); // Preserve the existing managed/native handler.
        if (!candidate || !armed) return;
        Point point;
        if (!GetCursorPos(out point) || point.X != targetX || point.Y != targetY ||
            WindowFromPoint(point) != Handle) return;
        // Immutable first completed client handler at the armed target. This is
        // readiness, not cryptographic attribution to a particular input packet.
        Count=1;
        armed=false; // Latch even a failed first observation; no later favorable event.
        HandlerCompleted=true;
        // WinForms WmSetCursor assigns Cursor.Current without setting Result.
        // Completion is not a truthy LRESULT. Read only the actual assigned
        // fixture cursor once; independent baseline appearance remains mandatory.
        CursorInfo info = new CursorInfo();
        info.Size=Marshal.SizeOf(typeof(CursorInfo));
        AssignedCursorObserved=GetCursorInfo(ref info) && info.Flags == 1 &&
            info.Cursor == expectedCursor && info.Position.X == targetX &&
            info.Position.Y == targetY;
    }
}
