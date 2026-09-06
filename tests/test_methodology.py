"""The methodology, tested before it exists.

Everything about a reference price is in its exclusion rules and its refusal to publish a number it
cannot stand behind. That is the whole file.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from refprice.methodology import (
    Methodology,
    Quote,
    compute_tick,
    settlement_risk,
    volume_weighted_median,
)

AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def q(source: str, mid: float, volume: float = 1.0) -> Quote:
    return Quote(source_id=source, mid=mid, volume=volume)


class TestVolumeWeightedMedian:
    def test_equal_weights_is_the_ordinary_median(self) -> None:
        assert volume_weighted_median([(10.0, 1), (11.0, 1), (12.0, 1)]) == pytest.approx(11.0)

    def test_even_count_averages_the_two_middle_values(self) -> None:
        assert volume_weighted_median([(10.0, 1), (12.0, 1)]) == pytest.approx(11.0)

    def test_weight_pulls_the_answer_towards_the_heavy_venue(self) -> None:
        assert volume_weighted_median([(10.0, 1), (11.0, 1), (12.0, 20)]) == pytest.approx(12.0)

    def test_a_single_huge_venue_cannot_drag_the_price_past_its_own_quote(self) -> None:
        """The reason this is a median and not a volume-weighted mean. A venue with 99% of the
        volume and an absurd print moves a VWAP mean arbitrarily far; it can move a weighted median
        no further than its own quote, and only if it holds more than half the weight."""
        heavy = volume_weighted_median([(100.0, 1), (101.0, 1), (5000.0, 999)])
        assert heavy == pytest.approx(5000.0)
        light = volume_weighted_median([(100.0, 1), (101.0, 1), (5000.0, 1)])
        assert light == pytest.approx(101.0)

    def test_single_quote_returns_itself(self) -> None:
        assert volume_weighted_median([(42.0, 3)]) == pytest.approx(42.0)

    def test_zero_total_weight_falls_back_to_the_unweighted_median(self) -> None:
        assert volume_weighted_median([(10.0, 0), (20.0, 0), (30.0, 0)]) == pytest.approx(20.0)

    def test_empty_input_is_an_error_not_a_zero(self) -> None:
        with pytest.raises(ValueError):
            volume_weighted_median([])


class TestExclusions:
    method = Methodology()

    def test_three_agreeing_venues_produce_a_price(self) -> None:
        tick = compute_tick(AT, [q("a", 100.0), q("b", 100.1), q("c", 99.9)], self.method)
        assert tick.price == pytest.approx(100.0)
        assert tick.n_contributors == 3
        assert tick.undefined_reason is None

    def test_a_zero_volume_venue_is_excluded_for_having_no_trades(self) -> None:
        """A bar with no trades carries no price information. Including it as if it did is how a
        thin venue gets a vote it did not earn."""
        tick = compute_tick(
            AT, [q("a", 100.0), q("b", 100.1), q("c", 99.9), q("d", 90.0, volume=0.0)], self.method
        )
        excluded = {c.source_id: c.excluded_reason for c in tick.contributions if not c.included}
        assert "d" in excluded
        reason = excluded["d"]
        assert reason is not None and "no trades" in reason
        assert tick.n_contributors == 3

    def test_a_wild_print_is_excluded_as_an_outlier(self) -> None:
        tick = compute_tick(
            AT, [q("a", 100.0), q("b", 100.1), q("c", 99.9), q("d", 150.0)], self.method
        )
        excluded = {c.source_id for c in tick.contributions if not c.included}
        assert excluded == {"d"}
        assert tick.price == pytest.approx(100.0)

    def test_a_venue_that_is_merely_a_little_wide_is_not_excluded(self) -> None:
        """Outlier rejection with a floor. Without one, three venues agreeing to a tenth of a basis
        point make the fourth an outlier for being one basis point away."""
        tick = compute_tick(
            AT, [q("a", 100.000), q("b", 100.001), q("c", 99.999), q("d", 100.02)], self.method
        )
        assert tick.n_contributors == 4

    def test_a_non_positive_or_non_finite_price_is_excluded(self) -> None:
        tick = compute_tick(
            AT,
            [q("a", 100.0), q("b", 100.1), q("c", 99.9), q("d", 0.0), q("e", float("nan"))],
            self.method,
        )
        excluded = {c.source_id for c in tick.contributions if not c.included}
        assert excluded == {"d", "e"}

    def test_below_the_minimum_contributor_count_no_price_is_published(self) -> None:
        """The property the whole thing rests on. Two venues can agree perfectly and still be wrong
        together, and there is no way to tell from two. Publishing 'undefined' is the honest output
        and almost nobody does it."""
        tick = compute_tick(AT, [q("a", 100.0), q("b", 100.1)], self.method)
        assert tick.price is None
        assert tick.undefined_reason is not None
        assert "2" in tick.undefined_reason and "3" in tick.undefined_reason

    def test_undefined_still_reports_what_it_saw(self) -> None:
        tick = compute_tick(AT, [q("a", 100.0), q("b", 100.1)], self.method)
        assert len(tick.contributions) == 2
        assert all(c.included for c in tick.contributions)

    def test_no_quotes_at_all_is_undefined_not_a_crash(self) -> None:
        tick = compute_tick(AT, [], self.method)
        assert tick.price is None
        assert tick.n_contributors == 0

    def test_outlier_rejection_cannot_starve_the_price_silently(self) -> None:
        """If rejection drops the survivor count below the minimum, the answer is undefined --
        never 'the two that happen to agree'."""
        method = Methodology(min_contributors=3)
        tick = compute_tick(AT, [q("a", 100.0), q("b", 100.1), q("c", 500.0)], method)
        assert tick.price is None
        assert "outlier" in (tick.undefined_reason or "").lower()

    def test_one_outlier_among_exactly_three_venues_leaves_no_price(self) -> None:
        """An operational property worth knowing before deploying this, not after.

        With exactly `min_contributors` venues configured, *any* single outlier drops the survivor
        count below the minimum and the minute has no reference price. That is the methodology
        behaving correctly -- three venues where one disagrees is genuinely two venues, and two is
        not enough -- but it means a three-venue configuration is fragile by construction. Run four.
        """
        method = Methodology(min_contributors=3)
        three = compute_tick(AT, [q("a", 101.0), q("b", 100.0), q("c", 100.0)], method)
        assert three.price is None
        four = compute_tick(
            AT, [q("a", 101.0), q("b", 100.0), q("c", 100.0), q("d", 100.0)], method
        )
        assert four.price == pytest.approx(100.0)

    def test_relaxing_the_minimum_is_possible_and_visible(self) -> None:
        tick = compute_tick(AT, [q("a", 100.0), q("b", 100.1)], Methodology(min_contributors=2))
        assert tick.price == pytest.approx(100.05)


class TestSettlementRisk:
    def test_a_venue_that_tracks_the_reference_shows_little_risk(self) -> None:
        ticks = [
            compute_tick(AT, [q("a", 100.0), q("b", 100.01), q("c", 99.99)], Methodology())
            for _ in range(10)
        ]
        risk = settlement_risk(ticks)
        assert risk["a"].max_deviation_bp < 5

    def test_a_persistently_offset_venue_is_quantified(self) -> None:
        """The number a prop firm is actually buying: settle on this venue alone and this is the
        size of the disagreement you are exposed to.

        Four venues, not three, and that is not incidental -- see
        `test_one_outlier_among_exactly_three_venues_leaves_no_price`."""
        ticks = [
            compute_tick(
                AT, [q("a", 101.0), q("b", 100.0), q("c", 100.0), q("d", 100.0)], Methodology()
            )
            for _ in range(10)
        ]
        risk = settlement_risk(ticks)
        assert risk["a"].median_deviation_bp == pytest.approx(99.5, abs=1.0)
        assert risk["b"].median_deviation_bp == pytest.approx(0.0, abs=0.01)

    def test_minutes_with_no_reference_price_are_not_counted_as_agreement(self) -> None:
        """An undefined minute is missing evidence, not evidence of agreement. Counting it as a
        zero deviation would make a broken window look like a calm one."""
        good = compute_tick(
            AT, [q("a", 100.0), q("b", 100.0), q("c", 100.0), q("d", 100.0)], Methodology()
        )
        bad = compute_tick(AT, [q("a", 101.0)], Methodology())
        risk = settlement_risk([good, bad])
        assert risk["a"].n_minutes == 1

    def test_an_excluded_venue_is_still_measured_against_the_reference(self) -> None:
        """Being thrown out of the calculation is exactly when a venue's deviation matters most."""
        ticks = [
            compute_tick(
                AT, [q("a", 100.0), q("b", 100.1), q("c", 99.9), q("d", 150.0)], Methodology()
            )
        ]
        risk = settlement_risk(ticks)
        assert "d" in risk
        assert risk["d"].max_deviation_bp > 3000
        assert risk["d"].n_excluded == 1
