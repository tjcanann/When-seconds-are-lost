"""Synthetic end-to-end test of cpi_envelopes.py against an independent port of the
author's verify_against_trades.py logic. Run: python test_cpi_envelopes.py <workdir>"""
import hashlib
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

NS = 10**9
work = Path(sys.argv[1]); work.mkdir(parents=True, exist_ok=True)
cache = work / "cache"
rng = np.random.default_rng(7)
script = Path(__file__).with_name("cpi_envelopes.py")


def write_day(symbol, date, trades, micro=False, header=False):
    folder = cache / symbol; folder.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}-trades-{date}.zip"
    t = trades.time_ns // (1_000 if micro else 1_000_000)
    lines = [f"{i},{p:.8f},{q:.8f},{p*q:.8f},{tt},{'True' if bm else 'False'},True"
             for i, p, q, tt, bm in zip(trades.trade_id, trades.price, trades.quantity, t, trades.buyer_maker)]
    if header:
        lines = ["id,price,qty,quote_qty,time,is_buyer_maker,is_best_match"] + lines
    zp = folder / name
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr(name.replace(".zip", ".csv"), "\n".join(lines) + "\n")
    (folder / (name + ".CHECKSUM")).write_text(hashlib.sha256(zp.read_bytes()).hexdigest() + "  " + name + "\n")


def synth(symbol, start_ns, end_ns, n, first_id, unit_ns):
    times = np.sort(rng.integers(start_ns // unit_ns, end_ns // unit_ns, n)) * unit_ns
    # force ties at identical timestamps
    times[1::7] = times[0::7][: len(times[1::7])]
    times = np.sort(times)
    price = np.round(1e-6 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), 8)
    return pd.DataFrame(dict(trade_id=np.arange(first_id, first_id + n), time_ns=times, price=price,
                             quantity=np.round(rng.uniform(1, 500, n), 8), buyer_maker=rng.random(n) < 0.5))


def reference(tape, minute):
    """Port of verify_against_trades.py (author's replication package)."""
    tape = tape[(tape.time_ns >= minute - 660 * NS) & (tape.time_ns < minute + 659 * NS)].sort_values(["time_ns", "trade_id"])
    times, prices, quantities = tape.time_ns.to_numpy(), tape.price.to_numpy(), tape.quantity.to_numpy()
    buys = ~tape.buyer_maker.to_numpy()
    out = []
    for s in range(60):
        t = minute + s * NS
        purchase = (times >= t) & (times < t + 60 * NS) & buys
        pre = np.flatnonzero(times < t); terminal = np.flatnonzero(times < t + 600 * NS)
        post = (times >= t) & (times < t + 600 * NS)
        before = prices[pre[-1]] if len(pre) else np.nan
        endpoint = prices[terminal[-1]] if len(terminal) else np.nan
        high = max(prices[post]) if post.any() else np.nan
        q = math.fsum(quantities[purchase]); c = math.fsum(prices[purchase] * quantities[purchase])
        mark = endpoint * q / c - 1 if c and len(terminal) else np.nan
        peak = high / before - 1 if len(pre) and post.any() else np.nan
        pre_age = (t - times[pre[-1]]) / NS if len(pre) else np.nan
        term_age = (t + 600 * NS - times[terminal[-1]]) / NS if len(terminal) else np.nan
        out.append(dict(candidate_second=s, clock_time_ns=t, n_buy=int(purchase.sum()), pre_price=before,
                        terminal_price=endpoint, peak_price=high, markout=mark, peak=peak,
                        markout_supported=bool(purchase.any() and term_age <= 60),
                        peak_supported=bool(post.any() and pre_age <= 60)))
    return pd.DataFrame(out)


# Day files: SYMABTC dense (two days, crossing midnight), SYMBBTC sparse (stale prints), SYMCBTC in 2025 (microseconds)
d0 = pd.Timestamp("2021-03-14", tz="UTC").value; d1 = d0 + 86400 * NS
a0 = synth("SYMABTC", d0, d1, 40000, 1, 10**6); a1 = synth("SYMABTC", d1, d1 + 86400 * NS, 40000, 40001, 10**6)
write_day("SYMABTC", "2021-03-14", a0); write_day("SYMABTC", "2021-03-15", a1, header=True)
b0 = synth("SYMBBTC", d0, d1, 300, 1, 10**6); write_day("SYMBBTC", "2021-03-14", b0)
c_day = pd.Timestamp("2025-02-03", tz="UTC").value
c0 = synth("SYMCBTC", c_day, c_day + 86400 * NS, 30000, 1, 10**3); write_day("SYMCBTC", "2025-02-03", c0, micro=True)

events = [("E1", "SYMABTC", pd.Timestamp("2021-03-14 23:58", tz="UTC")),  # crosses midnight
          ("E2", "SYMABTC", pd.Timestamp("2021-03-14 12:00", tz="UTC")),
          ("E3", "SYMBBTC", pd.Timestamp("2021-03-14 09:31", tz="UTC")),  # sparse: partial/zero support
          ("E4", "SYMCBTC", pd.Timestamp("2025-02-03 17:00", tz="UTC")),  # microsecond file
          ("E5", "SYMDBTC", pd.Timestamp("2021-01-01 00:00", tz="UTC")),  # no archive in offline cache
          ("E6", "SYMAUSDT", pd.Timestamp("2021-03-14 12:00", tz="UTC"))]  # not BTC-quoted
pd.DataFrame([dict(event_id=e, symbol=s, minute_utc=m.strftime("%Y-%m-%d %H:%M"), channel="Test") for e, s, m in events]
             ).to_csv(work / "events.csv", index=False)
tapes = {"SYMABTC": pd.concat([a0, a1]), "SYMBBTC": b0, "SYMCBTC": c0}

# Synthetic "stored grid" in the replication package's format, built with the reference port
grid = []
for e, s, m in events[:4]:
    r = reference(tapes[s], m.value); r["event_id"] = e; r["native_symbol"] = s; grid.append(r)
pd.concat(grid).to_csv(work / "grid.csv.gz", index=False, float_format="%.17g")

run = subprocess.run([sys.executable, "-I", str(script), "--events", str(work / "events.csv"),
                      "--validate-grid", str(work / "grid.csv.gz"), "--cache", str(cache),
                      "--output", str(work / "out"), "--offline"], capture_output=True, text=True)
print(run.stdout[-1500:], run.stderr[-1500:])
assert run.returncode == 0
val = json.loads((work / "out" / "VALIDATION.json").read_text())
assert val["status"] == "PASS" and val["clock_rows_compared"] == 240, val

# Independent check of the event-level summary
summary = json.loads((work / "out" / "SUMMARY.json").read_text())
assert summary["skipped"] == {"not_btc_quoted": 1, "no_archive": 1}, summary["skipped"]
mine = pd.read_csv(work / "out" / "clock_grid.csv.gz")
for e, s, m in events[:4]:
    ref = reference(tapes[s], m.value).set_index("candidate_second")
    got = mine[mine.event_id == e].set_index("candidate_second")
    for f in ["n_buy", "markout", "peak", "pre_price", "terminal_price", "peak_price"]:
        a, b = got[f].to_numpy(float), ref[f].to_numpy(float)
        assert np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=0, atol=1e-12), (e, f)
env = pd.read_csv(work / "out" / "event_envelopes.csv")
full = env[(env.outcome == "markout") & (env.cap_seconds == 60) & (env.support == "full")]
for r in full.itertuples():
    ref = reference(tapes[dict((x[0], x[1]) for x in events)[r.event_id]], dict((x[0], x[2].value) for x in events)[r.event_id])
    assert ref.markout_supported.all() and abs(ref.markout.max() - r.upper) < 1e-15 and abs(ref.markout.min() - r.lower) < 1e-15
print("support by event (cap 60):")
print(env[env.cap_seconds == 60].pivot_table(index="event_id", columns="outcome", values="support", aggfunc="first"))
print("ALL TESTS PASSED")
