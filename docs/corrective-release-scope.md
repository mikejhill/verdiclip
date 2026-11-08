# Historical corrective release scope (0.13.1)

This document records the completed corrective release, not the current feature
set. Menu capture was subsequently qualified and released in 0.13.2. Bounded
scrolling capture follows separately in 0.14.0; see the current
[capture instructions](../README.md#scrolling-capture) and
[scrolling qualification](design/simple-scrolling.md).

The first corrective release restores the capture implementation shipped in 0.12.1, including its native Qt mouse movement, region/window/fullscreen/repeat selection and original menu behavior. It retains tabs, capture destination settings, completed-capture editor activation, retained-document recovery, and independent startup, IPC, publishing and CI hardening.

Scrolling capture, broker-based menu preservation and native-pointer experiments are not available in this release. Historical design and evidence documents remain for provenance, not as descriptions of current commands or release qualification.

Menu capture will be redesigned and qualified separately. Automatic panel scrolling follows only after that separate release, without the earlier configuration dialog. No code injection into other applications is permitted.

The capture source is required to match commit `a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed` exactly. Local portable validation does not replace Windows capture, editor activation or release checks.
