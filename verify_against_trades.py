"""Optional independent reconstruction from the preserved individual trade tapes.

python code/verify_against_trades.py --trade-dir /path/to/inputs/trades
Raw exchange trade extracts are in the author's archived replication package,
not in this derived-data release. No message text is read by this program.
"""
from pathlib import Path
import argparse
from functools import lru_cache
import hashlib
import json
import math
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NS = 10**9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=ROOT/"provenance/TRADE_RECHECK.json")
    args = ap.parse_args()
    raw = pd.read_csv(ROOT/"data/candidate_clock_outcomes.csv.gz",
        dtype={c:"Int64" for c in ["clock_time_ns", "native_time_ns", "terminal_time_ns", "pre_time_ns", "terminal_trade_id", "pre_trade_id"]},
        float_precision="round_trip")
    checked_files = set()

    @lru_cache(maxsize=32)
    def load(symbol, date):
        p = args.trade_dir/f"{symbol}-{date}.csv.gz"
        m = json.loads(p.with_suffix(".json").read_text())
        assert hashlib.sha256(p.read_bytes()).hexdigest() == m["parsed_sha256"]
        assert m["checksum_verified"] and m["status"] == "verified"
        d = pd.read_csv(p, float_precision="round_trip").sort_values(["time_ns", "trade_id"])
        assert len(d) == m["rows"] and not d.trade_id.duplicated().any()
        assert d.execution_count.eq(1).all() and d.buyer_maker.dtype == bool
        checked_files.add(p.name)
        return d, m

    maximum = {k:0.0 for k in ["markout", "peak", "pre_price", "terminal_price", "peak_price"]}
    checked = 0
    for event, group in raw.groupby("event_id", sort=True):
        minute = int(group.clock_time_ns.min())
        lower, upper = minute-60*NS, minute+659*NS
        dayns = 86400*NS
        frames = []
        for day in range(lower//dayns*dayns, (upper-1)//dayns*dayns+1, dayns):
            date = pd.Timestamp(day, tz="UTC").strftime("%Y-%m-%d")
            d, meta = load(group.native_symbol.iloc[0], date)
            # All cap checks require coverage from minute-300 seconds. Coverage
            # itself is not inferred from an isolated retained context print.
            reach = max(minute-300*NS, day)
            for a, b in sorted(meta["signature"]["windows_ns"]):
                if a <= reach < b:
                    reach = b
            assert reach >= min(upper, day+dayns)
            frames.append(d[d.time_ns.ge(minute-660*NS) & d.time_ns.lt(upper)])
        tape = pd.concat(frames, ignore_index=True).sort_values(["time_ns", "trade_id"])
        assert not tape.trade_id.duplicated().any()
        times = tape.time_ns.to_numpy()
        prices = tape.price.to_numpy()
        quantities = tape.quantity.to_numpy()
        buys = ~tape.buyer_maker.to_numpy()
        for row in group.itertuples():
            t = int(row.clock_time_ns)
            purchase = (times >= t) & (times < t+60*NS) & buys
            pre = np.flatnonzero(times < t)
            terminal = np.flatnonzero(times < t+600*NS)
            post = (times >= t) & (times < t+600*NS)
            before = prices[pre[-1]] if len(pre) else np.nan
            endpoint = prices[terminal[-1]] if len(terminal) else np.nan
            high = max(prices[post]) if post.any() else np.nan
            quantity = math.fsum(quantities[purchase])
            cost = math.fsum(prices[purchase]*quantities[purchase])
            mark = endpoint*quantity/cost-1 if cost and len(terminal) else np.nan
            peak = high/before-1 if len(pre) and post.any() else np.nan
            assert int(purchase.sum()) == row.n_buy
            pre_age = (t-times[pre[-1]])/NS if len(pre) else np.nan
            terminal_age = (t+600*NS-times[terminal[-1]])/NS if len(terminal) else np.nan
            assert (purchase.any() and terminal_age <= 60) == row.markout_supported
            assert (post.any() and pre_age <= 60) == row.peak_supported
            for field, value in [("markout", mark), ("peak", peak), ("pre_price", before), ("terminal_price", endpoint), ("peak_price", high)]:
                stored = getattr(row, field)
                if np.isnan(value):
                    assert np.isnan(stored)
                else:
                    delta = abs(value-stored)
                    maximum[field] = max(maximum[field], delta)
                    assert delta < 1e-12, (event, row.candidate_second, field, delta)
            checked += 1
    report = dict(status="PASS", events=raw.event_id.nunique(), clock_rows=checked,
        parsed_files_rechecked=len(checked_files), max_absolute_errors=maximum,
        method="Independent Boolean-window reconstruction and math.fsum from hashed individual trade extracts; no measurement implementation imported.",
        limits="Checks the 316-event clock grid against preserved trade files. Does not recollect messages, validate original collection coverage, or redownload original exchange ZIP files.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
