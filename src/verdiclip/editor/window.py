"""The editor window: tools, actions, menus, and delivery for one document."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from PySide6.QtCore import QEvent, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from verdiclip.document.annotations import CounterMarker, LabeledBox, TextNote
from verdiclip.document.style import Style
from verdiclip.document.transform import (
    Flip,
    FlipAxis,
    ImageTransform,
    Resize,
    Rotate,
)
from verdiclip.editor.canvas import CanvasView
from verdiclip.editor.chrome import (
    BUTTON_SIZE,
    COMPACT_BUTTON_SIZE,
    ICON_SIZE,
    ChromeStyle,
)
from verdiclip.editor.icons import IconFactory
from verdiclip.editor.resize_dialog import ResizeDialog
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.style_bar import FIELD_LAYOUT, KIND_TOOLS, StyleBar
from verdiclip.editor.tools import TOOL_TYPES, Tool
from verdiclip.exceptions import AppError, CodecError
from verdiclip.geometry import Point
from verdiclip.output.delivery import ImageDelivery
from verdiclip.render.renderer import Renderer
from verdiclip.settings import ImageFormat

logger = logging.getLogger(__name__)

ANNOTATION_MIME: Final = "application/x-verdiclip-annotations+json"
STATUS_TIMEOUT_MS: Final = 4000
TOOL_GROUPS: Final = (
    (ToolId.SELECT, ToolId.CROP),
    (ToolId.RECTANGLE, ToolId.ELLIPSE, ToolId.LINE, ToolId.ARROW, ToolId.FREEHAND),
    (ToolId.TEXT, ToolId.COUNTER),
    (ToolId.HIGHLIGHT, ToolId.OBFUSCATE),
)
ACTION_GROUPS: Final = (
    ("copy_image", "save"),
    ("undo", "redo"),
    ("image",),
    ("settings",),
)
IMAGE_ACTIONS: Final = (
    "rotate_left",
    "rotate_right",
    "flip_horizontal",
    "flip_vertical",
    "resize",
)
IMAGE_FILTER: Final = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"


@dataclass(frozen=True, slots=True)
class EditorOptions:
    """How an editor window presents its document."""

    title: str = ""
    source_path: Path | None = None
    confirm_close: bool = True


class EditorWindow(QMainWindow):
    """Edit one document and deliver it."""

    open_image_requested = Signal(object)  # Path
    settings_requested = Signal()

    def __init__(
        self,
        session: EditorSession,
        delivery: ImageDelivery,
        options: EditorOptions | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        options = options or EditorOptions()
        title = options.title
        source_path = options.source_path
        self._confirm_close = options.confirm_close
        self._session = session
        self._delivery = delivery
        self._renderer = Renderer()
        self._title = title or "Screenshot"
        self._saved_path = source_path
        self._icons = IconFactory(self.palette().windowText().color())
        self._canvas = CanvasView(session, self._renderer, self)
        self._style_bar = StyleBar(self)
        self._tools: dict[ToolId, Tool] = {
            t.tool_id: t(session, self) for t in TOOL_TYPES
        }
        self._tool_actions: dict[ToolId, QAction] = {}
        self._actions: dict[str, QAction] = {}
        self._hint = QLabel()
        self._position = QLabel()
        self._size = QLabel()
        self._zoom = QToolButton()
        self._shown_once = False
        self._chrome_bars: tuple[QToolBar, ...] = ()
        self._build()
        self.activate_tool(ToolId.SELECT)
        self._refresh_chrome()

    # Public API

    @property
    def session(self) -> EditorSession:
        """Return the editing session."""
        return self._session

    @property
    def canvas(self) -> CanvasView:
        """Return the canvas."""
        return self._canvas

    @property
    def saved_path(self) -> Path | None:
        """Return where the document was last saved, if anywhere."""
        return self._saved_path

    def action(self, name: str) -> QAction:
        """Return the named action (for tests and the tray)."""
        return self._actions[name]

    def set_confirm_close(self, *, confirm: bool) -> None:
        """Turn the unsaved-changes prompt on or off."""
        self._confirm_close = confirm

    def flattened(self) -> QImage:
        """Return the cropped image with annotations, as exported."""
        self._canvas.editors.commit()
        return self._renderer.flatten(self._session.document)

    # ToolHost

    def activate_tool(self, tool: ToolId) -> None:
        """Switch to ``tool``."""
        self._canvas.set_tool(self._tools[tool])
        self._tool_actions[tool].setChecked(True)
        self._hint.setText(self._tools[tool].hint)
        self._sync_style_bar()

    def edit_text(self, note: TextNote | None, at: Point) -> None:
        """Open the inline text editor."""
        self._canvas.editors.edit_text(note, at)

    def edit_counter(self, marker: CounterMarker) -> None:
        """Open the inline counter label editor."""
        self._canvas.editors.edit_counter(marker)

    def edit_box_text(self, box: LabeledBox) -> None:
        """Open the inline editor for the text inside a rectangle or ellipse."""
        self._canvas.editors.edit_box_text(box)

    def edit_selected_text(self) -> bool:
        """Edit the text of the single selected annotation, if it has any."""
        selected = self._session.selected()
        if len(selected) != 1:
            return False
        target = selected[0]
        if isinstance(target, LabeledBox):
            self.edit_box_text(target)
        elif isinstance(target, TextNote):
            self.edit_text(target, target.rect.top_left)
        elif isinstance(target, CounterMarker):
            self.edit_counter(target)
        else:
            return False
        return True

    # Delivery actions

    def copy_image(self) -> bool:
        """Copy the flattened image to the clipboard."""
        if not self._deliver(lambda: self._delivery.copy(self.flattened())):
            return False
        self.statusBar().showMessage("Image copied to clipboard", STATUS_TIMEOUT_MS)
        return True

    def save(self) -> bool:
        """Save to the last path, or to an automatic name (UX-OUT-02)."""
        path = self._saved_path
        if path is None or not self._is_writable_format(path):
            path = self._delivery.auto_path(title=self._title)
        return self._save_to(path)

    def save_as(self) -> bool:
        """Ask for a path and save there."""
        start = self._saved_path or self._delivery.auto_path(title=self._title)
        filters = ";;".join(
            f"{f.value.upper()} image (*.{f.value})" for f in ImageFormat
        )
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save image as", str(start), filters
        )
        if not chosen:
            return False
        return self._save_to(Path(chosen))

    def print_image(self) -> bool:
        """Print the flattened image (printing doesn't count as saving it)."""
        printed = False

        def run() -> None:
            nonlocal printed
            printed = self._delivery.print_image(self.flattened(), self)

        return self._deliver(run, mark=False) and printed

    # Building

    def _build(self) -> None:
        """Assemble the window."""
        self.setWindowIcon(self._icons.brand())
        self.setAcceptDrops(True)
        self.setCentralWidget(self._canvas)
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_bar()
        self._session.history.subscribe(self._refresh_chrome)
        self._session.selection.subscribe(self._sync_style_bar)
        self._canvas.zoom_changed.connect(lambda _z: self._refresh_zoom())
        self._canvas.cursor_moved.connect(self._show_position)
        self._canvas.escape_unhandled.connect(self._on_escape)
        self._canvas.enter_unhandled.connect(self._on_enter)
        self._style_bar.style_changed.connect(self._on_style_changed)
        self.resize(self._initial_size())

    def _add_action(
        self,
        name: str,
        text: str,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None,
        slot: Callable[[], object],
    ) -> QAction:
        """Create, register, and return an action."""
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
            native = QKeySequence.SequenceFormat.NativeText
            keys = action.shortcut().toString(native)
            plain = text.replace("&", "").replace("…", "")
            action.setToolTip(f"{plain} ({keys})")
        action.triggered.connect(lambda _checked=False: slot())
        self.addAction(action)
        self._actions[name] = action
        return action

    def _build_actions(self) -> None:
        """Create every command action with its shortcut."""
        s = self._session
        canvas = self._canvas
        self._add_action(
            "open", "&Open image…", QKeySequence.StandardKey.Open, self._open
        )
        self._add_action("save", "&Save", QKeySequence.StandardKey.Save, self.save)
        self._add_action("save_as", "Save &as…", "Ctrl+Shift+S", self.save_as)
        self._add_action("copy_image", "Copy &image", "Ctrl+Shift+C", self.copy_image)
        self._add_action(
            "print", "&Print…", QKeySequence.StandardKey.Print, self.print_image
        )
        self._add_action(
            "settings", "Se&ttings…", "Ctrl+,", self.settings_requested.emit
        )
        self._add_action("close", "&Close", "Ctrl+W", self.close)
        self._add_action("undo", "&Undo", QKeySequence.StandardKey.Undo, self._undo)
        redo = self._add_action("redo", "&Redo", "Ctrl+Y", self._redo)
        redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        # Toolbar buttons keep short labels; menus show "Undo Draw arrow"
        self._actions["undo"].setIconText("Undo")
        redo.setIconText("Redo")
        self._add_action("copy", "&Copy", QKeySequence.StandardKey.Copy, self._copy)
        self._add_action("paste", "&Paste", QKeySequence.StandardKey.Paste, self._paste)
        delete = self._add_action("delete", "&Delete", "Delete", s.delete_selected)
        delete.setShortcuts([QKeySequence("Delete"), QKeySequence("Backspace")])
        self._add_action(
            "select_all",
            "Select &all",
            QKeySequence.StandardKey.SelectAll,
            self._select_all,
        )
        self._add_action("edit_text", "Edit &text", "F2", self.edit_selected_text)
        self._add_action(
            "forward",
            "Bring &forward",
            "Ctrl+]",
            lambda: s.restack_selected(forward=True),
        )
        self._add_action(
            "backward",
            "Send &backward",
            "Ctrl+[",
            lambda: s.restack_selected(forward=False),
        )
        self._add_action(
            "rotate_right",
            "Rotate &right",
            "Ctrl+R",
            lambda: self.transform_image(Rotate(clockwise=True)),
        )
        self._add_action(
            "rotate_left",
            "Rotate &left",
            "Ctrl+Shift+R",
            lambda: self.transform_image(Rotate(clockwise=False)),
        )
        self._add_action(
            "flip_horizontal",
            "Flip &horizontally",
            "Ctrl+Shift+H",
            lambda: self.transform_image(Flip(FlipAxis.HORIZONTAL)),
        )
        self._add_action(
            "flip_vertical",
            "Flip &vertically",
            "Ctrl+Shift+V",
            lambda: self.transform_image(Flip(FlipAxis.VERTICAL)),
        )
        self._add_action("resize", "Re&size…", "Ctrl+Alt+I", self.resize_image)
        image = self._add_action("image", "&Image", None, lambda: None)
        image.setToolTip("Rotate, flip, or resize the image")
        image_menu = QMenu(self)
        for name in IMAGE_ACTIONS:
            image_menu.addAction(self._actions[name])
        image.setMenu(image_menu)
        zoom_in = self._add_action("zoom_in", "Zoom &in", "Ctrl+=", canvas.zoom_in)
        zoom_in.setShortcuts([QKeySequence("Ctrl+="), QKeySequence("Ctrl++")])
        self._add_action("zoom_out", "Zoom &out", "Ctrl+-", canvas.zoom_out)
        self._add_action("zoom_actual", "&Actual size", "Ctrl+0", canvas.zoom_actual)
        self._add_action("zoom_fit", "&Fit to window", "Ctrl+Shift+F", canvas.zoom_fit)
        group = QActionGroup(self)
        for tool_type in TOOL_TYPES:
            tool_id = tool_type.tool_id
            action = self._add_action(
                f"tool_{tool_id.value}",
                tool_type.label,
                tool_type.shortcut,
                lambda t=tool_id: self.activate_tool(t),
            )
            action.setCheckable(True)
            action.setIcon(self._icons.tool(tool_id))
            action.setToolTip(
                f"{tool_type.label} ({tool_type.shortcut}) — {tool_type.hint}"
            )
            group.addAction(action)
            self._tool_actions[tool_id] = action

    def _build_menus(self) -> None:
        """Create the menu bar."""
        bar = self.menuBar()
        layout = {
            "&File": [
                "open",
                None,
                "save",
                "save_as",
                "copy_image",
                "print",
                None,
                "settings",
                None,
                "close",
            ],
            "&Edit": [
                "undo",
                "redo",
                None,
                "copy",
                "paste",
                "delete",
                "select_all",
                "edit_text",
                None,
                "forward",
                "backward",
            ],
            "&Image": [
                "rotate_left",
                "rotate_right",
                None,
                "flip_horizontal",
                "flip_vertical",
                None,
                "resize",
            ],
            "&View": ["zoom_in", "zoom_out", "zoom_actual", "zoom_fit"],
        }
        for title, names in layout.items():
            menu = bar.addMenu(title)
            for name in names:
                if name is None:
                    menu.addSeparator()
                else:
                    menu.addAction(self._actions[name])
        tools = bar.addMenu("&Tools")
        for action in self._tool_actions.values():
            tools.addAction(action)

    def _build_toolbars(self) -> None:
        """Create the grouped tool palette, the icon action bar, and the style bar."""
        palette = QToolBar("Tools", self)
        palette.setMovable(False)
        palette.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        for group in TOOL_GROUPS:
            for tool_id in group:
                palette.addAction(self._tool_actions[tool_id])
            if group is not TOOL_GROUPS[-1]:
                palette.addSeparator()
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, palette)
        actions = QToolBar("Actions", self)
        actions.setMovable(False)
        actions.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        actions.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for group in ACTION_GROUPS:
            for name in group:
                actions.addAction(self._actions[name])
            actions.addSeparator()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, actions)
        image_button = actions.widgetForAction(self._actions["image"])
        if isinstance(image_button, QToolButton):
            image_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._style_bar)
        # One fixed height for the top row so switching tools never moves the canvas
        height = max(self._style_bar.tallest_height(), BUTTON_SIZE + 8)
        actions.setFixedHeight(height)
        self._style_bar.setFixedHeight(height)
        self._chrome_bars = (palette, actions, self._style_bar)
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        """Restyle toolbars and redraw icons for the current theme."""
        chrome = ChromeStyle(self.palette())
        self._icons = IconFactory(self.palette().windowText().color())
        for bar in self._chrome_bars:
            compact = bar is self._style_bar
            size = COMPACT_BUTTON_SIZE if compact else BUTTON_SIZE
            bar.setStyleSheet(chrome.toolbar_sheet(size))
        for tool_id, action in self._tool_actions.items():
            action.setIcon(self._icons.tool(tool_id))
        for group in ACTION_GROUPS:
            for name in group:
                self._actions[name].setIcon(self._icons.action(name))

    def _build_status_bar(self) -> None:
        """Create the status bar: hint, position, size, zoom."""
        status = self.statusBar()
        status.addWidget(self._hint, 1)
        self._position.setMinimumWidth(
            self.fontMetrics().horizontalAdvance("00000, 00000")
        )
        self._position.setToolTip("Pointer position in image pixels")
        status.addPermanentWidget(self._position)
        status.addPermanentWidget(self._size)
        menu = QMenu(self._zoom)
        for percent in (25, 50, 100, 200, 400):
            act = menu.addAction(f"{percent}%")
            act.triggered.connect(
                lambda _=False, z=percent / 100: self._canvas.set_zoom(z)
            )
        menu.addAction(self._actions["zoom_fit"])
        self._zoom.setMenu(menu)
        self._zoom.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._zoom.setToolTip("Zoom (Ctrl+wheel)")
        status.addPermanentWidget(self._zoom)

    def _initial_size(self) -> QSize:
        """Return a window size that fits the image within 85% of the screen."""
        screen = self.screen().availableGeometry()
        crop = self._session.document.crop
        width = min(int(screen.width() * 0.85), int(crop.width) + 160)
        height = min(int(screen.height() * 0.85), int(crop.height) + 170)
        return QSize(max(720, width), max(480, height))

    # Event handlers

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Apply the initial zoom once the viewport has its real size."""
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            self._canvas.show_initial()
            self._canvas.setFocus()

    @override
    def changeEvent(self, event: QEvent) -> None:
        """Redraw tool icons in the new ink color when the theme changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and self._chrome_bars:
            self._apply_chrome()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Ask before discarding an image that wasn't saved or copied (UX-G-06)."""
        self._canvas.editors.commit()
        if not self._confirm_close or self._session.history.is_delivered:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "This image hasn't been saved or copied. Save it before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Discard or (
            answer == QMessageBox.StandardButton.Save and self.save()
        ):
            event.accept()
        else:
            event.ignore()

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept dragged image files."""
        if any(Path(u.toLocalFile()).suffix for u in event.mimeData().urls()):
            event.acceptProposedAction()

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        """Open dropped image files in new editors."""
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_image_requested.emit(Path(url.toLocalFile()))
        event.acceptProposedAction()

    def _on_escape(self) -> None:
        """Return to the Select tool when Esc has nothing else to cancel."""
        if self._canvas.tool is not self._tools[ToolId.SELECT]:
            self.activate_tool(ToolId.SELECT)

    def _on_enter(self) -> None:
        """Enter edits the selection's text; otherwise copy and close (UX-OUT-07)."""
        if self.edit_selected_text():
            return
        selecting = self._canvas.tool is self._tools[ToolId.SELECT]
        if selecting and len(self._session.selection) == 0 and self.copy_image():
            self.close()

    def _on_style_changed(self, change: Callable[[Style], Style]) -> None:
        """Apply a style edit to the selection and to the matching tool defaults."""
        selected = self._session.selected()
        if selected:
            self._session.restyle_selected(change)
            for kind in {a.kind for a in selected}:
                tool = KIND_TOOLS[kind]
                self._session.styles.set(tool, change(self._session.styles.get(tool)))
            return
        tool = self._current_tool_id()
        self._session.styles.set(tool, change(self._session.styles.get(tool)))

    # Edit actions

    def _copy(self) -> None:
        """Copy selected annotations, or the whole image when nothing is selected."""
        text = self._session.copy_selected()
        if text is None:
            self.copy_image()
            return
        mime = QMimeData()
        mime.setData(ANNOTATION_MIME, text.encode("utf-8"))
        clipboard = QGuiApplication.clipboard()
        clipboard.setMimeData(mime)
        count = len(self._session.selection)
        self.statusBar().showMessage(f"Copied {count} annotation(s)", STATUS_TIMEOUT_MS)

    def _paste(self) -> None:
        """Paste annotations copied from any VerdiClip editor."""
        mime = QGuiApplication.clipboard().mimeData()
        if not mime.hasFormat(ANNOTATION_MIME):
            self.statusBar().showMessage(
                "Nothing to paste: copy annotations first", STATUS_TIMEOUT_MS
            )
            return
        try:
            self._session.paste(
                bytes(mime.data(ANNOTATION_MIME).data()).decode("utf-8")
            )
        except (CodecError, UnicodeDecodeError) as err:
            logger.warning("Paste failed: %s", err)
            self.statusBar().showMessage(
                "Clipboard annotations could not be read", STATUS_TIMEOUT_MS
            )

    def transform_image(self, transform: ImageTransform) -> None:
        """Commit any in-place editor, then rotate, flip, or resize (UX-IMG-01)."""
        self._canvas.editors.commit()
        self._session.transform_image(transform)
        crop = self._session.document.crop
        self.statusBar().showMessage(
            f"{transform.description}: {crop.width:.0f} × {crop.height:.0f} px",  # noqa: RUF001
            STATUS_TIMEOUT_MS,
        )

    def resize_image(self) -> None:
        """Ask for a new size, then resize the visible image (UX-IMG-03)."""
        crop = self._session.document.crop.rounded()
        dialog = ResizeDialog(int(crop.width), int(crop.height), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        width, height = dialog.result_size()
        if (width, height) == (int(crop.width), int(crop.height)):
            return
        self.transform_image(Resize(width, height))

    def _undo(self) -> None:
        """Close any in-place editor, then undo."""
        self._canvas.editors.commit()
        self._session.history.undo()

    def _redo(self) -> None:
        """Close any in-place editor, then redo."""
        self._canvas.editors.commit()
        self._session.history.redo()

    def _select_all(self) -> None:
        """Select every annotation and switch to the Select tool."""
        self.activate_tool(ToolId.SELECT)
        self._session.select_all()

    def _open(self) -> None:
        """Ask for an image file and request an editor for it."""
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open image", str(self._delivery.settings.directory), IMAGE_FILTER
        )
        if chosen:
            self.open_image_requested.emit(Path(chosen))

    # Internals

    def _deliver(self, run: Callable[[], object], *, mark: bool = True) -> bool:
        """Run a delivery, reporting failures to the user (UX-OUT-04)."""
        try:
            run()
        except AppError as err:
            QMessageBox.warning(self, "VerdiClip", str(err))
            return False
        if mark:
            self._session.history.mark_delivered()
        return True

    def _save_to(self, path: Path) -> bool:
        """Save to ``path`` and remember it."""
        if not self._deliver(lambda: self._delivery.save(self.flattened(), path)):
            return False
        self._saved_path = path
        self.statusBar().showMessage(f"Saved {path}", STATUS_TIMEOUT_MS)
        self._refresh_chrome()
        return True

    def _is_writable_format(self, path: Path) -> bool:
        """Return True if ``path`` has an extension VerdiClip can write."""
        try:
            self._delivery.format_for(path)
        except AppError:
            return False
        return True

    def _current_tool_id(self) -> ToolId:
        """Return the active tool's id."""
        tool = self._canvas.tool
        return tool.tool_id if tool is not None else ToolId.SELECT

    def _sync_style_bar(self) -> None:
        """Show the selection's style, or the active tool's defaults."""
        selected = self._session.selected()
        if selected:
            tool = KIND_TOOLS[selected[0].kind]
            self._style_bar.show_for(FIELD_LAYOUT[tool], selected[0].style)
            return
        tool = self._current_tool_id()
        self._style_bar.show_for(FIELD_LAYOUT[tool], self._session.styles.get(tool))

    def _refresh_chrome(self) -> None:
        """Update title, undo/redo state, and size label."""
        history = self._session.history
        self._actions["undo"].setEnabled(history.can_undo)
        self._actions["redo"].setEnabled(history.can_redo)
        self._actions["undo"].setText(f"&Undo {history.undo_text}".strip())
        self._actions["redo"].setText(f"&Redo {history.redo_text}".strip())
        name = self._saved_path.name if self._saved_path is not None else self._title
        marker = "" if history.is_delivered else " •"
        self.setWindowTitle(f"{name}{marker} — VerdiClip")
        crop = self._session.document.crop
        self._size.setText(f"{int(crop.width)} × {int(crop.height)} px")  # noqa: RUF001
        self._refresh_zoom()
        self._sync_style_bar()

    def _refresh_zoom(self) -> None:
        """Show the zoom percentage."""
        self._zoom.setText(f"{round(self._canvas.zoom * 100)}%")

    def _show_position(self, point: Point | None) -> None:
        """Show the pointer position relative to the visible image."""
        if point is None:
            self._position.setText("")
            return
        crop = self._session.document.crop
        self._position.setText(f"{int(point.x - crop.x)}, {int(point.y - crop.y)}")
