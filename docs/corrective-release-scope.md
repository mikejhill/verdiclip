# Corrective release scope

The first corrective release restores the capture implementation shipped in 0.12.1, including its native Qt mouse movement, region/window/fullscreen/repeat selection and original menu behavior. It retains tabs, capture destination settings, completed-capture editor activation, retained-document recovery, and independent startup, IPC, publishing and CI hardening.

Scrolling capture, broker-based menu preservation and native-pointer experiments are not available in this release. Historical design and evidence documents remain for provenance, not as descriptions of current commands or release qualification.

Menu capture will be redesigned and qualified separately. Automatic panel scrolling follows only after that separate release, without the earlier configuration dialog. No code injection into other applications is permitted.

The capture source is required to match commit `a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed` exactly. Local portable validation does not replace Windows capture, editor activation or release checks.

## Supported Qt runtime

The minimum PySide6 Essentials runtime is **6.11.3**, with matching Shiboken6;
the lockfile pins both to 6.11.3. Current compatibility profiles test 6.11.3 and
6.12.0. Historical 6.11.2 receipts and design checkpoints remain unchanged and
do not describe the current minimum.

Qt's [official 6.11.3 release notes](https://doc.qt.io/qtforpython-6/release_notes/pyside6_release_notes.html)
list PYSIDE-3463, the incorrect ownership of the object returned by
`QObject.thread()`. The [upstream fix](https://github.com/qtproject/pyside-pyside-setup/commit/75f647a9dca089ed284429c0cace08b8d98b0742)
disables the erroneous return-value ownership heuristic. The declaration is
absent in the 6.11.2 source tag and present in both 6.11.3 and 6.12.0.

A controlled isolated comparison using official registry wheels kept the reduced
test sequence and non-Qt dependencies unchanged: 6.11.2 crashed, while 6.11.3
passed and preserved the main-thread wrapper after receiver destruction. This
supports the dependency floor; it does not rewrite earlier failures or replace
fresh Windows CI and native qualification of the new locked runtime.
