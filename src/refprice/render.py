"""The page.

Same rules as `mdq`: one file, no CDN, no build step, theme-aware, and a header that goes amber
before it goes green on data it did not fetch.

What is different is what it leads with. `mdq` leads with "is anything broken". This one leads with
a number and immediately tells you how much that number would have differed from any single venue
you might otherwise have settled on -- because that difference is the product.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import fields as dc_fields

from mdq import USER_AGENT
from mdq.models import SourceHealth, SourceStatus

from refprice.config import Config
from refprice.methodology import Methodology, ReferenceTick, VenueRisk
from refprice.store import StoreStats

METHODOLOGY_NOTES: dict[str, str] = {
    "min_contributors": (
        "Below this many surviving venues, the minute has no reference price and the page says so. "
        "Two venues can agree perfectly and be wrong together, and from two there is no way to "
        "tell. Publishing 'undefined' is the honest output, and almost nobody does it -- which is "
        "why settlement disputes are so hard to resolve after the fact."
    ),
    "outlier_mad_multiple": (
        "Distance from the cross-venue median, in units of the cross-venue median absolute "
        "deviation, beyond which a quote is discarded. Tighter than a monitoring threshold would "
        "be: here the cost of admitting a bad print is a wrong settlement price, not a noisy alert."
    ),
    "outlier_min_bp": (
        "Floor on the rejection band. When venues agree closely the MAD collapses towards zero and "
        "every remaining venue starts to look like an outlier; below 10 bp the disagreement is not "
        "economically real."
    ),
    "require_volume": (
        "Quotes from bars with no trades are excluded. A bar with no trades carries no price "
        "information, and counting it gives a thin venue a vote it did not earn."
    ),
}


def _e(v: object) -> str:
    return html.escape(str(v), quote=True)


def _spark(values: Sequence[float | None], width: int = 640, height: int = 78) -> str:
    """Inline SVG. Gaps are gaps: an undefined minute leaves a hole in the line rather than being
    bridged, because bridging it would draw a price that was never published."""
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return '<svg class="spark" viewBox="0 0 640 78" role="img" aria-label="not enough history yet"></svg>'
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    step = width / max(len(values) - 1, 1)
    segments: list[str] = []
    current: list[str] = []
    for i, v in enumerate(values):
        if v is None:
            if len(current) > 1:
                segments.append(" ".join(current))
            current = []
            continue
        current.append(f"{i * step:.1f},{height - 4 - (v - lo) / span * (height - 12):.1f}")
    if len(current) > 1:
        segments.append(" ".join(current))
    paths = "".join(
        f'<polyline points="{s}" fill="none" stroke="currentColor" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        for s in segments
    )
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
        f'aria-label="reference price, {len(pts)} defined minutes, {lo:.0f} to {hi:.0f}">{paths}</svg>'
    )


CSS = """
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1b1b1a;--muted:#6b6b66;--line:#e2e2dd;--card:#fff;
--ok:#1a7f4b;--warn:#a86a00;--err:#b3261e;--ok-bg:#e8f5ee;--warn-bg:#fdf3e0;--err-bg:#fdeceb;--accent:#1b1b1a;}
@media (prefers-color-scheme:dark){:root{--bg:#14140f;--fg:#e9e9e3;--muted:#9a9a92;--line:#2c2c26;
--card:#1c1c17;--ok:#5fd39a;--warn:#e5b25c;--err:#f2887f;--ok-bg:#11291d;--warn-bg:#2c2312;--err-bg:#2e1614;--accent:#e9e9e3;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Inter,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:19px;margin:0 0 2px;letter-spacing:-.01em}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:34px 0 10px;font-weight:600}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.hero{border:1px solid var(--line);border-radius:12px;padding:18px 20px;background:var(--card);margin-bottom:10px}
.price{font-size:44px;font-weight:650;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.05}
.price small{font-size:15px;font-weight:500;color:var(--muted);margin-left:8px;letter-spacing:0}
.price.undef{font-size:26px;color:var(--warn)}
.hero .spark{display:block;width:100%;height:78px;margin-top:12px;color:var(--accent);opacity:.8}
.banner{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:12px 16px;border-radius:10px;border:1px solid var(--line);font-weight:600;margin-bottom:22px}
.banner.ok{background:var(--ok-bg);border-color:var(--ok);color:var(--ok)}
.banner.warn{background:var(--warn-bg);border-color:var(--warn);color:var(--warn)}
.banner.err{background:var(--err-bg);border-color:var(--err);color:var(--err)}
.banner .dot{width:10px;height:10px;border-radius:50%;background:currentColor;flex:none}
table{width:100%;border-collapse:collapse;font-size:13px}
.scroll{overflow-x:auto}
th{text-align:left;font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:0 10px 7px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:top;font-variant-numeric:tabular-nums}
td.name{font-weight:600;font-variant-numeric:normal}
.pill{font-size:10px;padding:1px 7px;border-radius:99px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.err{background:var(--err-bg);color:var(--err)}.pill.blocked{background:var(--warn-bg);color:var(--warn)}
.bar{display:inline-block;height:8px;background:currentColor;border-radius:99px;opacity:.35;vertical-align:middle;margin-left:8px}
details{border:1px solid var(--line);border-radius:9px;padding:10px 13px;background:var(--card);margin-bottom:8px}
summary{cursor:pointer;font-weight:600;font-size:13px}
details p{color:var(--muted);font-size:13px;margin:9px 0 0}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
footer ul{margin:8px 0 0;padding-left:18px}
"""


def _headline(health: Sequence[SourceHealth], latest: ReferenceTick | None) -> tuple[str, str]:
    n = len(health)
    bad = [h for h in health if h.status is not SourceStatus.OK]
    if bad:
        return "err" if any(h.status is SourceStatus.ERROR for h in bad) else "warn", (
            f"{len(bad)} of {n} venues not answering — "
            + ", ".join(sorted(h.source_id for h in bad))
        )
    if latest is None or not latest.is_defined:
        return "warn", f"{n} venues responding · no reference price for the latest minute"
    return "ok", f"{n} venues responding · {latest.n_contributors} contributed to the latest print"


def render(
    config: Config,
    ticks: list[ReferenceTick],
    health: Sequence[SourceHealth],
    risk: dict[str, VenueRisk],
    *,
    history: Sequence[float | None] = (),
    stats: StoreStats | None = None,
    undefined: Sequence[tuple[str, int, str]] = (),
    n_provisional: int = 0,
) -> str:
    stats = stats or StoreStats(0, 0, 0, None, None)
    latest = next((t for t in reversed(ticks) if t.contributions), ticks[-1] if ticks else None)
    latest_defined = next((t for t in reversed(ticks) if t.is_defined), None)
    banner_class, banner_text = _headline(health, latest)

    if latest and latest.is_defined and latest.price is not None:
        price_html = (
            f'<div class="price">{latest.price:,.2f}<small>USDT · '
            f"{latest.at:%H:%M} UTC · {latest.n_contributors} venues</small></div>"
        )
    elif latest is not None and latest_defined is not None and latest_defined.price is not None:
        # The stale-value rule: the last good price is shown, labelled as last-good, and never
        # presented as current. Showing it without the label is how people act on old data.
        price_html = (
            f'<div class="price undef">no reference price for {latest.at:%H:%M} UTC — '
            f"{_e(latest.undefined_reason or 'insufficient contributors')}</div>"
            f'<div class="sub" style="margin:8px 0 0">Last defined: '
            f"<strong>{latest_defined.price:,.2f}</strong> at {latest_defined.at:%H:%M} UTC. "
            f"Shown as the last defined value, not as the current one.</div>"
        )
    else:
        price_html = '<div class="price undef">no reference price yet</div>'

    venue_rows = []
    health_by_id = {h.source_id: h for h in health}
    worst = max((r.median_deviation_bp for r in risk.values()), default=1.0) or 1.0
    for source_id in sorted(set(risk) | set(health_by_id)):
        r = risk.get(source_id)
        h = health_by_id.get(source_id)
        status = h.status.value if h else "—"
        cls = {"ok": "ok", "stale": "warn", "error": "err", "blocked": "blocked"}.get(
            status, "warn"
        )
        if r:
            width = max(2, round(r.median_deviation_bp / worst * 90))
            bar = f'<span class="bar" style="width:{width}px"></span>'
            excl = f"{r.n_excluded}" if r.n_excluded else "—"
            venue_rows.append(
                f'<tr><td class="name">{_e(source_id)} <span class="pill {cls}">{_e(status)}</span></td>'
                f"<td>{r.median_deviation_bp:.2f}{bar}</td><td>{r.p95_deviation_bp:.2f}</td>"
                f"<td>{r.max_deviation_bp:.2f}</td>"
                f'<td class="mono">{_e(r.worst_at.strftime("%H:%M") if r.worst_at else "—")}</td>'
                f"<td>{r.n_minutes}</td><td>{excl}</td></tr>"
            )
        else:
            venue_rows.append(
                f'<tr><td class="name">{_e(source_id)} <span class="pill {cls}">{_e(status)}</span></td>'
                f'<td colspan="6" style="color:var(--muted)">'
                f"{_e(h.error if h and h.error else 'no data this run')}</td></tr>"
            )

    method_rows = "".join(
        f'<tr><td class="name mono">{_e(f.name)}</td><td class="mono">'
        f'{_e(getattr(config.methodology, f.name))}</td><td style="font-variant-numeric:normal">'
        f"{_e(METHODOLOGY_NOTES[f.name])}</td></tr>"
        for f in sorted(dc_fields(Methodology), key=lambda f: f.name)
    )

    undef_rows = (
        "".join(
            f'<tr><td class="mono">{_e(at[:16].replace("T", " "))}</td><td>{_e(n)}</td>'
            f'<td style="font-variant-numeric:normal">{_e(reason)}</td></tr>'
            for at, n, reason in undefined
        )
        or '<tr><td colspan="3" style="color:var(--muted)">None — every minute in the store has a reference price.</td></tr>'
    )

    n_ticks = stats.n_ticks
    coverage = stats.coverage_pct
    first = (stats.first_tick or "")[:16].replace("T", " ")

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{_e(config.index_name)}</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>{_e(config.index_name)}</h1>
<p class="sub">A volume-weighted median across {len(config.venues)} independent venues, with a
published methodology and an explicit refusal to print a number it cannot stand behind.
{_e(n_ticks)} minutes stored since {_e(first or "first run")} · {coverage:.1f}% of them have a
reference price · published {_e(int(config.publication_lag.total_seconds() // 60))} minutes behind
the clock, with {_e(n_provisional)} newer minute(s) held as provisional.</p>

<div class="hero">{price_html}{_spark(history)}</div>

<div class="banner {banner_class}"><span class="dot"></span><span>{_e(banner_text)}</span></div>

<h2>Single-venue settlement risk</h2>
<p class="sub">If you settled on one of these venues alone, this is how far that decision would
have sat from the reference price. Deviations are in basis points. This is the number the whole
page exists to produce.</p>
<div class="scroll"><table>
<thead><tr><th>venue</th><th>median dev (bp)</th><th>p95</th><th>worst</th><th>worst at</th>
<th>minutes</th><th>excluded</th></tr></thead>
<tbody>{"".join(venue_rows)}</tbody></table></div>

<h2>Minutes with no reference price</h2>
<p class="sub">Published as gaps rather than filled in. A reference price that is always available
is a reference price that is sometimes invented.</p>
<div class="scroll"><table><thead><tr><th>minute</th><th>contributors</th><th>why</th></tr></thead>
<tbody>{undef_rows}</tbody></table></div>

<h2>Methodology</h2>
<div class="scroll"><table><thead><tr><th>rule</th><th>value</th><th>why</th></tr></thead>
<tbody>{method_rows}</tbody></table></div>

<details><summary>Why a volume-weighted <em>median</em> and not a volume-weighted mean</summary>
<p>A venue holding most of the volume can drag a mean arbitrarily far with a single bad print. It
can move a weighted median no further than its own quote, and only if it holds more than half the
weight. The failure mode a reference price has to survive is one venue being wrong and busy at the
same time.</p></details>

<details><summary>Why spot only, and why one quote currency</summary>
<p>A perpetual swap trades at a basis to spot that is the price of funding, not a disagreement
about the value of bitcoin — blending them would make the reference price move whenever funding
did. The same argument one step further out separates BTC/USD from BTC/USDT: they differ by the
USDT basis, and a reference price has to be denominated in one thing.</p></details>

<details><summary>Why the published price lags the clock by a few minutes</summary>
<p>Venues do not finish reporting a minute at the same instant — Coinbase Exchange's public candles
endpoint runs three to five minutes behind the others, measured, not assumed. Publishing a minute
the moment it closes would drop the slowest venues from every single print, quietly turning a
four-venue index into a two-venue one at exactly the moments anyone is looking at it. Minutes newer
than the lag are computed and stored as provisional, recomputed as late data arrives, and never
headlined.</p></details>

<details><summary>Known weaknesses of <em>this</em> venue set, stated rather than buried</summary>
<p>Two of the four venues are thin in BTC/USDT specifically: Kraken and Coinbase both run orders of
magnitude less volume in this pair than OKX and Bybit do. The volume-weighted median is therefore
decided in practice by two venues, with the other two acting as a sanity check rather than as
weight. That is survivable — it is part of why the aggregation is a median and not a mean, and why
three contributors are required — but it is a real limitation and a production version would add
depth. The obvious candidate for that depth is Binance, which is excluded here because its
<code>robots.txt</code> disallows automated agents.</p></details>

<details><summary>Why two venues are excluded that would have been easy to include</summary>
<p>OKX's <code>BTC-USDT-SWAP</code> and Deribit's <code>BTC-PERPETUAL</code> report volume in
<strong>contracts</strong>, not in base currency. Feeding a contract count into a volume-weighted
statistic alongside four venues reporting BTC would weight them by a different unit entirely, and
nothing in the output would look wrong. They stay out until that conversion is implemented and
tested, rather than going in with a weight that is quietly meaningless.</p></details>

<footer>
<p><strong>Data policy.</strong> Public, unauthenticated endpoints only. One request per second per
host, enforced in code. User-Agent <code>{_e(USER_AGENT)}</code>. <code>robots.txt</code> checked
before any request; a refusal is recorded as <em>blocked</em> and never worked around. Binance,
Stooq and Bitstamp are excluded because their <code>robots.txt</code> disallows it.</p>
<p><strong>This is a demonstration, not a licensed benchmark.</strong> It is not IOSCO-aligned, it
has no governance committee, no audit trail beyond this page, and no continuity guarantee. Do not
settle anything real on it.</p>
<p>Built on the same collection layer as <code>mdq</code>, the market-data integrity monitor —
same source adapters, same rate limiter, same refusal policy. Bound to localhost by default.</p>
</footer>
</div></body></html>
"""
