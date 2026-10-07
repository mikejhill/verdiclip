# Changelog

## [0.2.0](https://github.com/mikejhill/verdiclip/compare/v0.1.0...v0.2.0) (2026-10-07)


### ⚠ BREAKING CHANGES

* rebuild VerdiClip around an immutable document model ([#1](https://github.com/mikejhill/verdiclip/issues/1))

### Features

* add CLI support and comprehensive test suite ([c38ec38](https://github.com/mikejhill/verdiclip/commit/c38ec3820a1f1d031224796047afb3f17d3e2ee5))
* add window picker, fix hotkey threading, and improve editor UX ([9f2fb51](https://github.com/mikejhill/verdiclip/commit/9f2fb51a37f856a2ec2f1d841e7daf1d4a92ccce))
* **editor:** add resize handles, context toolbar, and font rendering ([8fc8575](https://github.com/mikejhill/verdiclip/commit/8fc8575aa5e8a4c2ce759021b62be358d381eae1))
* **editor:** fix 8 editor bugs — arrowhead scaling, copy-paste, handles, Esc, crop, toolbar ([e8f3923](https://github.com/mikejhill/verdiclip/commit/e8f39233a4789cdc56ae7e211be39d5d7133e27a))
* **editor:** fix sharp corners, arrow handles, 45-degree snap, counter resize ([3c5e279](https://github.com/mikejhill/verdiclip/commit/3c5e2794c3d3203231d1b4ca4fe4686ad3dcb03f))
* rebuild VerdiClip around an immutable document model ([#1](https://github.com/mikejhill/verdiclip/issues/1)) ([f1f43a9](https://github.com/mikejhill/verdiclip/commit/f1f43a9fb24457fc0ad52552b5044e003e133bbf))


### Bug Fixes

* **editor:** pointy arrows, arrow-key nudge, DPI scaling, Esc from toolbar ([1ad0fe3](https://github.com/mikejhill/verdiclip/commit/1ad0fe357900ad54ffc37f8bfa27f2de9c7542c8))
* freeze screen during capture by cropping from frozen background ([71cfc49](https://github.com/mikejhill/verdiclip/commit/71cfc4905fee634c437a00123fcd6c08cb672653))
* marshal hotkey callbacks to Qt GUI thread ([9f6ac27](https://github.com/mikejhill/verdiclip/commit/9f6ac2728a3dd268fad68c74e2300d339831295f))
* preserve arrows during crop and fix ArrowItem bounding rect ([91d6a1f](https://github.com/mikejhill/verdiclip/commit/91d6a1f23e62607c44f0fa5bc485959217d05658))
* prevent SIGINT timer garbage collection blocking shutdown ([dda7c24](https://github.com/mikejhill/verdiclip/commit/dda7c246d8a8574f6ead86c0c2717689a68a75d2))
* resolve 10 editor bugs in select tool, zoom, obfuscate, and undo crop ([25a37a4](https://github.com/mikejhill/verdiclip/commit/25a37a475fa6f492a0ec94a1a14aee10dc81c97d))
* resolve 12 editor and settings bugs ([7883594](https://github.com/mikejhill/verdiclip/commit/78835943794d663ceac78ec0bdf46d31fe259435))
* resolve 12 reported bugs across screenshots, editor, and configuration ([e4ea708](https://github.com/mikejhill/verdiclip/commit/e4ea708fbe684c1840cb47244b6863b9bc67787e))
* resolve 5 editor bugs in drag, obfuscation bounds, counter edit, and crop undo ([e5b43bd](https://github.com/mikejhill/verdiclip/commit/e5b43bd3d0f1f544320a01530eaf751b64d1e363))
* resolve 8 editor bugs across viewport, drag, counters, obfuscation, zoom, and crop ([fa5cd1c](https://github.com/mikejhill/verdiclip/commit/fa5cd1cea2cb546b2f02c137880108aed88f1f04))
* resolve 9 issues across zoom, undo, boundaries, obfuscation, crop, and capture ([5d17fd9](https://github.com/mikejhill/verdiclip/commit/5d17fd9f7fd8bb2e35790fff9db4ba1d573b3193))
* resolve extremely slow SIGINT shutdown ([f9888f9](https://github.com/mikejhill/verdiclip/commit/f9888f97f1b30ef91c13a7709acfd11ce954afc8))


### Documentation

* add CLI usage, missing shortcuts, and window_picker references ([c7e637d](https://github.com/mikejhill/verdiclip/commit/c7e637dab410f4f5e993b7e38ac467b83e071bfd))
* reorganize docs/ into topic-based subdirectories ([b560884](https://github.com/mikejhill/verdiclip/commit/b56088452fc2041c77881910b562e090c9e07529))
