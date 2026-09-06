"""Which venues contribute, and on what terms.

The venue list is short on purpose and every exclusion below is a methodology decision with a
stated reason, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from refprice.methodology import Methodology


@dataclass(frozen=True, slots=True)
class VenueSpec:
    source_id: str
    symbol: str
    base: str
    quote: str
    kind: str = "spot"
    label: str = ""

    @property
    def key(self) -> str:
        return self.label or f"{self.source_id}:{self.symbol}"


# BTC/USDT spot on four independent venues.
#
# Why *spot only*: a perpetual swap trades at a basis to spot that is the price of funding, not a
# disagreement about the value of bitcoin. Blending the two would put a real economic spread into a
# statistic that is supposed to measure venue disagreement, and would make the reference price move
# whenever funding did.
#
# Why *USDT only*: the same argument one currency further out. BTC/USD and BTC/USDT differ by the
# USDT basis. A reference price must be denominated in one thing.
#
# Why not OKX's BTC-USDT-SWAP or Deribit's BTC-PERPETUAL, beyond the spot rule: their volume fields
# are denominated in **contracts**, not in base currency. Feeding a contract count into a
# volume-weighted statistic alongside four venues reporting BTC would weight them by a different
# unit entirely, and nothing in the output would look wrong. They are excluded until that conversion
# is implemented and tested, rather than included with a weight that is quietly meaningless.
VENUES: tuple[VenueSpec, ...] = (
    VenueSpec("okx", "BTC-USDT", "BTC", "USDT", label="okx"),
    VenueSpec("bybit", "BTCUSDT", "BTC", "USDT", label="bybit"),
    VenueSpec("kraken", "XBTUSDT", "BTC", "USDT", label="kraken"),
    VenueSpec("coinbase", "BTC-USDT", "BTC", "USDT", label="coinbase"),
)


@dataclass(frozen=True, slots=True)
class Config:
    venues: tuple[VenueSpec, ...] = VENUES

    interval: timedelta = timedelta(minutes=5)
    """The observation interval. **Measured, not picked.**

    The interval has to be long enough that every contributing venue actually trades inside it. If
    it is not, thin venues contribute nothing, the survivor count falls below the minimum, and the
    index spends its time refusing to publish -- correctly, but uselessly.

    Measured on 2026-09-05 against the four configured venues, over the shared window:

    | interval | minutes with a price | mean contributors |
    |---|---|---|
    | 1 minute  | 69.7% | 2.83 |
    | 5 minutes | **98.3%** | **3.49** |
    | 15 minutes | 100.0% | 3.95 |

    One minute puts the mean contributor count *below the required three*, which is the whole
    problem in one number. Fifteen is fully covered and too coarse to be interesting. Five is the
    choice, and the 1.7% of intervals it still cannot price are real -- they are intervals where
    two of the four venues genuinely did not trade.
    """

    lookback_bars: int = 240
    methodology: Methodology = field(default_factory=Methodology)
    publication_lag: timedelta = timedelta(minutes=5)
    """How far behind the clock the *published* reference price sits.

    Real reference rates are published with a defined lag, and this is why: venues do not all
    finish reporting a minute at the same instant. Coinbase Exchange's public candles endpoint runs
    three to five minutes behind the others, measured. Publishing the newest minute the moment it
    closes would systematically drop the slowest venues from every print, which does not just add
    noise -- it quietly turns a four-venue index into a two-venue one at exactly the moments
    anybody is looking.

    Five minutes covers the measured Coinbase lag with margin. Minutes newer than this are computed and stored as *provisional* and are recomputed
    on the next run as late data arrives; only minutes older than the lag are treated as final.
    """

    db_path: Path = Path("var/refprice.sqlite3")
    html_path: Path = Path("var/index.html")
    bind_host: str = "127.0.0.1"
    bind_port: int = 8701
    index_name: str = "BTC/USDT 5-Minute Reference Rate"

    def __post_init__(self) -> None:
        if len(self.venues) < self.methodology.min_contributors + 1:
            raise ValueError(
                f"{len(self.venues)} venues configured with min_contributors="
                f"{self.methodology.min_contributors}. Run at least one spare: with exactly the "
                "minimum, any single outlier drops the count below it and every such minute has no "
                "reference price."
            )
