"""The editor window: tools, actions, menus, and delivery for one document."""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast, override

from PySide6.QtCore import QEvent, QMimeData, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from verdiclip import APP_NAME, DOCS_URL
from verdiclip.document.admission import CaptureProvenance
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
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.editor.icons import IconFactory
from verdiclip.editor.page import DocumentPage, DocumentRecord
from verdiclip.editor.resize_dialog import ResizeDialog
from verdiclip.editor.session import EditorSession, ToolId
from verdiclip.editor.style_bar import FIELD_LAYOUT, KIND_TOOLS, StyleBar, TextFit
from verdiclip.editor.tab_bar import DocumentTabBar
from verdiclip.editor.tools import TOOL_TYPES, Tool
from verdiclip.exceptions import AppError, CodecError
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.render.renderer import Renderer
from verdiclip.settings import ImageFormat

if TYPE_CHECKING:
    from verdiclip.editor.workspace import Workspace

logger = logging.getLogger(__name__)

ANNOTATION_MIME: Final = "application/x-verdiclip-annotations+json"
STATUS_TIMEOUT_MS: Final = 4000
LAST_TAB_NUMBER: Final = 9
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
    provenance: CaptureProvenance | None = None


@dataclass(frozen=True, slots=True)
class CloseApproval:
    """Approve one exact content state, never a mutable active-tab position."""

    page: DocumentPage
    state_id: int

    @property
    def current(self) -> bool:
        """Refuse new edits or output leases created after the decision."""
        record = self.page.record
        return (
            record.session.history.state_id == self.state_id
            and not record.operations
            and not record.jobs.blocks_close
        )


class EditorWindow(QMainWindow):
    """Edit one document and deliver it."""

    open_image_requested = Signal(object)  # Path
    settings_requested = Signal()
    about_requested = Signal()

    def __init__(
        self,
        session: EditorSession | None,
        delivery: ImageDelivery,
        options: EditorOptions | None = None,
        parent: QWidget | None = None,
        *,
        workspace: Workspace | None = None,
    ) -> None:
        super().__init__(parent)
        self._application_label = "VerdiClip"
        options = options or EditorOptions()
        from verdiclip.editor.workspace import Workspace  # noqa: PLC0415 - avoid cycle

        self._workspace = workspace if workspace is not None else Workspace()
        self._confirm_close = options.confirm_close
        self._default_delivery = delivery
        self._page_ref: DocumentPage | None = None
        self._closing = False
        self._close_reserved: tuple[DocumentRecord, ...] = ()
        self._approved_close: tuple[CloseApproval, ...] | None = None
        self._tabs = QTabWidget(self)
        self._tab_bar = DocumentTabBar()
        self._tabs.setTabBar(self._tab_bar)
        self._tabs.setDocumentMode(True)
        self._tabs.setAccessibleName("Open captures")
        self._icons = IconFactory(self.palette().windowText().color())
        self._style_bar = StyleBar(self)
        self._tool_actions: dict[ToolId, QAction] = {}
        self._actions: dict[str, QAction] = {}
        self._hint = QLabel()
        self._position = QLabel()
        self._size = QLabel()
        self._zoom = QToolButton()
        self._shown_once = False
        self._chrome_bars: tuple[QToolBar, ...] = ()
        self._build()
        self._workspace.register_window(self)
        if session is not None:
            self.add_document(
                DocumentRecord(
                    session,
                    delivery,
                    options.title or "Screenshot",
                    options.source_path,
                )
            )
        self._refresh_chrome()

    def set_application_label(self, label: str) -> None:
        """Identify isolated preview hosts without changing document metadata."""
        if not label.strip():
            raise ValueError("An application label must be nonempty")
        self._application_label = label
        if self._page_ref is None:
            self.setWindowTitle(label)
        else:
            self._refresh_chrome()

    @property
    def workspace(self) -> Workspace:
        """Return the application owner, also useful to standalone editors."""
        return self._workspace

    @property
    def confirm_close(self) -> bool:
        """Return the current close-confirmation preference for new hosts."""
        return self._confirm_close

    def begin_close_plan(self) -> None:
        """Reserve this host while a global close plan collects decisions."""
        self._closing = True
        self._close_reserved = tuple(page.record for page in self.pages)
        for record in self._close_reserved:
            record.jobs.freeze()

    def end_close_plan(self) -> None:
        """Release a preflight reservation after cancellation or approval."""
        self._closing = False
        records, self._close_reserved = self._close_reserved, ()
        for record in records:
            record.jobs.unfreeze()

    def close_approvals(self) -> tuple[CloseApproval, ...] | None:
        """Collect decisions while retaining every original document."""
        approvals: list[CloseApproval] = []
        for page in self.pages:
            approval = self.approve_close(page)
            if approval is None:
                return None
            approvals.append(approval)
        return tuple(approvals)

    def flush_pending_edits(self) -> None:
        """Commit inline typing before checking already-approved content."""
        for page in self.pages:
            page.suspend()

    def approvals_current(self, approvals: tuple[CloseApproval, ...]) -> bool:
        """Validate ordered identities and exact content without mutating them."""
        valid = self.pages == tuple(item.page for item in approvals) and all(
            item.current for item in approvals
        )
        if not valid:
            self.statusBar().showMessage(
                "A document changed while closing. Review it and close again."
            )
        return valid

    def commit_close_plan(self, approvals: tuple[CloseApproval, ...]) -> bool:
        """Close only the reserved states whose decisions are still current."""
        self._approved_close = approvals
        try:
            return self.close()
        finally:
            self._approved_close = None

    @property
    def pages(self) -> tuple[DocumentPage, ...]:
        """Return document pages in visible tab order."""
        return tuple(self._page_at(i) for i in range(self._tabs.count()))

    @property
    def tab_bar(self) -> DocumentTabBar:
        """Return the accessible tab strip."""
        return self._tab_bar

    @property
    def eligible(self) -> bool:
        """A closing or dialog-pinned host cannot receive incoming work."""
        modal = QApplication.activeModalWidget()
        blocked = modal is not None and self.isAncestorOf(modal)
        return (
            not self._closing
            and not blocked
            and not any(p.record.operations for p in self.pages)
        )

    @property
    def _page(self) -> DocumentPage:
        if self._page_ref is None:
            raise RuntimeError("No active document")
        return self._page_ref

    @property
    def _delivery(self) -> ImageDelivery:
        return (
            self._page.record.delivery
            if self._page_ref is not None
            else self._default_delivery
        )

    @contextmanager
    def _pin_document(self) -> Generator[None]:
        record = self._page.record
        record.operations += 1
        try:
            yield
        finally:
            record.operations -= 1

    @property
    def _session(self) -> EditorSession:
        return self._page.record.session

    @property
    def _canvas(self) -> CanvasView:
        return self._page.canvas

    @property
    def _renderer(self) -> Renderer:
        return self._page.renderer

    @property
    def _tools(self) -> dict[ToolId, Tool]:
        return self._page.tools

    @property
    def _title(self) -> str:
        return self._page.record.title

    @property
    def _saved_path(self) -> Path | None:
        return self._page.record.saved_path

    @_saved_path.setter
    def _saved_path(self, path: Path | None) -> None:
        self._page.record.saved_path = path

    @property
    def _copied(self) -> bool:
        return self._page.record.copied

    @_copied.setter
    def _copied(self, copied: bool) -> None:
        self._page.record.copied = copied

    def _page_at(self, index: int) -> DocumentPage:
        page = self._tabs.widget(index)
        if not isinstance(page, DocumentPage):
            raise TypeError("Tab has no document")
        return page

    def add_document(
        self, record: DocumentRecord, *, activate: bool = True
    ) -> DocumentPage:
        """Register a new document and present it as an ordinary tab."""
        page = DocumentPage(record)
        page.setParent(self._workspace.parking)
        self._workspace.register_page(page)
        self.attach_page(page, self._tabs.count())
        if activate:
            self._tabs.setCurrentWidget(page)
        if not self._shown_once and len(self.pages) == 1:
            self.resize(self._initial_size())
        return page

    def attach_page(self, page: DocumentPage, index: int) -> None:
        """Attach an existing page without cloning its session or pixels."""
        inserted = self._tabs.insertTab(index, page, page.record.label)
        page.changed.connect(self._page_changed)
        page.tool_changed.connect(self._page_changed)
        page.canvas.zoom_changed.connect(self._page_zoom_changed)
        page.canvas.cursor_moved.connect(self._show_position)
        page.canvas.escape_unhandled.connect(self._on_escape)
        page.canvas.enter_unhandled.connect(self._on_enter)
        self._tab_bar.setTabData(inserted, page.record.identifier)
        self._tab_bar.setTabToolTip(inserted, page.record.label)
        self._page_changed()

    def take_page(self, page: DocumentPage) -> int:
        """Detach presentation only; Workspace retains the document lifetime."""
        index = self._tabs.indexOf(page)
        if index < 0:
            raise ValueError("Document is not in this window")
        page.remember_viewport()
        page.suspend()
        page.changed.disconnect(self._page_changed)
        page.tool_changed.disconnect(self._page_changed)
        page.canvas.zoom_changed.disconnect(self._page_zoom_changed)
        page.canvas.cursor_moved.disconnect(self._show_position)
        page.canvas.escape_unhandled.disconnect(self._on_escape)
        page.canvas.enter_unhandled.disconnect(self._on_enter)
        self._tabs.removeTab(index)
        page.setParent(self._workspace.parking)
        self._page_changed()
        return index

    def activate_page(self, page: DocumentPage) -> None:
        """Select a stable page identity, independent of earlier tab indices."""
        self._tabs.setCurrentWidget(page)

    def _tab_changed(self, index: int) -> None:
        if index < 0:
            return
        page = self._page_at(index)
        previous = self._page_ref
        if previous is not None and previous is not page:
            if previous.record.operations:
                self._tabs.setCurrentWidget(previous)
                return
            previous.suspend()
        self._page_ref = page
        self._page_changed()
        if self.isVisible():
            page.present()
            if (
                QApplication.activePopupWidget() is None
                and QApplication.activeModalWidget() is None
            ):
                page.canvas.setFocus()

    def _page_zoom_changed(self, _zoom: float) -> None:
        self._refresh_zoom()

    def _page_changed(self) -> None:
        for index, page in enumerate(self.pages):
            self._tabs.setTabText(index, page.record.label)
            self._tabs.setTabToolTip(index, page.record.label)
            state = (
                "delivered"
                if page.record.session.history.is_delivered
                else "not saved or copied"
            )
            self._tab_bar.setAccessibleTabName(
                index, f"{page.record.label}, {index + 1} of {len(self.pages)}, {state}"
            )
            for side in (
                DocumentTabBar.ButtonPosition.LeftSide,
                DocumentTabBar.ButtonPosition.RightSide,
            ):
                button = cast(QWidget | None, self._tab_bar.tabButton(index, side))
                if button is not None:
                    button.setAccessibleName(f"Close {page.record.label}")
        if self._tabs.count():
            self._refresh_chrome()

    def close_tab(self, index: int = -1) -> bool:
        """Close one tab with a document-specific save/discard/cancel decision."""
        if self._closing or self._workspace.transferring:
            return False
        if index < 0:
            index = self._tabs.currentIndex()
        if index < 0:
            return True
        page = self._page_at(index)
        self._closing = True
        page.record.jobs.freeze()
        try:
            approval = self.approve_close(page)
            page.suspend()
            if approval is None or not approval.current:
                return False
            if not self._workspace.close_documents(((self, (approval,)),)):
                return False
        finally:
            self._closing = False
            page.record.jobs.unfreeze()
        if not self.pages:
            self.close()
        return True

    def approve_close(self, page: DocumentPage) -> CloseApproval | None:
        """Ask once while retaining every page until the caller commits close."""
        if page.record.operations or page.record.jobs.blocks_close:
            self.statusBar().showMessage(
                "Finish the pending output, or wait for cancellation "
                "to complete, before closing."
            )
            return None
        page.suspend()
        if not self._confirm_close or page.record.session.history.is_delivered:
            return CloseApproval(page, page.record.session.history.state_id)
        previous = self._page_ref
        self.activate_page(page)
        record = page.record
        record.operations += 1
        requested_state = record.session.history.state_id
        try:
            name = record.saved_path.name if record.saved_path is not None else None
            choice = ClosePrompt.describe(file_name=name, copied=record.copied).ask(
                self
            )
            if choice is CloseChoice.DISCARD:
                return CloseApproval(page, requested_state)
            if (
                choice is CloseChoice.SAVE
                and self.save()
                and record.session.history.is_delivered
            ):
                return CloseApproval(page, record.session.history.state_id)
            return None
        finally:
            record.operations -= 1
            if previous is not None and previous in self.pages:
                self.activate_page(previous)

    def _tab_context(self, index: int, position: object) -> None:
        self._workspace.show_tab_menu(self, self._page_at(index), position)

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
        self._page.activate_tool(tool)
        self._tool_actions[tool].setChecked(True)
        self._hint.setText(self._tools[tool].hint)
        self._sync_style_bar()

    def edit_text(
        self, note: TextNote | None, at: Point, box: Rect | None = None
    ) -> None:
        """Open the inline text editor."""
        self._canvas.editors.edit_text(note, at, box)

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
        record = self._page.record
        if not self._deliver(lambda: self._delivery.copy(self.flattened())):
            return False
        record.copied = True
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
        with self._pin_document():
            return self._choose_save_as()

    def _choose_save_as(self) -> bool:
        """Keep the initiating record pinned through the native picker."""
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
        with self._pin_document():
            return self._print_document()

    def _print_document(self) -> bool:
        """Keep printing attached to its originating document and host."""
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
        self.setCentralWidget(self._tabs)
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_bar()
        self._tabs.currentChanged.connect(self._tab_changed)
        self._tab_bar.tabCloseRequested.connect(self.close_tab)
        self._tab_bar.context_requested.connect(self._tab_context)
        self._style_bar.style_changed.connect(self._on_style_changed)
        self._style_bar.auto_fit_toggled.connect(self._on_auto_fit_toggled)
        self.resize(900, 650)

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
        self._add_action("close", "&Close", "Ctrl+W", self.close_tab)
        self._add_action(
            "help",
            "&Documentation",
            QKeySequence.StandardKey.HelpContents,
            lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)),
        )
        self._add_action("about", f"&About {APP_NAME}", None, self.about_requested.emit)
        self._add_action("undo", "&Undo", QKeySequence.StandardKey.Undo, self._undo)
        redo = self._add_action("redo", "&Redo", "Ctrl+Y", self._redo)
        redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        # Toolbar buttons keep short labels; menus show "Undo Draw arrow"
        self._actions["undo"].setIconText("Undo")
        redo.setIconText("Redo")
        self._add_action("copy", "&Copy", QKeySequence.StandardKey.Copy, self._copy)
        self._add_action("paste", "&Paste", QKeySequence.StandardKey.Paste, self._paste)
        delete = self._add_action("delete", "&Delete", "Delete", self._delete_selected)
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
            lambda: self._session.restack_selected(forward=True),
        )
        self._add_action(
            "backward",
            "Send &backward",
            "Ctrl+[",
            lambda: self._session.restack_selected(forward=False),
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
        zoom_in = self._add_action("zoom_in", "Zoom &in", "Ctrl+=", self._zoom_in)
        zoom_in.setShortcuts([QKeySequence("Ctrl+="), QKeySequence("Ctrl++")])
        self._add_action("zoom_out", "Zoom &out", "Ctrl+-", self._zoom_out)
        self._add_action("zoom_actual", "&Actual size", "Ctrl+0", self._zoom_actual)
        self._add_action(
            "zoom_fit",
            "&Fit to window",
            "Ctrl+Shift+F",
            self._zoom_fit,
        )
        self._build_tab_actions()
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

    def _build_tab_actions(self) -> None:
        """Build window-scoped tab commands separately from document tools."""
        self._add_action("next_tab", "Next tab", "Ctrl+Tab", lambda: self._cycle_tab(1))
        self._add_action(
            "previous_tab",
            "Previous tab",
            "Ctrl+Shift+Tab",
            lambda: self._cycle_tab(-1),
        )
        self._add_action(
            "detach_tab",
            "Move tab to new window",
            "Ctrl+Shift+N",
            lambda: self._workspace.move(self, self._page),
        )
        self._add_action(
            "tab_left", "Move tab left", "Ctrl+Shift+PgUp", lambda: self._move_tab(-1)
        )
        self._add_action(
            "tab_right",
            "Move tab right",
            "Ctrl+Shift+PgDown",
            lambda: self._move_tab(1),
        )
        for number in range(1, 10):
            self._add_action(
                f"tab_{number}",
                f"Activate tab {number}",
                f"Ctrl+{number}",
                lambda n=number: self._activate_number(n),
            )

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
        tabs = bar.addMenu("&Window")
        for name in ("next_tab", "previous_tab", "tab_left", "tab_right", "detach_tab"):
            tabs.addAction(self._actions[name])
        tabs.aboutToShow.connect(
            lambda: self._workspace.populate_window_menu(tabs, self)
        )
        tools = bar.addMenu("&Tools")
        for action in self._tool_actions.values():
            tools.addAction(action)
        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self._actions["help"])
        help_menu.addSeparator()
        help_menu.addAction(self._actions["about"])

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
        actions.setMinimumHeight(height)
        self._style_bar.setMinimumHeight(height)
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
        self._style_bar.set_icons(self._icons)
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
            if self._page_ref is not None:
                self._page.present()
                self._canvas.setFocus()

    @override
    def changeEvent(self, event: QEvent) -> None:
        """Redraw tool icons in the new ink color when the theme changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and self._chrome_bars:
            self._apply_chrome()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Approve and revalidate content before removing any tab."""
        self._tab_bar.cancel_drag()
        if self._workspace.transferring or (
            self._closing and self._approved_close is None
        ):
            event.ignore()
            return
        owns_plan = not self._closing
        if owns_plan:
            self.begin_close_plan()
        try:
            approvals = self._approved_close
            if approvals is None:
                approvals = self.close_approvals()
            self.flush_pending_edits()
            if approvals is None or not self.approvals_current(approvals):
                event.ignore()
                return
            if not self._workspace.close_documents(((self, approvals),)):
                event.ignore()
                return
            self._workspace.forget_window(self)
            event.accept()
        finally:
            if owns_plan:
                self.end_close_plan()

    @override
    def event(self, event: QEvent) -> bool:
        """Track editor activation without treating popups/overlays as hosts."""
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "_workspace"):
            self._workspace.activated(self)
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "_tab_bar"):
            self._tab_bar.cancel_drag()
        return super().event(event)

    def _delete_selected(self) -> None:
        self._session.delete_selected()

    def _zoom_in(self) -> None:
        self._canvas.zoom_in()

    def _zoom_out(self) -> None:
        self._canvas.zoom_out()

    def _zoom_actual(self) -> None:
        self._canvas.zoom_actual()

    def _zoom_fit(self) -> None:
        self._canvas.zoom_fit()

    def _cycle_tab(self, direction: int) -> None:
        if self._tabs.count():
            self._tabs.setCurrentIndex(
                (self._tabs.currentIndex() + direction) % self._tabs.count()
            )

    def _activate_number(self, number: int) -> None:
        index = self._tabs.count() - 1 if number == LAST_TAB_NUMBER else number - 1
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def _move_tab(self, direction: int) -> None:
        self._workspace.move(
            self, self._page, self, self._tabs.currentIndex() + direction
        )

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
        page = self._page
        if not selecting or len(page.record.session.selection) != 0:
            return
        if self.copy_image() and page in self.pages:
            self.close_tab(self.pages.index(page))

    def _on_auto_fit_toggled(self, on: bool) -> None:  # noqa: FBT001 — Qt signal payload
        """Size selected text to its text, or keep its box, after finishing typing."""
        self._canvas.editors.commit()
        self._session.set_auto_fit(on=on, fit=Renderer.fit)

    def _on_style_changed(self, change: Callable[[Style], Style]) -> None:
        """Apply a style edit to the selection and to the matching tool defaults.

        An open text editor is committed first, as if the bar had taken focus,
        so the edit and the text being typed can't drift apart.
        """
        self._canvas.editors.commit()
        selected = self._session.selected()
        if selected:
            self._session.restyle_selected(change, Renderer.fit)
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
        with self._pin_document():
            self._choose_resize()

    def _choose_resize(self) -> None:
        """Keep the resize dialog bound to the initiating document."""
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
        """Pin the originating page and mark only its exact exported state."""
        record = self._page.record
        with self._pin_document():
            self._canvas.editors.commit()
            state_id = record.session.history.state_id
            try:
                run()
            except AppError as err:
                QMessageBox.warning(self, "VerdiClip", str(err))
                return False
            if mark:
                record.session.history.mark_delivered(expected_state_id=state_id)
            return True

    def _save_to(self, path: Path) -> bool:
        """Save to ``path`` and remember it on the originating document."""
        record = self._page.record
        if not self._deliver(lambda: self._delivery.save(self.flattened(), path)):
            return False
        record.saved_path = path
        self.statusBar().showMessage(f"Saved {path}", STATUS_TIMEOUT_MS)
        self._page_changed()
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
            first = selected[0]
            fit = TextFit.NONE
            if isinstance(first, TextNote):
                fit = TextFit.AUTO if first.auto_fit else TextFit.FIXED
            tool = KIND_TOOLS[first.kind]
            self._style_bar.show_for(FIELD_LAYOUT[tool], first.style, fit)
            return
        tool = self._current_tool_id()
        self._style_bar.show_for(FIELD_LAYOUT[tool], self._session.styles.get(tool))

    def _refresh_chrome(self) -> None:
        """Update title, undo/redo state, and size label."""
        if self._page_ref is None:
            return
        history = self._session.history
        tool = self._current_tool_id()
        self._tool_actions[tool].setChecked(True)
        self._hint.setText(self._tools[tool].hint)
        self._actions["undo"].setEnabled(history.can_undo)
        self._actions["redo"].setEnabled(history.can_redo)
        self._actions["undo"].setText(f"&Undo {history.undo_text}".strip())
        self._actions["redo"].setText(f"&Redo {history.redo_text}".strip())
        name = self._saved_path.name if self._saved_path is not None else self._title
        marker = "" if history.is_delivered else " •"
        self.setWindowTitle(f"{name}{marker} — {self._application_label}")
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
