"""Remember each tool's last-used style across editors and app restarts."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from verdiclip.document.annotations import JsonObject, JsonValue
from verdiclip.document.codec import StyleCodec
from verdiclip.document.style import Style
from verdiclip.editor.session import ToolId
from verdiclip.exceptions import CodecError

logger = logging.getLogger(__name__)


class StyleMemory:
    """A small JSON file mapping tool names to their last-used style."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Return the file the styles are kept in."""
        return self._path

    def load(self) -> dict[ToolId, Style]:
        """Return remembered styles; unreadable or invalid entries are skipped."""
        raw = self._read()
        styles: dict[ToolId, Style] = {}
        for name, data in raw.items():
            try:
                styles[ToolId(name)] = StyleCodec.decode(data)
            except (ValueError, CodecError) as err:
                logger.warning("Ignoring remembered style for %s: %s", name, err)
        return styles

    def remember(self, tool: ToolId, style: Style) -> None:
        """Store ``style`` as the style for ``tool``."""
        raw = self._read()
        raw[tool.value] = StyleCodec.encode(style)
        self._write(raw)

    def remember_all(self, styles: Mapping[ToolId, Style]) -> None:
        """Replace every remembered style."""
        self._write({tool.value: StyleCodec.encode(s) for tool, s in styles.items()})

    def forget(self) -> None:
        """Forget every remembered style (new defaults take over)."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError as err:
            logger.warning("Could not reset remembered styles: %s", err)

    # Internals

    def _read(self) -> JsonObject:
        """Return the stored object, or {} if missing or unreadable."""
        if not self._path.exists():
            return {}
        try:
            # json.loads only ever produces JSON-compatible values
            data = cast("JsonValue", json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("Ignoring unreadable style memory %s: %s", self._path, err)
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: JsonObject) -> None:
        """Write ``data`` atomically; failures are logged, never raised."""
        temp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp.replace(self._path)
        except OSError as err:
            logger.warning("Could not remember styles in %s: %s", self._path, err)
