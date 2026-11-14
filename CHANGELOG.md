# Changelog

## [0.21.3](https://github.com/mikejhill/verdiclip/compare/v0.21.2...v0.21.3) (2026-11-14)


### Bug Fixes

* **scrolling:** no false estimated seam at the top of Claude panes ([#200](https://github.com/mikejhill/verdiclip/issues/200)) ([78db924](https://github.com/mikejhill/verdiclip/commit/78db924de5a8706599e99ac31745e1d5cf2de4df))

## [0.21.2](https://github.com/mikejhill/verdiclip/compare/v0.21.1...v0.21.2) (2026-11-14)


### Bug Fixes

* **cli:** usage errors show the subcommand's usage line ([#197](https://github.com/mikejhill/verdiclip/issues/197)) ([91be5b1](https://github.com/mikejhill/verdiclip/commit/91be5b1196e7bd83db4535d67621973bc9c2742c))

## [0.21.1](https://github.com/mikejhill/verdiclip/compare/v0.21.0...v0.21.1) (2026-11-14)


### Bug Fixes

* **scrolling:** re-grab the frame a step-back returns to ([#195](https://github.com/mikejhill/verdiclip/issues/195)) ([5d99678](https://github.com/mikejhill/verdiclip/commit/5d996782bf966f8ad52ffd3359a25f29e01a7d33))

## [0.21.0](https://github.com/mikejhill/verdiclip/compare/v0.20.0...v0.21.0) (2026-11-14)


### Features

* **ctl:** captures that wait for the user need --interactive ([#192](https://github.com/mikejhill/verdiclip/issues/192)) ([c943751](https://github.com/mikejhill/verdiclip/commit/c943751a5111bab9327ca12fd257c569c338ca59))

## [0.20.0](https://github.com/mikejhill/verdiclip/compare/v0.19.0...v0.20.0) (2026-11-14)


### Features

* **ctl:** capture, targets, export and close from the command line; help is the reference ([#186](https://github.com/mikejhill/verdiclip/issues/186)) ([7a35320](https://github.com/mikejhill/verdiclip/commit/7a3532015eae47e6e58d136180a2b4095b7da415))


### Bug Fixes

* move universal capture modifiers to their own Settings tab ([#188](https://github.com/mikejhill/verdiclip/issues/188)) ([f2dc6f6](https://github.com/mikejhill/verdiclip/commit/f2dc6f618ae60f1903bb29edf201856869bb4b8d))

## [0.19.0](https://github.com/mikejhill/verdiclip/compare/v0.18.4...v0.19.0) (2026-11-14)


### Features

* scrolling capture from the current position, and Esc keeps what was captured ([#183](https://github.com/mikejhill/verdiclip/issues/183)) ([7f6dd8e](https://github.com/mikejhill/verdiclip/commit/7f6dd8e475dbcde654d28c82488f55c0b88eb5f8))


### Bug Fixes

* **scrolling:** crop the scrollbar thumb's soft left edge ([#185](https://github.com/mikejhill/verdiclip/issues/185)) ([195e34c](https://github.com/mikejhill/verdiclip/commit/195e34c34012f1394ca4cc4c6e0a6652d5d5594b))

## [0.18.4](https://github.com/mikejhill/verdiclip/compare/v0.18.3...v0.18.4) (2026-11-14)


### Bug Fixes

* cover see-through and wide floating controls in scrolling captures ([#181](https://github.com/mikejhill/verdiclip/issues/181)) ([425bedc](https://github.com/mikejhill/verdiclip/commit/425bedcdb749d1e61bbfae3a73f50048f3793bf4))

## [0.18.3](https://github.com/mikejhill/verdiclip/compare/v0.18.2...v0.18.3) (2026-11-14)


### Bug Fixes

* leave out frozen scrollbars and floating overlays in scrolling captures ([#179](https://github.com/mikejhill/verdiclip/issues/179)) ([d3a15cd](https://github.com/mikejhill/verdiclip/commit/d3a15cd605ea4784543c17e45ac07345c04f805a))

## [0.18.2](https://github.com/mikejhill/verdiclip/compare/v0.18.1...v0.18.2) (2026-11-14)


### Bug Fixes

* confirm the end of a lagging panel and place unaligned frames by the step ([#177](https://github.com/mikejhill/verdiclip/issues/177)) ([5c333e4](https://github.com/mikejhill/verdiclip/commit/5c333e4e42139e96c4347f48d87dde84e1403202))

## [0.18.1](https://github.com/mikejhill/verdiclip/compare/v0.18.0...v0.18.1) (2026-11-14)


### Bug Fixes

* crop the scrollbar only when a thumb is seen, and refuse off-screen panels ([#175](https://github.com/mikejhill/verdiclip/issues/175)) ([ce47dfd](https://github.com/mikejhill/verdiclip/commit/ce47dfd55d307f89ffad50b369d858636b97e073))

## [0.18.0](https://github.com/mikejhill/verdiclip/compare/v0.17.0...v0.18.0) (2026-11-12)


### Features

* Ctrl+click a panel in region capture to capture it top to bottom ([#168](https://github.com/mikejhill/verdiclip/issues/168)) ([1e275b3](https://github.com/mikejhill/verdiclip/commit/1e275b39e85e0cd8ea2eab00832fac87b5860219))
* install a found update straight from the check window ([#166](https://github.com/mikejhill/verdiclip/issues/166)) ([2748289](https://github.com/mikejhill/verdiclip/commit/2748289a0d36dd492b202d273937e3016203c824)), closes [#124](https://github.com/mikejhill/verdiclip/issues/124)


### Bug Fixes

* guide the user through making VerdiClip the default image app ([#169](https://github.com/mikejhill/verdiclip/issues/169)) ([668a052](https://github.com/mikejhill/verdiclip/commit/668a052e269ee184e7ee212da7a266f9dad09840))
* keep tab labels from showing through the overflow arrows ([#165](https://github.com/mikejhill/verdiclip/issues/165)) ([f41a07c](https://github.com/mikejhill/verdiclip/commit/f41a07c026cebba7fe5ef85e53c60eb5bfbaec67)), closes [#120](https://github.com/mikejhill/verdiclip/issues/120)
* smooth fractional zoom levels and preload the editor for the first capture ([#171](https://github.com/mikejhill/verdiclip/issues/171)) ([f4d9af1](https://github.com/mikejhill/verdiclip/commit/f4d9af117b6b90ab6bec034eff64e86a28cbc68b))
* stop scrolling capture repeating content and showing the scrollbar ([#172](https://github.com/mikejhill/verdiclip/issues/172)) ([33fd8b2](https://github.com/mikejhill/verdiclip/commit/33fd8b26071a22f6886dcd898108cc67b270e645))

## [0.17.0](https://github.com/mikejhill/verdiclip/compare/v0.16.1...v0.17.0) (2026-11-12)


### Features

* **ctl:** open, save, copy and the set_image operation ([#159](https://github.com/mikejhill/verdiclip/issues/159)) ([9d90996](https://github.com/mikejhill/verdiclip/commit/9d909969e6b72f22084776423f9e5927046e2355))
* rebuild scrolling capture as a simple, tolerant top-to-bottom capture ([#158](https://github.com/mikejhill/verdiclip/issues/158)) ([70a8ec0](https://github.com/mikejhill/verdiclip/commit/70a8ec012d6cd781044a85f3e70c442efb77f9bd))


### Bug Fixes

* clear two CodeQL findings from the scrolling rebuild ([#164](https://github.com/mikejhill/verdiclip/issues/164)) ([d763136](https://github.com/mikejhill/verdiclip/commit/d7631362b92becbe2320e2def75690ec6736cc81))
* **ctl:** open returns the image it opened while a menu is open ([#160](https://github.com/mikejhill/verdiclip/issues/160)) ([b1dbab7](https://github.com/mikejhill/verdiclip/commit/b1dbab7a0febed74629acdbe7daf2d57ebeb0132))
* name ctl changes after the command line, not an assumed actor ([#156](https://github.com/mikejhill/verdiclip/issues/156)) ([66ec4bc](https://github.com/mikejhill/verdiclip/commit/66ec4bc49cd1c97fd24c20935bed4e0c90841d0c))

## [0.16.1](https://github.com/mikejhill/verdiclip/compare/v0.16.0...v0.16.1) (2026-11-12)


### Bug Fixes

* name automation steps without assuming an AI made them ([#154](https://github.com/mikejhill/verdiclip/issues/154)) ([019cf56](https://github.com/mikejhill/verdiclip/commit/019cf5675582852c1197056697e78427e1d987d4))
* reject bad apply options before editing and auto-fit AI text ([#153](https://github.com/mikejhill/verdiclip/issues/153)) ([a347923](https://github.com/mikejhill/verdiclip/commit/a3479231ab204665d70bb8333241131466407ea2))

## [0.16.0](https://github.com/mikejhill/verdiclip/compare/v0.15.0...v0.16.0) (2026-11-12)


### Features

* let AI tools edit open images through verdiclip ctl ([#150](https://github.com/mikejhill/verdiclip/issues/150)) ([b1bb04e](https://github.com/mikejhill/verdiclip/commit/b1bb04e289ddff833762dfea11bf476f96f0f4c9))
* ship a VerdiClip skill for AI coding agents ([#151](https://github.com/mikejhill/verdiclip/issues/151)) ([cdab24c](https://github.com/mikejhill/verdiclip/commit/cdab24c981388b6807542bb7b428451f77e2f8c7))

## [0.15.0](https://github.com/mikejhill/verdiclip/compare/v0.14.2...v0.15.0) (2026-11-11)


### Features

* let AI tools read open images through verdiclip ctl ([#146](https://github.com/mikejhill/verdiclip/issues/146)) ([f031949](https://github.com/mikejhill/verdiclip/commit/f031949b9b3f4db3860a388eb832a02b12a39e73))


### Bug Fixes

* hide scrolling capture until its rewrite lands ([#147](https://github.com/mikejhill/verdiclip/issues/147)) ([16e15b2](https://github.com/mikejhill/verdiclip/commit/16e15b286144d7e50eaac8133893b53a5bb6ec06))

## [0.14.2](https://github.com/mikejhill/verdiclip/compare/v0.14.1...v0.14.2) (2026-11-11)


### Bug Fixes

* acquire unambiguous scrolling frames ([c2ffae1](https://github.com/mikejhill/verdiclip/commit/c2ffae173786a07e2d8d74b94b85db78100a606d))
* qualify scrolling capture through registered native entry ([3670593](https://github.com/mikejhill/verdiclip/commit/367059390b337002a6e0aad95f5210db2b8263c3))
* repair scrolling in real applications ([11298c6](https://github.com/mikejhill/verdiclip/commit/11298c665a97774841b7dde57df727129f1fda6d))
* stabilize Settings startup and bounded scrolling recovery ([4712ae6](https://github.com/mikejhill/verdiclip/commit/4712ae663078f773c1b45bf33c5b83e3d71df26b))

## [0.14.1](https://github.com/mikejhill/verdiclip/compare/v0.14.0...v0.14.1) (2026-11-09)


### Bug Fixes

* restore reliable bounded scrolling capture in Explorer and Edge ([357dec2](https://github.com/mikejhill/verdiclip/commit/357dec2c9de4f647c187d06fe725e10b56ca94bd))

## [0.14.0](https://github.com/mikejhill/verdiclip/compare/v0.13.3...v0.14.0) (2026-11-08)


### Features

* integrate bounded scrolling capture ([#127](https://github.com/mikejhill/verdiclip/issues/127)) ([452987c](https://github.com/mikejhill/verdiclip/commit/452987ca053cd5684031654a0f6dd9adf9d77fb3))

Choose **Capture scrolling area** from the tray or press `Ctrl+Alt+PrtSc`, then
click a finite, static vertical panel that supports Windows UI Automation. The
image uses your normal editor, clipboard and save settings. Ordinary capture
and Repeat are unchanged; there is no configuration dialog or input injection.

Capture uses the whole visible width, preserving the horizontal position. Keep
the target foreground, unobscured and still. Mouse/keyboard activity, including
`Esc`, stops capture without delivering a partial image or promising restoration.
Visible scrollbar thumbs and focus borders may remain. Hidden pixels, tiny
overlays, sticky/animated/infinite content and fractional layouts are not
qualified. See [support and limits](README.md#scrolling-capture).

## [0.13.3](https://github.com/mikejhill/verdiclip/compare/v0.13.2...v0.13.3) (2026-11-07)


### Bug Fixes

* support page-key tab shortcuts safely ([#121](https://github.com/mikejhill/verdiclip/issues/121)) ([3553133](https://github.com/mikejhill/verdiclip/commit/355313309d06a0c2e3aec101d7417fbb18823a1b))


### Documentation

* make physical release checks exceptional ([#123](https://github.com/mikejhill/verdiclip/issues/123)) ([63e32a4](https://github.com/mikejhill/verdiclip/commit/63e32a42348069d4aa8af2d9494eb0d54b96c3e9))

## [0.13.2](https://github.com/mikejhill/verdiclip/compare/v0.13.1...v0.13.2) (2026-11-07)


### Bug Fixes

* freeze requested menu pixels before popup handoff ([#117](https://github.com/mikejhill/verdiclip/issues/117)) ([19ed7e1](https://github.com/mikejhill/verdiclip/commit/19ed7e1ec1203e6a48fadcc2e78841ce92889836))

## [0.13.1](https://github.com/mikejhill/verdiclip/compare/v0.13.0...v0.13.1) (2026-11-07)


### Bug Fixes

* gate release publication on exact main verification ([#83](https://github.com/mikejhill/verdiclip/issues/83)) ([9030047](https://github.com/mikejhill/verdiclip/commit/90300471544236d1e5a5203c4fc7bad91dc27a0f))
* restore 0.12.1 capture behavior with tabs ([#116](https://github.com/mikejhill/verdiclip/issues/116)) ([80a4d66](https://github.com/mikejhill/verdiclip/commit/80a4d661262e526c72068c8450d6424f5056ca12))
* retire mutation writers before sealing cached verdicts ([#90](https://github.com/mikejhill/verdiclip/issues/90)) ([6936b5e](https://github.com/mikejhill/verdiclip/commit/6936b5edc4f1ecf889205b1f2cb9b35ee110c9c1))
* stage workflow configuration for mutation statistics ([#87](https://github.com/mikejhill/verdiclip/issues/87)) ([0b15699](https://github.com/mikejhill/verdiclip/commit/0b156997985b9ad24e8e0d3e691d51702b51f670))

## [0.13.0](https://github.com/mikejhill/verdiclip/compare/v0.12.1...v0.13.0) (2026-11-05)


### Features

* add tabbed editor and guarded capture integration ([#62](https://github.com/mikejhill/verdiclip/issues/62)) ([5e858de](https://github.com/mikejhill/verdiclip/commit/5e858de17c8908b1d2828dbb169931587ed5d9fb))


### Bug Fixes

* prepare GUI resources before capture admission ([#78](https://github.com/mikejhill/verdiclip/issues/78)) ([5942159](https://github.com/mikejhill/verdiclip/commit/594215905b6f1010bcccfa8179a3921f699a0551))
* preserve staged mutation test inputs ([#76](https://github.com/mikejhill/verdiclip/issues/76)) ([5fe14b2](https://github.com/mikejhill/verdiclip/commit/5fe14b25b651c02caea31786e44e9508595e4e19))
* retain IPC clients through deferred deletion ([#72](https://github.com/mikejhill/verdiclip/issues/72)) ([f3066d3](https://github.com/mikejhill/verdiclip/commit/f3066d3cf458a4d8f72a4b679a22c72cc922d191))


### Documentation

* use theme-aware transparent README wordmark ([#66](https://github.com/mikejhill/verdiclip/issues/66)) ([4d1dc51](https://github.com/mikejhill/verdiclip/commit/4d1dc512f393f5fc5cc2d3db5b1a03b8386efc41))

## [0.12.1](https://github.com/mikejhill/verdiclip/compare/v0.12.0...v0.12.1) (2026-10-25)


### Bug Fixes

* transfer input ownership before capture ([cd26c2d](https://github.com/mikejhill/verdiclip/commit/cd26c2d40cfb80489d7ac89a50f2b0d629cea8f1))

## [0.12.0](https://github.com/mikejhill/verdiclip/compare/v0.11.0...v0.12.0) (2026-10-25)


### Features

* **brand:** refine VerdiClip capture identity ([#56](https://github.com/mikejhill/verdiclip/issues/56)) ([a5190a4](https://github.com/mikejhill/verdiclip/commit/a5190a45bff19375add35a409cf8c621cf2b20d8))


### Bug Fixes

* make update checks visible and refresh docs ([bfaf618](https://github.com/mikejhill/verdiclip/commit/bfaf618edf74aee3465e8e197d26f1857480f4a7))

## [Unreleased]

### Changed

- Refined the app identity with an emerald capture-corner V, scalable runtime
  SVG, light/dark wordmarks, pixel exports, and a brand guide.

## [0.11.0](https://github.com/mikejhill/verdiclip/compare/v0.10.0...v0.11.0) (2026-10-22)


### Features

* update VerdiClip automatically from PyPI ([#53](https://github.com/mikejhill/verdiclip/issues/53)) ([a689fcb](https://github.com/mikejhill/verdiclip/commit/a689fcbcd07281d42a88efecc592e34524108eb5))

## [0.10.0](https://github.com/mikejhill/verdiclip/compare/v0.9.1...v0.10.0) (2026-10-19)


### Features

* add Help menu with documentation link and About to the editor ([#49](https://github.com/mikejhill/verdiclip/issues/49)) ([b6aa889](https://github.com/mikejhill/verdiclip/commit/b6aa8891bda42993304a59cade3c7165ae1b6a1d))

## [0.9.1](https://github.com/mikejhill/verdiclip/compare/v0.9.0...v0.9.1) (2026-10-16)


### Bug Fixes

* record PrtSc, numpad and punctuation keys in Settings ([#47](https://github.com/mikejhill/verdiclip/issues/47)) ([1d33b4a](https://github.com/mikejhill/verdiclip/commit/1d33b4a3ec98b3c82afda93cdd9a83f115fe9e76))

## [0.9.0](https://github.com/mikejhill/verdiclip/compare/v0.8.3...v0.9.0) (2026-10-16)


### Features

* record hotkeys by pressing them in Settings ([#45](https://github.com/mikejhill/verdiclip/issues/45)) ([550c6b6](https://github.com/mikejhill/verdiclip/commit/550c6b65fc6a788f3a9cc91008ad2c1312c0542d))

## [0.8.3](https://github.com/mikejhill/verdiclip/compare/v0.8.2...v0.8.3) (2026-10-16)


### Bug Fixes

* no open dialog freezes a capture, and About drops the Greenshot line ([#43](https://github.com/mikejhill/verdiclip/issues/43)) ([4a4648a](https://github.com/mikejhill/verdiclip/commit/4a4648a3abac2a2320d471391f417e3fbbea9ea1))

## [0.8.2](https://github.com/mikejhill/verdiclip/compare/v0.8.1...v0.8.2) (2026-10-16)


### Bug Fixes

* Esc steps one shared ladder to rest in every tool ([#41](https://github.com/mikejhill/verdiclip/issues/41)) ([1e08841](https://github.com/mikejhill/verdiclip/commit/1e088414c151fea90aa99f51a2891d909477df7a))

## [0.8.1](https://github.com/mikejhill/verdiclip/compare/v0.8.0...v0.8.1) (2026-10-16)


### Bug Fixes

* make toolbar overflow reachable and slim toolbar icons ([#37](https://github.com/mikejhill/verdiclip/issues/37)) ([4129672](https://github.com/mikejhill/verdiclip/commit/41296724d282746f8c854bc09f03d51126ea43c4))

## [0.8.0](https://github.com/mikejhill/verdiclip/compare/v0.7.0...v0.8.0) (2026-10-16)


### Features

* default filename pattern has no spaces ([#36](https://github.com/mikejhill/verdiclip/issues/36)) ([6402fd1](https://github.com/mikejhill/verdiclip/commit/6402fd1a9952252ae62450ffe21810315cdec9a0))


### Bug Fixes

* text boxes keep on Esc, start on the image, and Esc reaches Select sooner ([#38](https://github.com/mikejhill/verdiclip/issues/38)) ([4a5f7b4](https://github.com/mikejhill/verdiclip/commit/4a5f7b4cb2e043488113ddbe1efde170e78441c1))

## [0.7.0](https://github.com/mikejhill/verdiclip/compare/v0.6.0...v0.7.0) (2026-10-15)


### Features

* text boxes with outlines, auto-fit or fixed size, alignment, and shadows ([#31](https://github.com/mikejhill/verdiclip/issues/31)) ([5351c36](https://github.com/mikejhill/verdiclip/commit/5351c366b971bd7165b7674411552639b2d82ceb))


### Bug Fixes

* repair Open with and shortcuts that still open a console ([#34](https://github.com/mikejhill/verdiclip/issues/34)) ([5e9b246](https://github.com/mikejhill/verdiclip/commit/5e9b2463e15822934b2a84133d0e61c8aec1a798))

## [0.6.0](https://github.com/mikejhill/verdiclip/compare/v0.5.0...v0.6.0) (2026-10-15)


### Features

* tray "Open screenshots folder", a clearer Settings cog, and room before style labels ([#30](https://github.com/mikejhill/verdiclip/issues/30)) ([67bba15](https://github.com/mikejhill/verdiclip/commit/67bba1595a1e903a2e136fb30cfb302502ed0146))

## [0.5.0](https://github.com/mikejhill/verdiclip/compare/v0.4.2...v0.5.0) (2026-10-15)


### Features

* label runs from a checkout as the next version's beta ([#25](https://github.com/mikejhill/verdiclip/issues/25)) ([708f1fe](https://github.com/mikejhill/verdiclip/commit/708f1fede275015c55300670945b8b7c08cd005d))
* name window screenshots after their window by default ([#26](https://github.com/mikejhill/verdiclip/issues/26)) ([f63117c](https://github.com/mikejhill/verdiclip/commit/f63117c27a8a7fe2a3c184359d9ac6fe337144cd))
* start at sign-in and appear in Start via named shortcuts ([#22](https://github.com/mikejhill/verdiclip/issues/22)) ([8afb121](https://github.com/mikejhill/verdiclip/commit/8afb121d9e226b8b50eee4683ca2af99a1f5c4fd))


### Bug Fixes

* don't let a second checkout take over shortcuts or Open with ([#28](https://github.com/mikejhill/verdiclip/issues/28)) ([59b07d1](https://github.com/mikejhill/verdiclip/commit/59b07d16ec36c7006efc479face0e135ab8e153d))
* launch shortcuts without a console window ([#27](https://github.com/mikejhill/verdiclip/issues/27)) ([29e7e71](https://github.com/mikejhill/verdiclip/commit/29e7e7153b595d6d39d7d5f8a1e53b74b2896301))
* span monitors, capture with Settings open, full screen = current monitor ([34da576](https://github.com/mikejhill/verdiclip/commit/34da5765410117b86a9e64f2798bca906f30331f))

## [0.4.2](https://github.com/mikejhill/verdiclip/compare/v0.4.1...v0.4.2) (2026-10-11)


### Bug Fixes

* keep checked boxes visible after switching themes ([#19](https://github.com/mikejhill/verdiclip/issues/19)) ([de4f73c](https://github.com/mikejhill/verdiclip/commit/de4f73c12bdd836baba7977207a3265049b9e4e1))

## [0.4.1](https://github.com/mikejhill/verdiclip/compare/v0.4.0...v0.4.1) (2026-10-11)


### Bug Fixes

* add detached Python launchers for the checkout ([#17](https://github.com/mikejhill/verdiclip/issues/17)) ([9affa4f](https://github.com/mikejhill/verdiclip/commit/9affa4f1f9a422ed212bb9b057852db68e2e3eff))

## [0.4.0](https://github.com/mikejhill/verdiclip/compare/v0.3.0...v0.4.0) (2026-10-10)


### Features

* pixel-exact magnifier reticle and Enter to start/finish a selection ([#15](https://github.com/mikejhill/verdiclip/issues/15)) ([c5a22fc](https://github.com/mikejhill/verdiclip/commit/c5a22fcd96137aa6a439cfeda50fc00a205672e3))

## [0.3.0](https://github.com/mikejhill/verdiclip/compare/v0.2.1...v0.3.0) (2026-10-10)


### Features

* ask before closing a capture that wasn't saved or copied ([#6](https://github.com/mikejhill/verdiclip/issues/6)) ([7729379](https://github.com/mikejhill/verdiclip/commit/772937997a89fc92568739db0ccb8e70ad1bc188))
* offer VerdiClip in Explorer's Open with menu for images ([#9](https://github.com/mikejhill/verdiclip/issues/9)) ([cafb1bc](https://github.com/mikejhill/verdiclip/commit/cafb1bc33617ebf5133fd60a6f8f74bdffb91eca))
* rotate, flip, and resize images in the editor ([#11](https://github.com/mikejhill/verdiclip/issues/11)) ([fd8209b](https://github.com/mikejhill/verdiclip/commit/fd8209b7be86c810bc33b0e12714173aa467c40a))


### Bug Fixes

* highlight windows that span monitors on every monitor ([#7](https://github.com/mikejhill/verdiclip/issues/7)) ([bc5f032](https://github.com/mikejhill/verdiclip/commit/bc5f032dbba036fce70642edbc1c74a4c0b4b43d))
* re-apply run-at-login and Open with registrations on launch ([#12](https://github.com/mikejhill/verdiclip/issues/12)) ([c466b1c](https://github.com/mikejhill/verdiclip/commit/c466b1c3620be19618cc749d9608949e69ab1ab0))
* word the close prompt for what would be lost and add S/N shortcuts ([#13](https://github.com/mikejhill/verdiclip/issues/13)) ([3cac693](https://github.com/mikejhill/verdiclip/commit/3cac6932653899e2d086433989255f397d3163c7))


### Documentation

* simplify attribution to a plain credit ([#14](https://github.com/mikejhill/verdiclip/issues/14)) ([8ea308c](https://github.com/mikejhill/verdiclip/commit/8ea308cc5ee088d8b5cd29f8fd72609bb9c7ba03))

## [0.2.1](https://github.com/mikejhill/verdiclip/compare/v0.2.0...v0.2.1) (2026-10-08)


### Bug Fixes

* show capture guides and window highlight before the mouse moves ([#4](https://github.com/mikejhill/verdiclip/issues/4)) ([f80e117](https://github.com/mikejhill/verdiclip/commit/f80e1179785be096bb08c0e5fffed309f5bf6914))

## [0.2.0](https://github.com/mikejhill/verdiclip/compare/v0.1-legacy...v0.2.0) (2026-10-07)


### ⚠ BREAKING CHANGES

* rebuild VerdiClip around an immutable document model ([#1](https://github.com/mikejhill/verdiclip/issues/1)) ([f1f43a9](https://github.com/mikejhill/verdiclip/commit/f1f43a9fb24457fc0ad52552b5044e003e133bbf)). Settings move to `%APPDATA%\VerdiClip\settings.json` with a new schema; the pynput and Pillow dependencies are removed; the CLI options `--monitor`, `--format`, and `--quality` are gone.

### Features

* ground-up rebuild: immutable document with a non-destructive crop, every change undoable, and one renderer for the canvas and exports ([#1](https://github.com/mikejhill/verdiclip/issues/1))
* frozen-screen capture across all monitors with a magnifier, window picking, and repeat-last; global hotkeys via `RegisterHotKey` with conflict reporting
* editor with select, crop, rectangle, ellipse, line, arrow, text, counter, highlight, obfuscate, and freehand tools
* type labels inside rectangles and ellipses, starting right after drawing
* per-tool colors, fills, widths, and fonts remembered across screenshots
* combine after-capture actions: open in the editor, copy to the clipboard, save to a file
* light, dark, and match-Windows themes; settings reachable from any editor (`Ctrl+,`)
* single instance with file forwarding, run at sign-in, and a `capture`/`open` command line

### Documentation

* README with screenshots, design philosophy, UX contract, contributing and release guides ([#1](https://github.com/mikejhill/verdiclip/issues/1))
