# Bounded scrolling edge registration

The native Edge bordered-panel diagnostic captured 771 × 417 pixel frames.
Scrolling by the UIA-reported 209 pixels changed the two outermost columns on
each side from gray (177, 181, 186) to black (16, 16, 16), uniformly over rows
8 through 408. The adjacent third columns remained gray. Full-width matching
found no displacement; excluding only those four columns from registration
gave one exact textured match: advance 209, new-frame band [93, 177).

Registration now tries every column first. Only absence of a match permits a
single retry. An ambiguous first result still fails immediately. Each side's
excluded width is measured independently from the contiguous changed outer
columns that are individually vertically uniform in both frames. The color may
change between frames. At the original 96-DPI allowance, testing uniformity
permits only the first and last eight rows as corner exceptions. Measurement
stops at the first unchanged or textured column. More than two changed uniform
columns on either side rejects that entire strip, rather than masking its first
two columns. Verified-DPI scaling of these hard bounds is described below; it
does not infer a content crop or replace the independent pixel measurements.

The retry still requires globally unique displacement and a textured exact band.
UIA displacement agreement, accumulated extent, consistent seam, and resource
limits remain unchanged. There is no vertical registration crop, width search,
tolerance matching, or removal of output columns.

## Bounded top comparison

Side registration alone exposes a second rejection in the native pair: the raw
top comparison finds 2,488 of 24,672 pixels different (10.08%). The focused panel
has two uniform black top rows and a third gray border row. All three differ
from the shifted document content.

The full-width top comparison still runs first at 5%. Only a failing comparison
may retry after identifying uniform top perimeter rows in **both** original
frames. Their row indices must agree. At the 96-DPI allowance, a qualifying row
must lie within the first four physical rows, be exactly uniform except for at
most eight columns at each
corner, and use a color present in vertically uniform strips at both side edges.
Side colors are measured only within three columns, including the native gray
border's unchanged third column. A color continuing uniformly into the fourth
column is excluded, rather than treating a wider solid sidebar as perimeter.
A qualifying top color in the fifth row rejects the entire top mask rather than
partially masking a taller header. No text recognition or color tolerance is used.

The retry omits only those measured top rows from the comparison, keeps every
remaining column, and applies the same 5% threshold to the **remaining** pixel
count. It does not remove these rows from the result or alter alignment. Native
rows [1, 2, 3] qualify, leaving 175 of 22,359 pixels different (0.783%). The exact
native pair then joins at output row 344, producing 771 × 626 pixels. Whole-image
equality verifies the original first-frame rows [0, 344) followed by second-frame
rows [135, 417), including every output edge and seam pixel. This is pair-only
replay evidence, not complete representative native qualification.

Stitching retains the original pixels above and below the chosen seam, including
outlines, corners and scrollbars. A complete synthetic sequence's whole-image
oracle checks every pixel over repeated joins and a short final movement.
Negative tests cover texture in or next to either edge, wider solid strips,
deviations just beyond corner allowances, inconsistent perimeter geometry,
ambiguous interiors, wrong displacement, taller solid headers, and fixed content
inside a real border. A boundary test also proves the unchanged 5% denominator
after omitting measured top rows.

## DPI evidence and bounded allowance

The original two-frame diagnostic recorded no DPI. A later complete native Edge
panel-click diagnostic records **window DPI 96**. Its independent composition
oracle verified every output row against captured native frames at UIA-derived
positions. Offline replay through the DPI-aware matcher produces exactly that
entire native result. This proves compatibility with those recorded
96-window-DPI pixels, not native execution of the new display guards.

Production now retains a small read-only display snapshot after discovering the
viewport: selected root HWND's window DPI, window and capture-thread awareness,
monitor handle, and monitor bounds. `MonitorFromWindow` and `MonitorFromRect`
must identify the same monitor, and its bounds must contain the **entire** UIA
viewport. A largest-intersection result alone is not enough. The snapshot is
re-read during every normal target check; changes stop capture before another
scroll write. No awareness context, display setting, window or UI is changed.

`GetDpiForWindow` permits a scaled allowance only when both the selected root and
capture thread report per-monitor awareness and the observed DPI is 96–192.
This is window-DPI evidence with verified same-monitor geometry, not an
independent display-scaling measurement. Missing APIs, unknown awareness, zero
DPI, or DPI outside this bounded range use the original conservative allowance.
Known monitor inconsistency, failed monitor-bounds reads and cross-monitor
viewports raise explicit capture errors; they never silently widen a mask.

For verified DPI, each physical cap is `ceil(original_bound * dpi / 96)`, never
`original_bound * ceil(dpi / 96)`. The resulting registration-column / side-column
/ top-row / corner-pixel caps are:

- 96 DPI: 2 / 3 / 4 / 8.
- 120 DPI: 3 / 4 / 5 / 10.
- 144 DPI: 3 / 5 / 6 / 12.
- 192 DPI: 4 / 6 / 8 / 16.

The pixels must still independently satisfy exact uniformity, minimal-width
measurement and global alignment. Partially covered changed columns count
toward the cap. No color tolerance, output scaling, or general mismatch increase
is introduced. The ordinary 5% top guard still applies to all remaining pixels.

Synthetic 100/125/150/200% fixtures now exercise the complete transaction with
whole-image equality and restoration, including an illustrative antialiased
third column at 125%. They also test one-too-wide solid strips, nonuniform
outer/antialiased columns, wider fixed sidebars, and texture immediately beyond
each scaled corner allowance. Unverified DPI still rejects the wider outlines.
At 125% and 150%, a different modeled fractional top placement that extends
beyond the proportional top cap deliberately remains unsupported. Separately
rounding inset and border sizes does not authorize adding another pixel.

These generated fixtures do not establish native browser rasterization at those
scales. Complete native qualification remains required at 125%, 150% and,
particularly, the actual 200% target, with recorded window DPI, independently
checked display scaling, browser zoom and measured perimeter geometry. Read-only
display-configuration observations are not a native capture pass. The 200%
target configuration remains unqualified until complete native evidence is
recorded for this change.

## Quality limits

Even narrow solid content can be visually indistinguishable from an outline.
These measured conditions cannot prove semantic completeness, including for a
thin solid header whose pixels resemble a perimeter. Repeated scrollbar
thumbs in earlier accepted prose captures are also a separate known output
quality limitation. This patch does not remove or repair them. Restoration
concerns scroll position only: the native panel's focus outline can remain after
the original position is restored. This is not release qualification or a claim
that Explorer, every browser panel, or the user's exact app geometry works.
