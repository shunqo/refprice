"""SQLite persistence for the reference series.

Ticks accumulate. That is the point: a reference rate that only exists for the last four minutes is
a calculator, and a reference rate with a week behind it is something you can argue a settlement
dispute with.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from refprice.methodology import ReferenceTick


@dataclass(frozen=True, slots=True)
class StoreStats:
    """What the store holds. A dataclass rather than a dict so the page cannot format a string
    where it meant an integer and only find out at render time."""

    n_ticks: int
    n_defined: int
    n_runs: int
    first_tick: str | None
    last_tick: str | None

    @property
    def coverage_pct(self) -> float:
        return (self.n_defined / self.n_ticks * 100.0) if self.n_ticks else 0.0


SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    at            TEXT PRIMARY KEY,
    price         REAL,
    n_contributors INTEGER NOT NULL,
    undefined_reason TEXT
);
CREATE TABLE IF NOT EXISTS contributions (
    at            TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    mid           REAL NOT NULL,
    volume        REAL NOT NULL,
    included      INTEGER NOT NULL,
    excluded_reason TEXT,
    deviation_bp  REAL,
    PRIMARY KEY (at, source_id)
);
CREATE INDEX IF NOT EXISTS ix_contrib_source ON contributions(source_id);
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    n_ticks    INTEGER NOT NULL,
    n_defined  INTEGER NOT NULL,
    n_venues_ok INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def save_ticks(self, ticks: Iterable[ReferenceTick]) -> int:
        ticks = list(ticks)
        with self._tx() as conn:
            conn.executemany(
                "INSERT INTO ticks (at, price, n_contributors, undefined_reason) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(at) DO UPDATE SET price=excluded.price, "
                "n_contributors=excluded.n_contributors, undefined_reason=excluded.undefined_reason",
                [(t.at.isoformat(), t.price, t.n_contributors, t.undefined_reason) for t in ticks],
            )
            conn.executemany(
                "INSERT INTO contributions (at, source_id, mid, volume, included, excluded_reason, "
                "deviation_bp) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(at, source_id) DO UPDATE SET mid=excluded.mid, volume=excluded.volume, "
                "included=excluded.included, excluded_reason=excluded.excluded_reason, "
                "deviation_bp=excluded.deviation_bp",
                [
                    (
                        t.at.isoformat(),
                        c.source_id,
                        c.mid,
                        c.volume,
                        int(c.included),
                        c.excluded_reason,
                        c.deviation_bp,
                    )
                    for t in ticks
                    for c in t.contributions
                ],
            )
        return len(ticks)

    def record_run(self, ticks: list[ReferenceTick], n_venues_ok: int) -> int:
        from datetime import UTC, datetime

        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO runs (at, n_ticks, n_defined, n_venues_ok) VALUES (?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    len(ticks),
                    sum(1 for t in ticks if t.is_defined),
                    n_venues_ok,
                ),
            )
        return int(cur.lastrowid or 0)

    def stats(self) -> StoreStats:
        row = self._conn.execute(
            "SELECT COUNT(*) n, SUM(price IS NOT NULL) defined, MIN(at) first, MAX(at) last FROM ticks"
        ).fetchone()
        runs = self._conn.execute("SELECT COUNT(*) n FROM runs").fetchone()
        return StoreStats(
            n_ticks=int(row["n"] or 0),
            n_defined=int(row["defined"] or 0),
            n_runs=int(runs["n"] or 0),
            first_tick=row["first"],
            last_tick=row["last"],
        )

    def recent_prices(self, limit: int = 240) -> list[tuple[str, float | None]]:
        rows = self._conn.execute(
            "SELECT at, price FROM ticks ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [(r["at"], r["price"]) for r in reversed(rows)]

    def undefined_minutes(self, limit: int = 12) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT at, n_contributors, undefined_reason FROM ticks WHERE price IS NULL "
                "ORDER BY at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        )

    def exclusion_counts(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT source_id, COUNT(*) n, SUM(included=0) excluded FROM contributions "
                "GROUP BY source_id ORDER BY source_id"
            ).fetchall()
        )
