// Observe the owned fixture's normal handler. Never write cursor state or input.
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public sealed class OwnedCursorReadiness : NativeWindow {
    [StructLayout(LayoutKind.Sequential)]
    private struct Point { public int X, Y; }
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(Point point);
    private readonly int targetX, targetY;
    private bool armed;
    public int Count { get; private set; }
    public bool Handled { get; private set; }

    public OwnedCursorReadiness(IntPtr window, int x, int y) {
        Point initial;
        if (window == IntPtr.Zero || !GetCursorPos(out initial) ||
            (initial.X == x && initial.Y == y))
            throw new InvalidOperationException("Owned cursor setup target unavailable");
        targetX=x; targetY=y;
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
        Handled=message.Result != IntPtr.Zero;
        armed=false;
    }
}
