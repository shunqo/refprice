"""Fetch bars from each venue and turn them into per-minute quote sets.

Everything about the network -- pacing, retries, refusals, unclosed-bar handling, timestamp
normalisation -- comes from `mdq.sources` unchanged. This module only aligns and shapes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from mdq.models import BarSeries, Instrument, SourceHealth, SourceStatus
from mdq.sources import get_source
from mdq.sources.base import SourceBlocked, SourceError, SourceUnavailable

from refprice.config import Config, VenueSpec
from refprice.methodology import Quote


@dataclass(frozen=True, slots=True)
class Collected:
    series: dict[str, BarSeries]
    health: tuple[SourceHealth, ...]
    fetched_at: datetime


def _status(exc: SourceError) -> SourceStatus:
    if isinstance(exc, SourceBlocked):
        return SourceStatus.BLOCKED
    if isinstance(exc, SourceUnavailable):
        return SourceStatus.ERROR
    return SourceStatus.ERROR


def collect(config: Config, *, now: datetime | None = None) -> Collected:
    now = now or datetime.now(UTC)
    series: dict[str, BarSeries] = {}
    health: list[SourceHealth] = []

    for venue in config.venues:
        source = get_source(venue.source_id)
        instrument = Instrument(venue.symbol, venue.base, venue.quote, venue.kind)
        started = time.monotonic()
        try:
            fetched = source.fetch(instrument, config.interval, config.lookback_bars)
        except SourceError as exc:
            health.append(
                SourceHealth(
                    source_id=venue.key,
                    venue=source.venue,
                    status=_status(exc),
                    checked_at=now,
                    latency_ms=(time.monotonic() - started) * 1000.0,
                    http_status=getattr(source, "last_http_status", None),
                    error=str(exc),
                )
            )
            continue
        series[venue.key] = fetched
        last = max((b.open_time for b in fetched.bars), default=None)
        health.append(
            SourceHealth(
                source_id=venue.key,
                venue=source.venue,
                status=SourceStatus.OK if fetched.bars else SourceStatus.STALE,
                checked_at=now,
                latency_ms=getattr(source, "last_latency_ms", None),
                http_status=getattr(source, "last_http_status", None),
                bars_returned=len(fetched),
                last_bar_age_s=(now - last).total_seconds() if last else None,
            )
        )
    return Collected(series=series, health=tuple(health), fetched_at=now)


def shared_window_start(collected: Collected) -> datetime | None:
    """The earliest minute every responding venue actually covers.

    Venues honour a `limit` differently -- in one live run Kraken returned 720 bars, Coinbase 350
    and OKX and Bybit 239 each. Computing a reference price outside the overlap would produce a
    long tail of minutes marked "not enough contributors" that say nothing about the venues and
    everything about how many bars each endpoint felt like returning. Those are not gaps in the
    index; they are the edge of the request.
    """
    starts = [min(b.open_time for b in s.bars) for s in collected.series.values() if s.bars]
    return max(starts) if starts else None


def quotes_by_minute(
    collected: Collected,
    venues: tuple[VenueSpec, ...],
    *,
    start: datetime | None = None,
) -> dict[datetime, list[Quote]]:
    """Group every venue's bars by bar-open time.

    Aligning on the bar's own open time and nothing else is deliberate: a reference price for
    12:03 must be built from what each venue said about 12:03, never from whatever each venue
    happened to publish most recently.
    """
    out: dict[datetime, list[Quote]] = {}
    for venue in venues:
        series = collected.series.get(venue.key)
        if not series:
            continue
        for bar in series.bars:
            if start and bar.open_time < start:
                continue
            out.setdefault(bar.open_time, []).append(
                Quote(source_id=venue.key, mid=bar.mid, volume=bar.volume)
            )
    return dict(sorted(out.items()))
