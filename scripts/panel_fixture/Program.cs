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
        Content = grid;
        timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(50) };
        timer.Tick += (_, _) => Sample();
        ContentRendered += (_, _) =>
        {
            target.ScrollToVerticalOffset(119);
            target.ScrollToHorizontalOffset(47);
            sibling.ScrollToVerticalOffset(93);
            sibling.ScrollToHorizontalOffset(27);
            Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, () =>
            {
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

    [STAThread]
    private static void Main(string[] args)
    {
        if (args.Length != 2) throw new ArgumentException("state-path mode required");
        var app = new Application();
        app.Run(new Fixture(Path.GetFullPath(args[0]), args[1]));
    }
}
