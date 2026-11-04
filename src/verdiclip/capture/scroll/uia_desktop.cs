using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CaptureDesktop {
    [StructLayout(LayoutKind.Sequential)] struct Rectangle { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetTopWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out Rectangle rectangle);
    [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder value, uint size, out uint needed);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr handle);
    public static bool Interactive() {
        var handle = OpenInputDesktop(0, false, 1);
        if (handle == IntPtr.Zero) return false;
        try {
            var name = new StringBuilder(256); uint needed;
            return GetUserObjectInformation(handle, 2, name, 512, out needed) && name.ToString() == "Default";
        } finally { CloseDesktop(handle); }
    }
    public static bool Unoccluded(IntPtr target, int x, int y, int width, int height) {
        // Refuse every overlapping visible top-level rectangle above target,
        // including transparent/tool/owned popup windows. Unknown is not clear.
        var window = GetTopWindow(IntPtr.Zero);
        for (int count = 0; count < 512 && window != IntPtr.Zero; count++) {
            if (window == target) return true;
            if (IsWindowVisible(window) && !IsIconic(window)) {
                Rectangle bounds;
                if (!GetWindowRect(window, out bounds)) return false;
                if ((long)bounds.Left < (long)x + width && bounds.Right > x &&
                    (long)bounds.Top < (long)y + height && bounds.Bottom > y) return false;
            }
            window = GetWindow(window, 2);
        }
        return false;
    }
}
