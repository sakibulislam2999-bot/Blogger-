"""Tiny JSON-backed store used for de-duplication and failure tracking."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
MAX_ENTRIES = 5000


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {"done": {}, "failures": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self._data["done"] = loaded.get("done", {})
            self._data["failures"] = loaded.get("failures", {})
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state file (%s). Starting fresh.", exc)

    def _save(self) -> None:
        done = self._data["done"]
        if len(done) > MAX_ENTRIES:  # prune oldest
            for key in list(done)[: len(done) - MAX_ENTRIES]:
                done.pop(key, None)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)  # atomic
        except OSError:
            logger.exception("Failed to save state file")
            if os.path.exists(tmp):
                os.unlink(tmp)

    def seen(self, uid: str) -> bool:
        return uid in self._data["done"]

    def mark_done(self, uid: str, **meta) -> None:
        self._data["done"][uid] = {"at": datetime.now(timezone.utc).isoformat(), **meta}
        self._data["failures"].pop(uid, None)
        self._save()

    def record_failure(self, uid: str) -> int:
        count = self._data["failures"].get(uid, 0) + 1
        self._data["failures"][uid] = count
        self._save()
        return count
