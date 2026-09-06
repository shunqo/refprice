"""Command line.

    refprice run      collect, compute, store, write the page
    refprice serve    serve the page (localhost by default; a wildcard bind is refused)
    refprice show     print the latest reference price and the settlement-risk table

Exit codes match `mdq`'s: 0 clean, 1 degraded, 2 a venue is down or the latest minute is undefined,
3 could not run at all.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from mdq.models import SourceStatus
from mdq.server import BindRefused, serve

from refprice.collect import collect, quotes_by_minute, shared_window_start
from refprice.config import Config
from refprice.methodology import ReferenceTick, compute_tick, settlement_risk
from refprice.render import render
from refprice.store import Store

EXIT_OK, EXIT_DEGRADED, EXIT_BAD, EXIT_CANNOT_RUN = 0, 1, 2, 3


def build_ticks(config: Config, collected) -> list[ReferenceTick]:
    grouped = quotes_by_minute(collected, config.venues, start=shared_window_start(collected))
    return [compute_tick(at, quotes, config.methodology) for at, quotes in grouped.items()]


def split_final(config: Config, ticks: list[ReferenceTick], now: datetime) -> tuple[list, list]:
    """(final, provisional). A minute is final once every venue has had `publication_lag` to
    report it; before that it is computed, stored and recomputed, but never headlined."""
    cutoff = now - config.interval - config.publication_lag
    return [t for t in ticks if t.at <= cutoff], [t for t in ticks if t.at > cutoff]


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    collected = collect(config, now=now)
    ticks = build_ticks(config, collected)
    final, provisional = split_final(config, ticks, now)
    # Settlement risk is measured on final minutes only. Including provisional ones would count a
    # venue's ordinary publication lag as a price disagreement, which is a different thing.
    risk = settlement_risk(final)
    n_ok = sum(1 for h in collected.health if h.status is SourceStatus.OK)

    with Store(config.db_path) as store:
        store.save_ticks(ticks)
        store.record_run(ticks, n_ok)
        stats = store.stats()
        history = [p for _, p in store.recent_prices(360)]
        undefined = [
            (r["at"], r["n_contributors"], r["undefined_reason"] or "")
            for r in store.undefined_minutes(12)
        ]

    config.html_path.parent.mkdir(parents=True, exist_ok=True)
    config.html_path.write_text(
        render(
            config,
            final,
            collected.health,
            risk,
            history=history,
            stats=stats,
            undefined=undefined,
            n_provisional=len(provisional),
        ),
        encoding="utf-8",
    )

    defined = sum(1 for t in final if t.is_defined)
    if not args.quiet:
        print(
            f"{n_ok}/{len(config.venues)} venues ok · {len(final)} final minutes "
            f"(+{len(provisional)} provisional) · {defined} with a reference price "
            f"({defined / max(len(final), 1):.1%}) · store holds {stats.n_ticks} intervals"
        )
        for h in sorted(collected.health, key=lambda x: x.source_id):
            latency = f"{h.latency_ms:.0f}ms" if h.latency_ms is not None else "--"
            print(
                f"  {h.status.value:8} {h.source_id:12} {h.bars_returned:>5} bars {latency:>8}"
                + (f"  {h.error}" if h.error else "")
            )
        print(f"  {'venue':12} {'median bp':>10} {'p95 bp':>8} {'worst bp':>9} {'excluded':>9}")
        for sid, r in sorted(risk.items()):
            print(
                f"  {sid:12} {r.median_deviation_bp:>10.2f} {r.p95_deviation_bp:>8.2f} "
                f"{r.max_deviation_bp:>9.2f} {r.n_excluded:>9}"
            )
        print(f"  page -> {config.html_path}")

    return exit_code(config, final or ticks, n_ok)


RECENT_INTERVALS_JUDGED = 12
"""How many of the most recent final intervals decide the exit code -- one hour at five minutes.

Not the whole lookback window. The window always contains the same handful of old intervals that
could not be priced, so judging on it would make the job exit non-zero on every single run, for
ever. A cron alarm that is always on is not an alarm. What matters operationally is whether the
index is healthy *now*.
"""


def exit_code(config: Config, ticks: list[ReferenceTick], n_ok: int) -> int:
    latest = ticks[-1] if ticks else None
    if latest is None or not latest.is_defined:
        return EXIT_BAD
    if n_ok < len(config.venues):
        return EXIT_BAD
    recent = ticks[-RECENT_INTERVALS_JUDGED:]
    return EXIT_OK if all(t.is_defined for t in recent) else EXIT_DEGRADED


def cmd_show(config: Config, args: argparse.Namespace) -> int:  # noqa: ARG001
    now = datetime.now(UTC)
    collected = collect(config, now=now)
    final, _ = split_final(config, build_ticks(config, collected), now)
    latest = final[-1] if final else None
    if latest is None:
        print("no data")
        return EXIT_BAD
    if latest.is_defined and latest.price is not None:
        print(
            f"{config.index_name}: {latest.price:,.2f} at {latest.at:%Y-%m-%d %H:%M} UTC "
            f"({latest.n_contributors} contributors)"
        )
    else:
        print(
            f"{config.index_name}: UNDEFINED at {latest.at:%Y-%m-%d %H:%M} UTC — "
            f"{latest.undefined_reason}"
        )
    for c in sorted(latest.contributions, key=lambda c: c.source_id):
        mark = "  " if c.included else "x "
        dev = f"{c.deviation_bp:6.2f} bp" if c.deviation_bp is not None else "        "
        print(
            f"  {mark}{c.source_id:12} {c.mid:>12,.2f}  vol {c.volume:>10.4f}  {dev}"
            + (f"  {c.excluded_reason}" if c.excluded_reason else "")
        )
    return EXIT_OK if latest.is_defined else EXIT_BAD


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    try:
        serve(config.html_path, args.host or config.bind_host, args.port or config.bind_port)
    except BindRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    except KeyboardInterrupt:
        print("\nstopped")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="refprice", description="Cross-venue reference price.")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--db", type=Path, default=None, help="override the SQLite path")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="collect, compute, store and write the page")
    sub.add_parser("show", help="print the latest reference price and its contributors")
    s = sub.add_parser("serve", help="serve the page written by `run`")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    return p


COMMANDS = {"run": cmd_run, "show": cmd_show, "serve": cmd_serve}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config()
    if args.db:
        config = Config(db_path=args.db)
    return COMMANDS[args.command](config, args)


if __name__ == "__main__":
    raise SystemExit(main())
