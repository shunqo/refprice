"""The reference-price methodology.

A reference price is not an average of some prices. It is a **published set of rules** about which
prices count, what happens when they disagree, and — the part almost everybody skips — what the
answer is when there is not enough evidence to have one.

Every rule below is here because of a specific way the naive version fails:

* **Volume-weighted median, not a volume-weighted mean.** A venue holding most of the volume can
  drag a mean arbitrarily far with one bad print. It can move a weighted median no further than its
  own quote, and only if it holds more than half the weight.
* **Zero-volume quotes are excluded.** A bar with no trades carries no price information. Counting
  it gives a thin venue a vote it did not earn.
* **Outlier rejection has a floor.** Without one, three venues agreeing to a tenth of a basis point
  make the fourth an outlier for being one basis point away, and the reference price starts
  tracking whichever venues happen to be quietest.
* **Below a minimum number of contributors, no price is published.** Two venues can agree perfectly
  and be wrong together, and from two there is no way to tell. `None` is the honest output. Almost
  nobody publishes it, which is why disputes about settlement prices are so hard to resolve.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime

BP = 10_000.0


@dataclass(frozen=True, slots=True)
class Methodology:
    """The published rules. Change one and the reference price is a different product."""

    min_contributors: int = 3
    """Fewer surviving venues than this and the minute has no reference price.

    Three, because two venues that agree cannot be distinguished from two venues that are wrong in
    the same direction, and the whole point of a reference price is to be arguable in a dispute.
    """

    outlier_mad_multiple: float = 5.0
    """Distance from the cross-venue median, in units of the cross-venue MAD, beyond which a quote
    is discarded. Deliberately tighter than a monitoring threshold: here the cost of including a bad
    print is a wrong settlement price, not a noisy alert."""

    outlier_min_bp: float = 10.0
    """Floor on the rejection band. When venues agree very closely the MAD collapses and every
    remaining venue looks like an outlier; below 10 bp the disagreement is not economically real."""

    require_volume: bool = True
    """Exclude quotes from bars with no trades."""


@dataclass(frozen=True, slots=True)
class Quote:
    source_id: str
    mid: float
    volume: float


@dataclass(frozen=True, slots=True)
class Contribution:
    source_id: str
    mid: float
    volume: float
    included: bool
    excluded_reason: str | None = None
    deviation_bp: float | None = None


@dataclass(frozen=True, slots=True)
class ReferenceTick:
    at: datetime
    price: float | None
    n_contributors: int
    contributions: tuple[Contribution, ...] = ()
    undefined_reason: str | None = None

    @property
    def is_defined(self) -> bool:
        return self.price is not None


def volume_weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Median of `price` weighted by `volume`.

    Walks the cumulative weight and returns the price at which it crosses half the total. When the
    crossing lands exactly on a boundary the two neighbouring prices are averaged, which is what
    makes the two-venue case behave like an ordinary median rather than jumping to one side.

    Zero total weight falls back to the unweighted median rather than dividing by zero: it happens
    when `require_volume` is off and every venue reported a quiet minute, and the prices are still
    the best evidence available.
    """
    if not pairs:
        raise ValueError("volume_weighted_median() needs at least one quote")
    ordered = sorted(pairs, key=lambda p: p[0])
    total = sum(w for _, w in ordered)
    if total <= 0:
        return statistics.median([p for p, _ in ordered])

    half = total / 2.0
    cumulative = 0.0
    for i, (price, weight) in enumerate(ordered):
        cumulative += weight
        if cumulative > half:
            return price
        if cumulative == half:
            nxt = ordered[i + 1][0] if i + 1 < len(ordered) else price
            return (price + nxt) / 2.0
    return ordered[-1][0]


def _robust_band_bp(mids: list[float], method: Methodology) -> tuple[float, float]:
    """(centre, half-width in bp) of the acceptance band."""
    centre = statistics.median(mids)
    deviations_bp = [abs(math.log(m / centre)) * BP for m in mids if m > 0]
    mad_bp = statistics.median(deviations_bp) if deviations_bp else 0.0
    return centre, max(mad_bp * method.outlier_mad_multiple, method.outlier_min_bp)


def compute_tick(at: datetime, quotes: list[Quote], method: Methodology) -> ReferenceTick:
    """One minute of reference price, with every exclusion recorded and reasoned."""
    contributions: list[Contribution] = []
    survivors: list[Quote] = []

    for quote in quotes:
        reason: str | None = None
        if not math.isfinite(quote.mid) or quote.mid <= 0:
            reason = "price is not a finite positive number"
        elif method.require_volume and quote.volume <= 0:
            reason = "no trades in this bar, so it carries no price information"
        if reason:
            contributions.append(
                Contribution(quote.source_id, quote.mid, quote.volume, False, reason)
            )
        else:
            survivors.append(quote)

    if not survivors:
        return ReferenceTick(
            at=at,
            price=None,
            n_contributors=0,
            contributions=tuple(contributions),
            undefined_reason=f"no usable quotes from {len(quotes)} venue(s)",
        )

    centre, band_bp = _robust_band_bp([q.mid for q in survivors], method)
    kept: list[Quote] = []
    n_outliers = 0
    for quote in survivors:
        deviation_bp = abs(math.log(quote.mid / centre)) * BP
        if deviation_bp > band_bp:
            n_outliers += 1
            contributions.append(
                Contribution(
                    quote.source_id,
                    quote.mid,
                    quote.volume,
                    False,
                    f"outlier: {deviation_bp:.1f} bp from the cross-venue median, band is "
                    f"{band_bp:.1f} bp",
                    deviation_bp,
                )
            )
        else:
            kept.append(quote)

    if len(kept) < method.min_contributors:
        detail = f" after discarding {n_outliers} outlier(s)" if n_outliers else ""
        for quote in kept:
            contributions.append(Contribution(quote.source_id, quote.mid, quote.volume, True))
        return ReferenceTick(
            at=at,
            price=None,
            n_contributors=len(kept),
            contributions=tuple(contributions),
            undefined_reason=(
                f"only {len(kept)} contributing venue(s){detail}; the methodology requires "
                f"{method.min_contributors}"
            ),
        )

    price = volume_weighted_median([(q.mid, q.volume) for q in kept])
    for quote in kept:
        contributions.append(
            Contribution(
                quote.source_id,
                quote.mid,
                quote.volume,
                True,
                None,
                abs(math.log(quote.mid / price)) * BP,
            )
        )
    return ReferenceTick(
        at=at, price=price, n_contributors=len(kept), contributions=tuple(contributions)
    )


@dataclass(frozen=True, slots=True)
class VenueRisk:
    """What settling on one venue alone would have exposed you to."""

    source_id: str
    n_minutes: int
    n_excluded: int
    median_deviation_bp: float
    p95_deviation_bp: float
    max_deviation_bp: float
    worst_at: datetime | None
    deviations_bp: list[float] = field(default_factory=list)


def settlement_risk(ticks: list[ReferenceTick]) -> dict[str, VenueRisk]:
    """Per-venue deviation from the reference price, over the window.

    Minutes with no reference price are skipped entirely. An undefined minute is *missing evidence*,
    not evidence of agreement, and counting it as a zero deviation would make the most broken window
    in the sample look like the calmest one.

    Excluded venues are still measured. Being thrown out of the calculation is exactly the moment a
    venue's deviation matters most.
    """
    per_source: dict[str, list[tuple[float, datetime]]] = {}
    excluded_counts: dict[str, int] = {}

    for tick in ticks:
        for c in tick.contributions:
            if not c.included:
                excluded_counts[c.source_id] = excluded_counts.get(c.source_id, 0) + 1
        if tick.price is None:
            continue
        for c in tick.contributions:
            if c.mid > 0 and math.isfinite(c.mid):
                deviation_bp = abs(math.log(c.mid / tick.price)) * BP
                per_source.setdefault(c.source_id, []).append((deviation_bp, tick.at))

    out: dict[str, VenueRisk] = {}
    for source_id in set(per_source) | set(excluded_counts):
        samples = sorted(per_source.get(source_id, []))
        values = [d for d, _ in samples]
        worst = samples[-1] if samples else None
        out[source_id] = VenueRisk(
            source_id=source_id,
            n_minutes=len(values),
            n_excluded=excluded_counts.get(source_id, 0),
            median_deviation_bp=statistics.median(values) if values else 0.0,
            p95_deviation_bp=values[int(len(values) * 0.95)] if values else 0.0,
            max_deviation_bp=worst[0] if worst else 0.0,
            worst_at=worst[1] if worst else None,
            deviations_bp=values,
        )
    return out
