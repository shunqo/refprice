"""Alignment, windowing and the publication lag. No network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mdq.models import Bar, BarSeries, Instrument

from refprice.cli import split_final
from refprice.collect import Collected, quotes_by_minute, shared_window_start
from refprice.config import Config, VenueSpec
from refprice.methodology import Methodology, ReferenceTick

T0 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
FIVE = timedelta(minutes=5)
INST = Instrument("BTC-USDT", "BTC", "USDT", "spot")

VENUES = (
    VenueSpec("okx", "BTC-USDT", "BTC", "USDT", label="okx"),
    VenueSpec("bybit", "BTCUSDT", "BTC", "USDT", label="bybit"),
)


def series(source_id: str, n: int, start: datetime = T0, price: float = 100.0) -> BarSeries:
    bars = tuple(
        Bar(
            open_time=start + FIVE * i,
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=1.0,
        )
        for i in range(n)
    )
    return BarSeries(
        source_id=source_id, instrument=INST, interval=FIVE, bars=bars, fetched_at=start + FIVE * n
    )


def collected(**series_by_key: BarSeries) -> Collected:
    return Collected(series=dict(series_by_key), health=(), fetched_at=T0 + FIVE * 100)


class TestSharedWindow:
    def test_the_start_is_the_latest_first_bar(self) -> None:
        """Venues honour a `limit` differently. One live run: Kraken 720 bars, Coinbase 350, OKX
        and Bybit 239 each. Outside the overlap the index would report 'not enough contributors'
        for reasons that say nothing about the venues."""
        c = collected(okx=series("okx", 10), bybit=series("bybit", 40, start=T0 - FIVE * 30))
        assert shared_window_start(c) == T0

    def test_no_data_means_no_window(self) -> None:
        assert shared_window_start(collected()) is None

    def test_quotes_before_the_shared_start_are_dropped(self) -> None:
        c = collected(okx=series("okx", 3), bybit=series("bybit", 10, start=T0 - FIVE * 7))
        grouped = quotes_by_minute(c, VENUES, start=shared_window_start(c))
        assert min(grouped) == T0
        assert all(len(v) == 2 for v in grouped.values())

    def test_alignment_is_on_bar_open_time_not_arrival(self) -> None:
        """A reference price for 12:05 is built from what each venue said about 12:05, never from
        whatever each venue happened to publish most recently."""
        a = series("okx", 3)
        b = series("bybit", 3, start=T0 + FIVE)  # one interval behind
        grouped = quotes_by_minute(collected(okx=a, bybit=b), VENUES)
        assert [len(v) for _, v in sorted(grouped.items())] == [1, 2, 2, 1]


class TestPublicationLag:
    def _ticks(self, n: int, start: datetime) -> list[ReferenceTick]:
        return [ReferenceTick(at=start + FIVE * i, price=1.0, n_contributors=4) for i in range(n)]

    def test_recent_intervals_are_provisional(self) -> None:
        cfg = Config(interval=FIVE, publication_lag=timedelta(minutes=5))
        now = T0 + FIVE * 10
        final, provisional = split_final(cfg, self._ticks(10, T0), now)
        # cutoff = now - 5m - 5m; intervals at cutoff or earlier are final.
        assert provisional and final
        assert max(t.at for t in final) <= now - FIVE - cfg.publication_lag
        assert min(t.at for t in provisional) > now - FIVE - cfg.publication_lag

    def test_a_longer_lag_holds_back_more(self) -> None:
        now = T0 + FIVE * 10
        short, _ = split_final(
            Config(interval=FIVE, publication_lag=timedelta(0)), self._ticks(10, T0), now
        )
        long_, _ = split_final(
            Config(interval=FIVE, publication_lag=timedelta(minutes=20)), self._ticks(10, T0), now
        )
        assert len(long_) < len(short)

    def test_nothing_is_final_when_the_lag_exceeds_the_window(self) -> None:
        now = T0 + FIVE
        final, provisional = split_final(
            Config(interval=FIVE, publication_lag=timedelta(hours=2)), self._ticks(5, T0), now
        )
        assert final == []
        assert len(provisional) == 5


class TestConfigGuards:
    def test_a_venue_set_with_no_spare_is_refused(self) -> None:
        """With exactly `min_contributors` venues, any single outlier makes every such interval
        undefined. The config refuses rather than letting that be discovered in production."""
        three = VENUES + (VenueSpec("kraken", "XBTUSDT", "BTC", "USDT", label="kraken"),)
        with pytest.raises(ValueError, match="spare"):
            Config(venues=three, methodology=Methodology(min_contributors=3))

    def test_the_shipped_default_has_a_spare(self) -> None:
        cfg = Config()
        assert len(cfg.venues) > cfg.methodology.min_contributors

    def test_the_default_interval_is_the_measured_one(self) -> None:
        assert Config().interval == timedelta(minutes=5)


class TestExitCode:
    """A cron alarm that is always on is not an alarm."""

    cfg = Config()

    def _t(self, defined: bool, i: int = 0) -> ReferenceTick:
        return ReferenceTick(
            at=T0 + FIVE * i,
            price=100.0 if defined else None,
            n_contributors=4 if defined else 1,
            undefined_reason=None if defined else "too few",
        )

    def test_all_recent_priced_and_all_venues_up_is_clean(self) -> None:
        from refprice.cli import EXIT_OK, exit_code

        ticks = [self._t(True, i) for i in range(30)]
        assert exit_code(self.cfg, ticks, n_ok=len(self.cfg.venues)) == EXIT_OK

    def test_an_old_unpriced_interval_does_not_keep_the_alarm_on_forever(self) -> None:
        """The bug this constant exists to prevent: one bad interval three hours ago must not make
        every run for the rest of the day exit non-zero."""
        from refprice.cli import EXIT_OK, exit_code

        ticks = [self._t(i != 0, i) for i in range(40)]
        assert exit_code(self.cfg, ticks, n_ok=len(self.cfg.venues)) == EXIT_OK

    def test_a_recent_unpriced_interval_is_degraded(self) -> None:
        from refprice.cli import EXIT_DEGRADED, exit_code

        ticks = [self._t(True, i) for i in range(30)]
        ticks[-3] = self._t(False, 27)
        assert exit_code(self.cfg, ticks, n_ok=len(self.cfg.venues)) == EXIT_DEGRADED

    def test_the_latest_interval_being_unpriced_is_bad_not_degraded(self) -> None:
        from refprice.cli import EXIT_BAD, exit_code

        ticks = [self._t(True, i) for i in range(30)] + [self._t(False, 30)]
        assert exit_code(self.cfg, ticks, n_ok=len(self.cfg.venues)) == EXIT_BAD

    def test_a_venue_being_down_is_bad_even_if_the_price_is_fine(self) -> None:
        from refprice.cli import EXIT_BAD, exit_code

        ticks = [self._t(True, i) for i in range(30)]
        assert exit_code(self.cfg, ticks, n_ok=len(self.cfg.venues) - 1) == EXIT_BAD

    def test_no_ticks_at_all_is_bad(self) -> None:
        from refprice.cli import EXIT_BAD, exit_code

        assert exit_code(self.cfg, [], n_ok=4) == EXIT_BAD
