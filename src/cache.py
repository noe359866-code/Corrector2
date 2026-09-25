"""Caché SQLite LOCAL (dentro de la corrida): títulos que se repiten mucho
(1080p/720p/2160p de lo mismo) no consultan las APIs dos veces."""
from __future__ import annotations

import json
import sqlite3
import threading


class LocalCache:
    def __init__(self, path: str = "cache.sqlite") -> None:
        self.path = path
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self.db.execute(
                "create table if not exists title_cache "
                "(key text primary key, ids text, source text, "
                " confidence real, updated text)")
            self.db.execute(
                "create table if not exists miss_cache "
                "(key text primary key, updated text)")
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def get(self, key: str) -> dict | None:
        """Hit -> {'ids':..., 'source':..., 'confidence':...} | None."""
        with self._lock:
            row = self.db.execute(
                "select ids, source, confidence from title_cache where key=?",
                (key,)).fetchone()
        if not row:
            return None
        try:
            return {"ids": json.loads(row[0]), "source": row[1],
                    "confidence": row[2]}
        except (ValueError, TypeError):
            return None

    def set(self, key: str, ids: dict, source: str, confidence: float) -> None:
        with self._lock:
            self.db.execute(
                "insert or replace into title_cache "
                "(key, ids, source, confidence, updated) "
                "values (?, ?, ?, ?, datetime('now'))",
                (key, json.dumps(ids), source, confidence))
            self.db.commit()

    def is_miss(self, key: str) -> bool:
        with self._lock:
            row = self.db.execute(
                "select 1 from miss_cache where key=?", (key,)).fetchone()
        return row is not None

    def set_miss(self, key: str) -> None:
        with self._lock:
            self.db.execute(
                "insert or ignore into miss_cache (key, updated) "
                "values (?, datetime('now'))", (key,))
            self.db.commit()

    def stats(self) -> dict:
        with self._lock:
            titles = self.db.execute("select count(*) from title_cache").fetchone()[0]
            misses = self.db.execute("select count(*) from miss_cache").fetchone()[0]
        return {"titulos_cache": titles, "sin_match_cache": misses}
