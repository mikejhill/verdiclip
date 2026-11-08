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
change between frames. Testing uniformity permits only the first and last eight
rows as corner exceptions. Measurement stops at the first unchanged or textured
column. More than two changed uniform columns on either side rejects that
entire strip, rather than masking its first two columns. These are conservative
physical-pixel bounds taken from this diagnostic, not a general border detector
or a DPI-scaled content crop.

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
frames. Their row indices must agree. A qualifying row must lie within the first
four physical rows, be exactly uniform except for at most eight columns at each
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

## DPI evidence and current bounds

The inspected native pair's `target.json` records geometry but **no DPI**. Its
actual window DPI and monitor scale are unknown. The two changed physical
columns therefore do not establish 100% scaling, and no specific native scale
is qualified by this replay. Future diagnostic records use `GetDpiForWindow`;
that value must be labelled **window DPI**, not independently measured monitor
scale. Complete native sequence qualification is still pending.

Synthetic tests explicitly model a nominal 2px focus outline and 3px border at
these scales. They are generated pixel fixtures, not Windows/Edge DPI tests:

- 100% model: 2 changed outline columns and a 3-column border. Accepted, with
  whole-image equality against unmodified original-frame slices.
- 125% model: 3 changed outline columns (including an illustrative half-coverage
  third column) and a 4-column border. Rejected under the current production cap.
- 150% model: 3 changed outline columns and a 5-column border. Rejected.
- 200% model: 4 changed outline columns and a 6-column border. Rejected.

Each synthetic scale also has a textured-edge negative. These cases deliberately
document a compatibility gap rather than silently widen production allowances.
They do not predict exact Edge antialiasing, browser zoom, theme, or fractional
placement. Current bounds remain two changed registration columns, three side
perimeter columns, four top rows, and eight-pixel corner allowances, all physical.

A future DPI-aware allowance needs a separate, verified capture-geometry contract:

1. Carry the retained target's physical rectangle, owning window and monitor
   identity, effective DPI evidence and the DPI-awareness context. Window DPI
   alone, an assumed 96-DPI baseline, or a user-reported percentage is insufficient
   to claim independently verified selected-monitor scale.
2. Qualify complete native sequences at the intended 125%, 150% and 200% settings,
   recording window DPI, independently checked display scaling, browser zoom and
   measured outline/border/corner extents. Do not infer logical widths from this
   existing unknown-DPI pair.
3. Derive only a small hard cap from that verified scale and measured baseline.
   The pixels must still independently satisfy uniformity and minimum-width
   inference. Count partially covered changed columns; do not add color tolerance,
   resample output, or excuse textured edges and broader headers/sidebars.
4. Revalidate geometry, monitor and DPI throughout capture. A scale/monitor change
   or unsupported mixed-monitor target must stop capture, not widen a live mask.
   Unknown or unverified scale retains the current conservative behavior.

That contract is planning only. This patch neither reads DPI nor changes its
production pixel caps from synthetic scale evidence.

## Quality limits

Even narrow solid content can be visually indistinguishable from an outline.
These measured conditions cannot prove semantic completeness, including for a
thin solid header whose pixels resemble a perimeter. Repeated scrollbar
thumbs in earlier accepted prose captures are also a separate known output
quality limitation. This patch does not remove or repair them. Restoration
concerns scroll position only: the native panel's focus outline can remain after
the original position is restored. This is not release qualification or a claim
that Explorer, every browser panel, or the user's exact app geometry works.
