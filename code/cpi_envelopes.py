"""Clock envelopes for a minute-only event file, using Binance individual trades.

Applies the measurement rules of "When seconds are lost" to events whose announcement
time is known only to the UTC minute (for example, the Crypto Pump Island rows of the
Clough-Edwards release). For each event it evaluates the purchase markout and execution
peak at all 60 compatible seconds and reports event envelopes and sample summaries.

Usage
-----
1. Check the implementation against the audited 316-event grid (recommended first):
     python cpi_envelopes.py --validate-grid data/candidate_clock_outcomes.csv.gz \
         --cache binance_cache --output validation
2. Run the minute-only events:
     python cpi_envelopes.py --events cpi_binance_events.csv --cache binance_cache \
         --output cpi_results

Events file (CSV), one row per event:
  event_id    unique identifier
  symbol      Binance spot symbol quoted in BTC, e.g. XYZBTC
  minute_utc  announcement minute, ISO 8601 UTC (2021-03-14 17:00) or epoch seconds
  channel     optional; used only to label output
Rows whose symbol does not end in BTC are skipped and counted.

Trades come from https://data.binance.vision (spot, daily "trades" files). Every zip is
verified against its published SHA-256 .CHECKSUM before use and cached locally.

Rules (identical to the letter and to verify_against_trades.py in the replication package):
  candidate clocks t = m + s, s = 0..59; windows are half-open; ties ordered by trade ID
  purchases: buyer is not the maker, executed in [t, t+60)
  pre-price: last execution strictly before t; terminal: last execution strictly before t+600
  peak price: highest execution in [t, t+600)
  markout = terminal * sum(q) / sum(p q) - 1 (math.fsum); peak = peak price / pre-price - 1
  markout supported: purchases exist and terminal age <= cap
  peak supported: an execution exists in [t, t+600) and pre-price age <= cap
  tape retained for an event: executions in [m - 660 s, m + 659 s)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import time
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

NS = 10**9
DAY_NS = 86_400 * NS
BASE_URL = "https://data.binance.vision/data/spot/daily/trades"
CAPS = (30, 60, 300)
PRIMARY_CAP = 60


# --------------------------------------------------------------------------- trades
def _download(url: str, dest: Path, attempts: int = 4) -> bool:
    for k in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                dest.write_bytes(r.read())
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
        except Exception:
            pass
        time.sleep(2 * (k + 1))
    raise RuntimeError(f"could not download {url}")


def fetch_day(symbol: str, date: str, cache: Path, offline: bool) -> Path | None:
    """Return a verified local zip for symbol/date, or None if Binance has no file."""
    folder = cache / symbol
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}-trades-{date}.zip"
    zpath, cpath = folder / name, folder / (name + ".CHECKSUM")
    missing = folder / (name + ".MISSING")
    if missing.exists():
        return None
    if not (zpath.exists() and cpath.exists()):
        if offline:
            return None
        url = f"{BASE_URL}/{symbol}/{name}"
        if not _download(url + ".CHECKSUM", cpath):
            missing.write_text("404\n")
            return None
        if not _download(url, zpath):
            missing.write_text("404\n")
            return None
    expected = cpath.read_text().split()[0].strip().lower()
    actual = hashlib.sha256(zpath.read_bytes()).hexdigest()
    if actual != expected:
        zpath.unlink()
        raise RuntimeError(f"checksum mismatch for {zpath}")
    return zpath


@lru_cache(maxsize=64)
def load_day(zpath: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        raw = z.read(z.namelist()[0])
    first = raw[:200].decode("utf-8", "replace").split("\n", 1)[0]
    header = 0 if any(c.isalpha() for c in first.split(",")[0]) else None
    d = pd.read_csv(io.BytesIO(raw), header=header, dtype=str)
    d = d.iloc[:, :6]
    d.columns = ["trade_id", "price", "quantity", "quote_qty", "time", "buyer_maker"]
    out = pd.DataFrame({
        "trade_id": d.trade_id.astype(np.int64),
        "price": d.price.astype(float),
        "quantity": d.quantity.astype(float),
        "buyer_maker": d.buyer_maker.str.strip().str.lower().map({"true": True, "false": False}),
    })
    t = d.time.astype(np.int64)
    # Spot timestamps are milliseconds before 2025 and microseconds from 1 January 2025.
    out["time_ns"] = np.where(t > 10**15, t * 1_000, t * 1_000_000)
    if out.buyer_maker.isna().any():
        raise ValueError(f"unparsed buyer_maker values in {zpath}")
    return out.sort_values(["time_ns", "trade_id"], kind="mergesort").reset_index(drop=True)


def event_tape(symbol: str, minute_ns: int, cache: Path, offline: bool):
    lower, upper = minute_ns - 660 * NS, minute_ns + 659 * NS
    frames, files, gaps = [], [], []
    for day in range(lower // DAY_NS * DAY_NS, (upper - 1) // DAY_NS * DAY_NS + 1, DAY_NS):
        date = pd.Timestamp(day, unit="ns", tz="UTC").strftime("%Y-%m-%d")
        z = fetch_day(symbol, date, cache, offline)
        if z is None:
            gaps.append(date)
            continue
        d = load_day(z)
        frames.append(d[(d.time_ns >= lower) & (d.time_ns < upper)])
        files.append(z.name)
    if gaps:
        return None, files, gaps
    tape = pd.concat(frames, ignore_index=True).sort_values(["time_ns", "trade_id"], kind="mergesort")
    if tape.trade_id.duplicated().any():
        raise ValueError(f"duplicate trade IDs for {symbol} at {minute_ns}")
    return tape, files, gaps


# ------------------------------------------------------------------------ outcomes
def clock_rows(tape: pd.DataFrame, minute_ns: int) -> list[dict]:
    times = tape.time_ns.to_numpy()
    prices = tape.price.to_numpy()
    quantities = tape.quantity.to_numpy()
    buys = ~tape.buyer_maker.to_numpy(dtype=bool)
    rows = []
    for s in range(60):
        t = minute_ns + s * NS
        purchase = (times >= t) & (times < t + 60 * NS) & buys
        pre = np.flatnonzero(times < t)
        terminal = np.flatnonzero(times < t + 600 * NS)
        post = (times >= t) & (times < t + 600 * NS)
        before = prices[pre[-1]] if len(pre) else np.nan
        endpoint = prices[terminal[-1]] if len(terminal) else np.nan
        high = max(prices[post]) if post.any() else np.nan
        quantity = math.fsum(quantities[purchase])
        cost = math.fsum(prices[purchase] * quantities[purchase])
        mark = endpoint * quantity / cost - 1 if cost and len(terminal) else np.nan
        peak = high / before - 1 if len(pre) and post.any() else np.nan
        rows.append(dict(
            candidate_second=s, clock_time_ns=t, n_buy=int(purchase.sum()),
            pre_price=before, terminal_price=endpoint, peak_price=high, markout=mark, peak=peak,
            pre_age_seconds=(t - times[pre[-1]]) / NS if len(pre) else np.nan,
            terminal_age_seconds=(t + 600 * NS - times[terminal[-1]]) / NS if len(terminal) else np.nan,
            has_purchases=bool(purchase.any()), has_post=bool(post.any())))
    return rows


def supported(r: pd.DataFrame, outcome: str, cap: int) -> pd.Series:
    if outcome == "markout":
        return r.has_purchases & r.terminal_age_seconds.le(cap)
    return r.has_post & r.pre_age_seconds.le(cap)


def envelopes(grid: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (event, channel), g in grid.groupby(["event_id", "channel"], sort=True):
        for outcome in ("markout", "peak"):
            for cap in CAPS:
                ok = supported(g, outcome, cap)
                n = int(ok.sum())
                full = n == 60
                floor = g.loc[g.candidate_second.eq(0) & ok, outcome]
                out.append(dict(
                    event_id=event, channel=channel, outcome=outcome, cap_seconds=cap,
                    supported_clocks=n, support="full" if full else ("zero" if n == 0 else "partial"),
                    lower=g[outcome].min() if full else np.nan,
                    upper=g[outcome].max() if full else np.nan,
                    floor_value=floor.iloc[0] if len(floor) else np.nan))
    e = pd.DataFrame(out)
    e["width"] = e.upper - e.lower
    return e


def summarize(env: pd.DataFrame, grid: pd.DataFrame, skipped: dict) -> dict:
    res = dict(events_evaluated=int(env.event_id.nunique()), skipped=skipped)
    for outcome in ("markout", "peak"):
        for cap in CAPS:
            e = env[(env.outcome == outcome) & (env.cap_seconds == cap)]
            f = e[e.support == "full"]
            block = dict(
                full=int((e.support == "full").sum()), partial=int((e.support == "partial").sum()),
                zero=int((e.support == "zero").sum()),
                mean_floor_pct=100 * f.floor_value.mean(), mean_lower_pct=100 * f.lower.mean(),
                mean_upper_pct=100 * f.upper.mean(),
                median_width_pp=100 * f.width.median(), p90_width_pp=100 * f.width.quantile(0.9))
            if outcome == "markout":
                block.update(negative_all_clocks=int((f.upper < 0).sum()),
                             nonnegative_all_clocks=int((f.lower >= 0).sum()),
                             sign_switches=int(((f.lower < 0) & (f.upper >= 0)).sum()))
            else:
                block.update(positive_all_clocks=int((f.lower > 0).sum()))
            res[f"{outcome}_cap{cap}"] = block
    # common sample at the primary cap
    p = env[env.cap_seconds == PRIMARY_CAP].pivot_table(index="event_id", columns="outcome",
                                                        values="support", aggfunc="first")
    common = p.index[(p.markout == "full") & (p.peak == "full")]
    g = grid[grid.event_id.isin(common)]
    ok = supported(g, "markout", PRIMARY_CAP) & supported(g, "peak", PRIMARY_CAP)
    combo = g.assign(c=ok & (g.peak > 0) & (g.markout < 0)).groupby("event_id").c
    res["common_cap60"] = dict(
        events=int(len(common)),
        positive_peak_negative_markout_every_clock=int(combo.all().sum()),
        positive_peak_negative_markout_some_clock=int(combo.any().sum()))
    return res


# ---------------------------------------------------------------------- validation
def validate(grid_path: Path, cache: Path, offline: bool, out: Path) -> dict:
    stored = pd.read_csv(grid_path, dtype={"clock_time_ns": "Int64"}, float_precision="round_trip")
    worst = {k: 0.0 for k in ("markout", "peak", "pre_price", "terminal_price", "peak_price")}
    mismatches, rows, missing = [], 0, []
    for event, g in stored.groupby("event_id", sort=True):
        minute = int(g.clock_time_ns.min())
        tape, files, gaps = event_tape(g.native_symbol.iloc[0], minute, cache, offline)
        if tape is None:
            missing.append(event)
            continue
        mine = pd.DataFrame(clock_rows(tape, minute)).set_index("candidate_second")
        for r in g.itertuples():
            m = mine.loc[int(r.candidate_second)]
            rows += 1
            if int(m.n_buy) != int(r.n_buy):
                mismatches.append((event, int(r.candidate_second), "n_buy"))
            if bool(m.has_purchases and m.terminal_age_seconds <= 60) != bool(r.markout_supported):
                mismatches.append((event, int(r.candidate_second), "markout_supported"))
            if bool(m.has_post and m.pre_age_seconds <= 60) != bool(r.peak_supported):
                mismatches.append((event, int(r.candidate_second), "peak_supported"))
            for field in worst:
                a, b = m[field], getattr(r, field)
                if pd.isna(a) and pd.isna(b):
                    continue
                if pd.isna(a) != pd.isna(b) or abs(a - b) > 1e-12:
                    mismatches.append((event, int(r.candidate_second), field))
                else:
                    worst[field] = max(worst[field], abs(a - b))
    report = dict(status="PASS" if not mismatches and not missing else "FAIL",
                  events=int(stored.event_id.nunique()), clock_rows_compared=rows,
                  events_without_archive=missing, mismatches=mismatches[:50],
                  mismatch_count=len(mismatches), max_absolute_differences=worst)
    out.mkdir(parents=True, exist_ok=True)
    (out / "VALIDATION.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


# ---------------------------------------------------------------------------- main
def parse_minute(v) -> int:
    s = str(v).strip()
    if s.lstrip("-").isdigit():
        ns = int(s) * NS
    else:
        ts = pd.Timestamp(s)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        ns = int(ts.value)
    if ns % (60 * NS):
        raise ValueError(f"minute_utc is not on a UTC minute boundary: {v}")
    return ns


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", type=Path)
    ap.add_argument("--validate-grid", type=Path)
    ap.add_argument("--cache", type=Path, default=Path("binance_cache"))
    ap.add_argument("--output", type=Path, default=Path("cpi_results"))
    ap.add_argument("--offline", action="store_true", help="use cached archives only")
    a = ap.parse_args()
    if not a.events and not a.validate_grid:
        ap.error("give --events and/or --validate-grid")
    a.output.mkdir(parents=True, exist_ok=True)
    if a.validate_grid:
        rep = validate(a.validate_grid, a.cache, a.offline, a.output)
        print(json.dumps({k: rep[k] for k in ("status", "events", "clock_rows_compared", "mismatch_count")}, indent=2))
    if not a.events:
        return
    ev = pd.read_csv(a.events, dtype=str)
    need = {"event_id", "symbol", "minute_utc"}
    if not need <= set(ev.columns):
        sys.exit(f"events file needs columns {sorted(need)}")
    if ev.event_id.duplicated().any():
        sys.exit("event_id must be unique")
    ev["channel"] = ev.get("channel", pd.Series("minute-only", index=ev.index)).fillna("minute-only")
    ev["symbol"] = ev.symbol.str.strip().str.upper()
    skipped = dict(not_btc_quoted=int((~ev.symbol.str.endswith("BTC")).sum()), no_archive=0)
    ev = ev[ev.symbol.str.endswith("BTC")]
    grid_rows, no_archive = [], []
    for r in ev.itertuples():
        minute = parse_minute(r.minute_utc)
        tape, files, gaps = event_tape(r.symbol, minute, a.cache, a.offline)
        if tape is None:
            no_archive.append(dict(event_id=r.event_id, symbol=r.symbol, missing_days=";".join(gaps)))
            continue
        for row in clock_rows(tape, minute):
            row.update(event_id=r.event_id, channel=r.channel, symbol=r.symbol, minute_ns=minute)
            grid_rows.append(row)
    skipped["no_archive"] = len(no_archive)
    pd.DataFrame(no_archive).to_csv(a.output / "events_without_archive.csv", index=False)
    if not grid_rows:
        sys.exit("no events had archives; nothing to summarize")
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(a.output / "clock_grid.csv.gz", index=False, float_format="%.17g")
    env = envelopes(grid)
    env.to_csv(a.output / "event_envelopes.csv", index=False, float_format="%.17g")
    summary = summarize(env, grid, skipped)
    (a.output / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
