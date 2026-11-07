"""Portable source contracts, not native WPF execution or pixel qualification."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2] / "scripts" / "panel_fixture" / "Program.cs"
).read_text(encoding="utf-8")


def section(start: str, end: str) -> str:
    """Read a bounded authored C# section without executing foreign code."""
    return SOURCE.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def test_ordinary_mode_snapshot_bytes_are_preserved() -> None:
    """The old writer remains byte-for-byte intact after the opt-in dispatch."""
    signature = "    private void Sample()"
    original = signature + section(signature, "    private Grid ObserveLayout")
    dispatch = "        if (ObserveOnly) { SampleObservation(); return; }\n"
    assert original.count(dispatch) == 1
    original = original.replace(dispatch, "")
    # SHA-256 of the pre-existing method, including field order and JSON writer.
    assert hashlib.sha256(original.encode()).hexdigest() == (
        "3e4de0c0b4145a532f8567dfc6da40b76f5a4d605edab5b9144bc7e2a69ec6ef"
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "internal sealed class Rows",
            "internal sealed class Fixture",
            "2fe1c36a9516048c50d184213f2b4c2ee77e643294f44aa1423f301167a506ea",
        ),
        (
            "    private static ScrollViewer MakeViewer",
            "    private void Sample()",
            "a976f976ba8639d770c2a12dfd7f24076b1bd7163eafdd888ff6bb8f9b102fb4",
        ),
    ],
)
def test_existing_rows_and_viewer_contracts_are_preserved(
    start: str, end: str, expected: str
) -> None:
    """Rows, viewer defaults, physical bounds, and state keep their prior code."""
    original = start + section(start, end)
    assert hashlib.sha256(original.encode()).hexdigest() == expected


def test_observation_is_explicit_opt_in_and_preserves_sibling() -> None:
    """Only the new mode changes target horizontal behavior and timer period."""
    assert 'private bool ObserveOnly => mode == "observe-only";' in SOURCE
    constructor = section("    internal Fixture(", "    private static ScrollViewer")
    assert 'target = MakeViewer("owned-target", mode == "borderless" ? 0 : 2);' in (
        constructor
    )
    assert 'target.Height = mode == "nested" ? 270 : 430;' in constructor
    assert 'target.Width = mode == "nested" ? 390 : 510;' in constructor
    assert 'sibling = MakeViewer("owned-sibling", 2);' in constructor
    assert "sibling.Width = 190; sibling.Height = 430;" in constructor
    assert "target.ScrollToVerticalOffset(119);" in constructor
    assert "target.ScrollToHorizontalOffset(ObserveOnly ? 0 : 47);" in constructor
    assert "sibling.ScrollToVerticalOffset(93);" in constructor
    assert "sibling.ScrollToHorizontalOffset(27);" in constructor
    assert "TimeSpan.FromMilliseconds(ObserveOnly ? 100 : 50)" in constructor
    branch = section("        if (ObserveOnly)\n", "        timer =")
    assert branch == (
        "        {\n"
        "            using var process = Process.GetCurrentProcess();\n"
        "            processCreated100ns = "
        "process.StartTime.ToUniversalTime().ToFileTimeUtc();\n"
        "            target.HorizontalScrollBarVisibility = "
        "ScrollBarVisibility.Disabled;\n"
        "            observeRoot = ObserveLayout(grid);\n"
        "            Content = observeRoot;\n"
        "        }\n"
        "        else { Content = grid; }\n"
    )


def test_marker_layout_is_outside_both_panels_and_not_searched() -> None:
    """Fixed four-DIP markers sit off-panel in a new margin-free root layer."""
    assert "var grid = new Grid { Margin = new Thickness(24) };" in SOURCE
    layout = section("    private Grid ObserveLayout", "    private void AddMarker")
    assert "var root = new Grid();" in layout
    assert "root.Children.Add(panels);" in layout
    assert "var layer = new Canvas { IsHitTestVisible = false };" in layout
    assert "root.Children.Add(layer);" in layout
    authored = re.findall(
        r'AddMarker\(layer, "([a-z-]+)", (\d+), (\d+), '
        r"Color.FromRgb\((\d+), (\d+), (\d+)\)\);",
        layout,
    )
    assert len(authored) == 4
    assert len({entry[0] for entry in authored}) == 4
    assert len({entry[3:] for entry in authored}) == 4
    # Existing grid margin, first column width, and panel dimensions determine R.
    panels = [(24, 24, 534, 454), (574, 24, 764, 454)]
    for _name, x_text, y_text, *rgb in authored:
        x, y = int(x_text), int(y_text)
        assert 0 <= x <= 820
        assert 0 <= y <= 550
        assert all(0 <= int(channel) <= 255 for channel in rgb)
        assert all(
            x + 4 <= left or x >= right or y + 4 <= top or y >= bottom
            for left, top, right, bottom in panels
        )
    marker = section("    private void AddMarker", "    private void SampleObservation")
    assert "Width = 4, Height = 4, Background = new SolidColorBrush(color)," in marker
    assert "Canvas.SetLeft(element, left);" in marker
    assert "Canvas.SetTop(element, top);" in marker
    assert "new[] { (int)color.R, color.G, color.B }" in marker


def test_fresh_observation_records_are_bounded_and_atomically_published() -> None:
    """Every tick writes fresh evidence even when the owned state is unchanged."""
    sample = section("    private void SampleObservation", "    [STAThread]")
    assert "previous" not in sample
    assert "if (!initialized) return;" in sample
    bound = (
        "if (sequence >= 256) throw new "
        'InvalidOperationException("Snapshot bound exceeded");'
    )
    write = "File.WriteAllText(temporary, JsonSerializer.Serialize(record));"
    publish = "File.Move(temporary, final);"
    assert bound in sample
    assert 'string final = Path.Combine(output, $"{sequence++:D4}.json");' in sample
    assert 'string temporary = final + ".tmp";' in sample
    assert sample.index(bound) < sample.index(write) < sample.index(publish)
    assert "File.Append" not in sample
    assert "File.Delete" not in sample


@pytest.mark.parametrize(
    "field",
    [
        'schema = 1, kind = "owned-wpf", mode, pid = Environment.ProcessId,',
        "alive = true,",
        "process_created_100ns = processCreated100ns,",
        "root_hwnd = new WindowInteropHelper(this).Handle.ToInt64(),",
        "authored_layout_rect = Bounds(observeRoot",
        "rect = Bounds(target), content_rect = contentRect,",
        "owned_process_ids = new[] { Environment.ProcessId },",
        "dpi_x = dpi.PixelsPerInchX, dpi_y = dpi.PixelsPerInchY,",
        "row_height_dip = Rows.RowHeight, row_count = Rows.Count,",
        "{ id = marker.Id, rect = Bounds(marker.Element), "
        "rgb = marker.Rgb }).ToArray(),",
        "state = new { target = State(target), sibling = State(sibling), "
        "outer = (object?)null },",
        "milliseconds = elapsed.ElapsedMilliseconds, sequence",
    ],
)
def test_observation_telemetry_uses_authored_state(field: str) -> None:
    """Fixture facts are distinct from independently measured outer HWND bounds."""
    sample = section("    private void SampleObservation", "    [STAThread]")
    assert field in sample
    assert "root_bounds" not in sample
    assert "root_client_rect" not in sample
    assert "DllImport" not in SOURCE


def test_only_observation_honors_fixed_stop_signal_without_acting_on_content() -> None:
    """An owned stop file and lifetime bound close only this opt-in fixture mode."""
    sample = section("    private void SampleObservation", "    [STAThread]")
    lifetime = "if (elapsed.Elapsed > TimeSpan.FromSeconds(70)) { Close(); return; }"
    assert lifetime in sample
    stop = 'if (File.Exists(Path.Combine(output, "stop"))) { Close(); return; }'
    assert stop in sample
    assert SOURCE.count(stop) == 1
    assert sample.index(stop) < sample.index("if (!initialized) return;")
    assert not any(
        forbidden in sample
        for forbidden in (
            "File.Read",
            "Process.Start",
            "ScrollTo",
            "SendMessage",
            "SendInput",
            "SetWindow",
            "Kill(",
        )
    )


def test_creation_time_is_frozen_once_only_in_observe_mode() -> None:
    """A constructor-time lifetime identifier cannot drift with repeated telemetry."""
    assert "private readonly long processCreated100ns;" in SOURCE
    branch = section("        if (ObserveOnly)\n", "        timer =")
    assert "using var process = Process.GetCurrentProcess();" in branch
    assert "process.StartTime.ToUniversalTime().ToFileTimeUtc();" in branch
    assert SOURCE.count("Process.GetCurrentProcess()") == 1
    assert SOURCE.count(".StartTime") == 1
    sample = section("    private void SampleObservation", "    [STAThread]")
    assert "process_created_100ns = processCreated100ns," in sample
    assert "GetCurrentProcess" not in sample
