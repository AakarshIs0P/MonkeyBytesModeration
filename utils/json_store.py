"""Concurrency-safe atomic JSON persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("bot.json_store")


class AtomicJSONStore:
    def __init__(self, path: str | Path, default_factory: Callable[[], dict]):
        self.path = Path(path)
        self.default_factory = default_factory
        self.lock = asyncio.Lock()
        self.data = default_factory()
        self.dirty = False

    async def load(self) -> dict:
        async with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stale_temp = self.path.with_suffix(self.path.suffix + ".tmp")
            if stale_temp.exists():
                try:
                    await asyncio.to_thread(stale_temp.unlink)
                except OSError:
                    log.warning("Could not remove stale JSON temp file %s", stale_temp)
            if not self.path.exists():
                self.data = self.default_factory()
                return self.data
            try:
                raw = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ValueError("JSON root must be an object")
                self.data = loaded
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
                try:
                    await asyncio.to_thread(os.replace, self.path, backup)
                except OSError:
                    log.exception("Could not preserve malformed JSON file %s", self.path)
                log.warning("Recovered malformed JSON %s: %s", self.path, exc)
                self.data = self.default_factory()
                self.dirty = True
            return self.data

    def mark_dirty(self) -> None:
        self.dirty = True

    async def flush(self, force: bool = False) -> None:
        async with self.lock:
            if not force and not self.dirty:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = json.dumps(self.data, indent=2, sort_keys=True)
            await asyncio.to_thread(temp.write_text, payload, encoding="utf-8")
            await asyncio.to_thread(os.replace, temp, self.path)
            self.dirty = False
