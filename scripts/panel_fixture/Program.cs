using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

// An owned, precompiled fixture only. No code runs inside another application.
internal sealed class Rows : FrameworkElement
{
    internal const int Count = 47;
    internal const int RowHeight = 31;
    internal Rows() { Width = 760; Height = Count * RowHeight; }
    internal static Color ColorAt(int row) => Color.FromRgb(
        (byte)(32 + row * 53 % 190), (byte)(32 + row * 29 % 190),
        (byte)(32 + row * 71 % 190));
    protected override void OnRender(DrawingContext dc)
    {
        for (int i = 0; i < Count; i++)
        {
            dc.DrawRectangle(new SolidColorBrush(ColorAt(i)), null,
                new Rect(0, i * RowHeight, Width, RowHeight));
            dc.DrawRectangle(Brushes.White, null,
                new Rect(0, i * RowHeight, Width, 1));
            var text = new FormattedText($"Owned row {i:D2}", CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface("Segoe UI"), 15,
                Brushes.Black, VisualTreeHelper.GetDpi(this).PixelsPerDip);
            dc.DrawText(text, new Point(60, i * RowHeight + 5));
        }
    }
}

internal sealed class Fixture : Window
{
    private readonly string output;
    private readonly string mode;
    private readonly ScrollViewer target;
    private readonly ScrollViewer sibling;
    private readonly ScrollViewer? outer;
    private readonly DispatcherTimer timer;
    private readonly Stopwatch elapsed = Stopwatch.StartNew();
    private int sequence;
    private string previous = "";
    private bool initialized;
    private readonly Grid? observeRoot;
    private readonly long processCreated100ns;
    private readonly List<Marker> markers = new();
    private bool ObserveOnly => mode == "observe-only";
    private sealed record Marker(string Id, Border Element, int[] Rgb);

    internal Fixture(string output, string mode)
    {
        this.output = output;
        Directory.CreateDirectory(output);
        this.mode = mode;
        Title = "Owned VerdiClip panel feasibility fixture";
        Width = 900; Height = 640; Left = 40; Top = 40;
        ResizeMode = ResizeMode.NoResize;
        Background = Brushes.LightGray;
        var grid = new Grid { Margin = new Thickness(24) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(550) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(210) });
        target = MakeViewer("owned-target", mode == "borderless" ? 0 : 2);
        target.Height = mode == "nested" ? 270 : 430;
        target.Width = mode == "nested" ? 390 : 510;
        target.Content = new Rows();
        sibling = MakeViewer("owned-sibling", 2);
        sibling.Width = 190; sibling.Height = 430; sibling.Content = new Rows();
        if (mode == "nested")
        {
            outer = MakeViewer("owned-outer", 2);
            outer.Width = 510; outer.Height = 430;
            var stack = new StackPanel { Width = 800 };
            stack.Children.Add(new Border { Height = 24, Background = Brushes.Orange });
            target.HorizontalAlignment = HorizontalAlignment.Left;
            stack.Children.Add(target);
            stack.Children.Add(new Rows());
            outer.Content = stack;
            grid.Children.Add(outer);
        }
        else { grid.Children.Add(target); }
        Grid.SetColumn(sibling, 1);
        grid.Children.Add(sibling);
        if (ObserveOnly)
        {
            using var process = Process.GetCurrentProcess();
            processCreated100ns = process.StartTime.ToUniversalTime().ToFileTimeUtc();
            target.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled;
            observeRoot = ObserveLayout(grid);
            Content = observeRoot;
        }
        else { Content = grid; }
        timer = new DispatcherTimer
            { Interval = TimeSpan.FromMilliseconds(ObserveOnly ? 100 : 50) };
        timer.Tick += (_, _) => Sample();
        ContentRendered += (_, _) =>
        {
            if (ObserveOnly) ObserveDiagnostic.Record("content-rendered");
            target.ScrollToVerticalOffset(119);
            target.ScrollToHorizontalOffset(ObserveOnly ? 0 : 47);
            sibling.ScrollToVerticalOffset(93);
            sibling.ScrollToHorizontalOffset(27);
            Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, () =>
            {
                if (ObserveOnly) ObserveDiagnostic.Record("application-idle");
                initialized = true;
                Sample();
                timer.Start();
            });
        };
    }

    private static ScrollViewer MakeViewer(string id, double border)
    {
        var viewer = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Visible,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Visible,
            CanContentScroll = false, BorderThickness = new Thickness(border),
            BorderBrush = Brushes.Magenta, Padding = new Thickness(0),
            HorizontalAlignment = HorizontalAlignment.Left,
            VerticalAlignment = VerticalAlignment.Top
        };
        AutomationProperties.SetAutomationId(viewer, id);
        return viewer;
    }

    private static double[] Bounds(FrameworkElement element)
    {
        Point first = element.PointToScreen(new Point(0, 0));
        Point last = element.PointToScreen(new Point(element.ActualWidth, element.ActualHeight));
        return new[] { first.X, first.Y, last.X, last.Y };
    }

    private static object State(ScrollViewer viewer) => new
    {
        vertical = viewer.VerticalOffset, horizontal = viewer.HorizontalOffset,
        viewport_height = viewer.ViewportHeight, viewport_width = viewer.ViewportWidth,
        extent_height = viewer.ExtentHeight, extent_width = viewer.ExtentWidth
    };

    private void Sample()
    {
        if (ObserveOnly) { SampleObservation(); return; }
        if (elapsed.Elapsed > TimeSpan.FromSeconds(70)) { Close(); return; }
        if (!initialized) return;
        var presenter = target.Template.FindName("PART_ScrollContentPresenter", target)
            as ScrollContentPresenter ?? throw new InvalidOperationException("No presenter");
        var viewerRect = Bounds(target);
        var contentRect = Bounds(presenter);
        var state = new { target = State(target), sibling = State(sibling),
            outer = outer is null ? null : State(outer) };
        string serialized = JsonSerializer.Serialize(state);
        if (serialized == previous) return;
        previous = serialized;
        if (sequence >= 256) throw new InvalidOperationException("Snapshot bound exceeded");
        var dpi = VisualTreeHelper.GetDpi(this);
        var record = new
        {
            schema = 1, kind = "owned-wpf", mode, pid = Environment.ProcessId,
            root_hwnd = new WindowInteropHelper(this).Handle.ToInt64(),
            rect = viewerRect, content_rect = contentRect,
            click = new[] { (contentRect[0] + contentRect[2]) / 2,
                (contentRect[1] + contentRect[3]) / 2 },
            owned_process_ids = new[] { Environment.ProcessId },
            dpi_x = dpi.PixelsPerInchX, dpi_y = dpi.PixelsPerInchY,
            row_height_dip = Rows.RowHeight, row_count = Rows.Count,
            state, milliseconds = elapsed.ElapsedMilliseconds, sequence
        };
        // Immutable, closed-before-published snapshots avoid Windows sharing races.
        string final = Path.Combine(output, $"{sequence++:D4}.json");
        string temporary = final + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(record));
        File.Move(temporary, final);
    }

    private Grid ObserveLayout(Grid panels)
    {
        var root = new Grid();
        root.Children.Add(panels);
        var layer = new Canvas { IsHitTestVisible = false };
        // Authored positions are outside both panels. The harness samples only
        // these reported rectangles; it must never search desktop pixels.
        AddMarker(layer, "top-left", 8, 8, Color.FromRgb(11, 241, 227));
        AddMarker(layer, "top-right", 812, 8, Color.FromRgb(247, 181, 13));
        AddMarker(layer, "bottom-left", 8, 544, Color.FromRgb(149, 17, 239));
        AddMarker(layer, "bottom-right", 812, 544, Color.FromRgb(19, 239, 73));
        root.Children.Add(layer);
        return root;
    }

    private void AddMarker(Canvas layer, string id, double left, double top, Color color)
    {
        var element = new Border
        {
            Width = 4, Height = 4, Background = new SolidColorBrush(color),
            SnapsToDevicePixels = true
        };
        Canvas.SetLeft(element, left);
        Canvas.SetTop(element, top);
        layer.Children.Add(element);
        markers.Add(new Marker(id, element, new[] { (int)color.R, color.G, color.B }));
    }

    private void SampleObservation()
    {
        if (elapsed.Elapsed > TimeSpan.FromSeconds(70)) { Close(); return; }
        // The only control is an owned, fixed stop file, never a command payload.
        if (File.Exists(Path.Combine(output, "stop"))) { Close(); return; }
        if (!initialized) return;
        if (sequence >= 256) throw new InvalidOperationException("Snapshot bound exceeded");
        var presenter = target.Template.FindName("PART_ScrollContentPresenter", target)
            as ScrollContentPresenter ?? throw new InvalidOperationException("No presenter");
        var contentRect = Bounds(presenter);
        var dpi = VisualTreeHelper.GetDpi(this);
        var record = new
        {
            schema = 1, kind = "owned-wpf", mode, pid = Environment.ProcessId,
            alive = true,
            process_created_100ns = processCreated100ns,
            root_hwnd = new WindowInteropHelper(this).Handle.ToInt64(),
            // This is the authored layout/client rectangle, NOT an outer HWND
            // rectangle. The harness obtains and pins the latter independently.
            authored_layout_rect = Bounds(observeRoot
                ?? throw new InvalidOperationException("No observation layout")),
            rect = Bounds(target), content_rect = contentRect,
            click = new[] { (contentRect[0] + contentRect[2]) / 2,
                (contentRect[1] + contentRect[3]) / 2 },
            owned_process_ids = new[] { Environment.ProcessId },
            dpi_x = dpi.PixelsPerInchX, dpi_y = dpi.PixelsPerInchY,
            row_height_dip = Rows.RowHeight, row_count = Rows.Count,
            markers = markers.Select(marker => new
                { id = marker.Id, rect = Bounds(marker.Element), rgb = marker.Rgb }).ToArray(),
            state = new { target = State(target), sibling = State(sibling), outer = (object?)null },
            milliseconds = elapsed.ElapsedMilliseconds, sequence
        };
        // Publish every sample, including unchanged state, so freshness can be
        // checked independently. No telemetry file is reopened or overwritten.
        string final = Path.Combine(output, $"{sequence++:D4}.json");
        string temporary = final + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(record));
        File.Move(temporary, final);
        if (sequence == 1) ObserveDiagnostic.Record("first-sample-published");
    }

    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Length != 2) throw new ArgumentException("state-path mode required");
        if (args[1] != "observe-only")
        {
            var app = new Application();
            app.Run(new Fixture(Path.GetFullPath(args[0]), args[1]));
            return;
        }
        ObserveDiagnostic.Configure(Path.GetFullPath(args[0]));
        ObserveDiagnostic.Record("main-entry");
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            ObserveDiagnostic.Record("domain-unhandled", e.ExceptionObject as Exception);
        try
        {
            ObserveDiagnostic.Record("application-create");
            var app = new Application();
            app.DispatcherUnhandledException += (_, e) =>
                ObserveDiagnostic.Record("dispatcher-unhandled", e.Exception);
            ObserveDiagnostic.Record("constructor-entry");
            var fixture = new Fixture(Path.GetFullPath(args[0]), args[1]);
            ObserveDiagnostic.Record("constructor-returned");
            app.Run(fixture);
        }
        catch (Exception error)
        {
            ObserveDiagnostic.Record("main-exception", error);
            throw;
        }
    }
}


// Best-effort evidence only: never handles an application exception or retries.
// Fixed names, one publication per phase, <=4096 bytes each; not telemetry.
internal static class ObserveDiagnostic
{
    private static string? directory;
    private static readonly object gate = new();
    private static readonly HashSet<string> recorded = new();
    private static readonly HashSet<string> phases = new()
    {
        "main-entry", "application-create", "constructor-entry",
        "constructor-returned", "content-rendered", "application-idle",
        "first-sample-published", "domain-unhandled", "dispatcher-unhandled",
        "main-exception"
    };

    internal static void Configure(string states)
    {
        directory = Path.Combine(states, "diagnostics");
    }

    internal static void Record(string phase, Exception? error = null)
    {
        try
        {
            lock (gate)
            {
                if (directory is null || !phases.Contains(phase) || !recorded.Add(phase)) return;
                Directory.CreateDirectory(directory);
                string? type = error?.GetType().FullName;
                string? message = error?.Message;
                if (type?.Length > 128) type = type[..128];
                if (message?.Length > 256) message = message[..256];
                byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(new
                {
                    schema = 1, scope = "owned-observe-only-startup", phase,
                    pid = Environment.ProcessId, monotonic_ticks = Stopwatch.GetTimestamp(),
                    monotonic_frequency = Stopwatch.Frequency,
                    exception_type = type, exception_message = message,
                    exception_hresult = error?.HResult
                });
                if (bytes.Length > 4096) return;
                string final = Path.Combine(directory, phase + ".json");
                string temporary = final + ".tmp";
                File.WriteAllBytes(temporary, bytes);
                File.Move(temporary, final);
            }
        }
        catch (Exception)
        {
            // Persistence failure must not replace the original exception or
            // change fixture execution. Missing evidence remains unqualified.
        }
    }
}
