# Corrective release scope

The first corrective release restores the capture implementation shipped in 0.12.1, including its native Qt mouse movement, region/window/fullscreen/repeat selection and original menu behavior. It retains tabs, capture destination settings, completed-capture editor activation, retained-document recovery, and independent startup, IPC, publishing and CI hardening.

Scrolling capture, broker-based menu preservation and native-pointer experiments are not available in this release. Historical design and evidence documents remain for provenance, not as descriptions of current commands or release qualification.

Menu capture will be redesigned and qualified separately. Automatic panel scrolling follows only after that separate release, without the earlier configuration dialog. No code injection into other applications is permitted.

The capture source is required to match commit `a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed` exactly. Local portable validation does not replace Windows capture, editor activation or release checks.
