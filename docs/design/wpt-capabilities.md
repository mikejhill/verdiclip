# Installed-tool feasibility only

This draft-only workflow inspects the existing Microsoft-signed WPT binaries on
one disposable GitHub-hosted Windows runner. It runs only fixed help commands,
using preinstalled Python and PowerShell. It does not install software, start a
trace, launch a fixture, download symbols, attach a debugger, suspend a thread,
change machine settings or upload artifacts. The existing native tests and their
20-second fixture verdict remain untouched.

Each help process has a ten-second deadline and two-second owned cleanup bound.
Output is file-backed to avoid Windows pipe-reader locks, rejected when polling observes more than 256 KiB per stream (output may
overshoot between polls), and confined to a fresh private directory. Only signed fixed installed
paths with non-reparse ancestry are used. Raw help and errors are removed before
the fixed boolean/version receipt is printed. Cleanup failure prevents a clean
receipt; it never triggers a capture fallback.

Help can establish installed tools and advertised flags. It cannot verify an
export schema, resolved managed WPF frames, stack grouping, or capture-time
ownership isolation. Those fields remain explicitly false even if every command
succeeds. An advertised PID filter may apply only during export, not collection.
Standard WPR kernel switch-stack collection is not authorized by this preflight.
No synthetic trace is allowed to fill these gaps.

Official references:
- https://learn.microsoft.com/en-us/windows-hardware/test/wpt/stack
- https://learn.microsoft.com/en-us/windows-hardware/test/wpt/exporter
- https://learn.microsoft.com/en-us/windows-hardware/test/wpt/using-clr-40-ngen-pdb-support
