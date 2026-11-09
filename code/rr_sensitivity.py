"""Revision analyses for "When seconds are lost" (Economics Letters R&R, MS EL70396).

Reads a 60-clock event grid and writes RR_RESULTS.json plus event-level CSVs.

Works on either grid format:
  * the verified sample: data/candidate_clock_outcomes.csv.gz (has native_second)
  * the CPI minute-only run: cpi_results/clock_grid.csv.gz (from cpi_envelopes.py)

Usage:
  python rr_sensitivity.py GRID.csv.gz OUTPUT_DIR

Analyses (all at freshness caps 30, 60, 300 seconds; 60 is primary):
  1. Clock envelope on fully supported events (the published bound, recomputed).
  2. Referee point 2: a bound on the mean over events with a defined outcome at the
     assigned second. Fully supported events always enter. A partially supported event
     can enter at its lowest or highest defined value, or drop out by being assigned a
     second at which the outcome is undefined. Events with no defined clock never enter.
     Lower endpoint: add partially supported events in ascending order of their minima
     and keep the smallest running mean. Upper endpoint: add them in descending order
     of their maxima and keep the largest running mean. (The optimum over all subsets
     is attained by such a prefix.)
  3. Referee point 3: peak with the reference price fixed at the last execution before
     the minute starts, p^-(m_i), and only the ten-minute peak window shifted with the
     clock: Ktilde_i(t) = H_i(t) / p^-(m_i) - 1. Defined when p^-(m_i) is no older than
     the cap and at least one execution occurs in [t, t+600).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CAPS = (30, 60, 300)
PRIMARY = 60


def load(path):
    g = pd.read_csv(path)
    if "has_purchases" not in g:
        g["has_purchases"] = g.n_buy.fillna(0) > 0
    if "has_post" not in g:
        g["has_post"] = g.peak_price.notna()
    for c in ("has_purchases", "has_post"):
        g[c] = g[c].astype(str).str.lower().eq("true") if g[c].dtype == object else g[c].astype(bool)
    if "channel" not in g:
        g["channel"] = g.pump_channel
    g = g.sort_values(["event_id", "candidate_second"]).reset_index(drop=True)
    counts = g.groupby("event_id").candidate_second.agg(["count", "min", "max", "nunique"])
    assert (counts["count"] == 60).all() and (counts["nunique"] == 60).all(), "each event needs 60 clocks"
    assert (counts["min"] == 0).all() and (counts["max"] == 59).all()
    return g


def support(g, outcome, cap):
    if outcome == "markout":
        return g.has_purchases & g.terminal_age_seconds.le(cap)
    return g.has_post & g.pre_age_seconds.le(cap)


def pct(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(100 * float(x), 4)


def event_table(g, outcome, ok, native):
    v = g[outcome].where(ok)
    t = pd.DataFrame({"event_id": g.event_id, "v": v, "ok": ok, "s": g.candidate_second})
    e = t.groupby("event_id").agg(n=("ok", "sum"), lo=("v", "min"), hi=("v", "max"))
    e["floor"] = t[t.s == 0].set_index("event_id").v
    if native is not None:
        e["native"] = t[g.candidate_second.eq(g.native_second)].set_index("event_id").v
    e["support"] = np.where(e.n == 60, "full", np.where(e.n == 0, "zero", "partial"))
    return e


def prefix_bound(full_vals, part_vals, lower):
    """Extreme running mean when partials are added in order; returns (mean, k)."""
    s, n = float(np.sum(full_vals)), len(full_vals)
    order = np.sort(part_vals) if lower else -np.sort(-part_vals)
    best, best_k = (s / n if n else (np.inf if lower else -np.inf)), 0
    for k, x in enumerate(order, start=1):
        s, n = s + x, n + 1
        m = s / n
        if (lower and m < best) or (not lower and m > best):
            best, best_k = m, k
    return best, best_k


def brute_check(full_vals, part_lo, part_hi):
    """Exhaustive check of the prefix rule on a small random problem."""
    rng = np.random.default_rng(0)
    import itertools
    for _ in range(200):
        f = rng.normal(size=rng.integers(1, 5))
        lo = rng.normal(size=rng.integers(0, 7))
        hi = lo + rng.exponential(size=len(lo))
        best_lo, best_hi = np.inf, -np.inf
        for mask in itertools.product([0, 1], repeat=len(lo)):
            m = np.array(mask, bool)
            best_lo = min(best_lo, (f.sum() + lo[m].sum()) / (len(f) + m.sum()))
            best_hi = max(best_hi, (f.sum() + hi[m].sum()) / (len(f) + m.sum()))
        assert abs(prefix_bound(f, lo, True)[0] - best_lo) < 1e-12
        assert abs(prefix_bound(f, hi, False)[0] - best_hi) < 1e-12


def main():
    grid_path, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    brute_check(None, None, None)
    g = load(grid_path)
    native = g.native_second if "native_second" in g else None
    res = {"grid": grid_path.name, "events": int(g.event_id.nunique()), "rows": int(len(g))}
    keep_events = {}

    for cap in CAPS:
        for outcome in ("markout", "peak"):
            ok = support(g, outcome, cap)
            e = event_table(g, outcome, ok, native)
            F, P = e[e.support == "full"], e[e.support == "partial"]
            lo, klo = prefix_bound(F.lo.to_numpy(), P.lo.to_numpy(), True)
            hi, khi = prefix_bound(F.hi.to_numpy(), P.hi.to_numpy(), False)
            blk = dict(
                full=int(len(F)), partial=int(len(P)), zero=int((e.support == "zero").sum()),
                full_floor_pct=pct(F.floor.mean()), full_lower_pct=pct(F.lo.mean()), full_upper_pct=pct(F.hi.mean()),
                varying_lower_pct=pct(lo), varying_lower_partials_added=int(klo), varying_lower_events=int(len(F) + klo),
                varying_upper_pct=pct(hi), varying_upper_partials_added=int(khi), varying_upper_events=int(len(F) + khi),
                floor_defined_events=int(e.floor.notna().sum()), floor_defined_mean_pct=pct(e.floor.mean()),
                full_or_partial_events=int(len(F) + len(P)))
            if native is not None:
                blk.update(full_native_pct=pct(F.native.mean()), native_defined_events=int(e.native.notna().sum()),
                           native_defined_mean_pct=pct(e.native.mean()))
            if outcome == "markout":
                blk.update(sign_switch_full=int(((F.lo < 0) & (F.hi >= 0)).sum()),
                           partial_negative_all_defined=int((P.hi < 0).sum()),
                           partial_nonnegative_all_defined=int((P.lo >= 0).sum()))
            res[f"{outcome}_cap{cap}"] = blk
            if cap == PRIMARY:
                # event-level record of which events enter each endpoint
                Plo = P.sort_values("lo").iloc[:klo].index
                Phi = P.sort_values("hi", ascending=False).iloc[:khi].index
                rec = e[["n", "lo", "hi", "floor", "support"]].copy()
                rec["enters_lower"] = (rec.support == "full") | rec.index.isin(Plo)
                rec["enters_upper"] = (rec.support == "full") | rec.index.isin(Phi)
                rec.to_csv(out / f"{outcome}_cap{cap}_membership.csv")
                keep_events[outcome] = F.index

        # fixed minute-start benchmark for peaks
        ref = g[g.candidate_second == 0].set_index("event_id")
        ref_ok = ref.pre_age_seconds.le(cap) & ref.pre_price.notna()
        refp = ref.pre_price.where(ref_ok)
        kt = g.peak_price / g.event_id.map(refp) - 1
        okt = g.has_post & g.event_id.map(ref_ok).fillna(False).astype(bool)
        t = pd.DataFrame({"event_id": g.event_id, "v": kt.where(okt), "ok": okt, "s": g.candidate_second})
        et = t.groupby("event_id").agg(n=("ok", "sum"), lo=("v", "min"), hi=("v", "max"))
        et["floor"] = t[t.s == 0].set_index("event_id").v
        if native is not None:
            et["native"] = t[g.candidate_second.eq(g.native_second)].set_index("event_id").v
        # same events as the moving-reference peak envelope at this cap
        okp = support(g, "peak", cap)
        ep = event_table(g, "peak", okp, native)
        same = ep.index[ep.support == "full"]
        s = et.loc[same]
        assert (s.n == 60).all()
        moving_width = ep.loc[same].hi.mean() - ep.loc[same].lo.mean()
        fb = dict(
            events=int(len(same)), moving_lower_pct=pct(ep.loc[same].lo.mean()),
            moving_upper_pct=pct(ep.loc[same].hi.mean()),
            fixed_floor_pct=pct(s.floor.mean()), fixed_lower_pct=pct(s.lo.mean()), fixed_upper_pct=pct(s.hi.mean()),
            fixed_width_pp=pct(s.hi.mean() - s.lo.mean()), moving_width_pp=pct(moving_width),
            share_of_range_remaining=round(float((s.hi.mean() - s.lo.mean()) / moving_width), 4),
            median_event_width_pp=pct((s.hi - s.lo).median()),
            events_width_over_1pp=int(((s.hi - s.lo) > 0.01).sum()),
            own_full_support=int((et.n == 60).sum()))
        own = et[et.n == 60]
        fb.update(own_floor_pct=pct(own.floor.mean()), own_lower_pct=pct(own.lo.mean()), own_upper_pct=pct(own.hi.mean()))
        if native is not None:
            fb.update(fixed_native_pct=pct(s.native.mean()), moving_native_pct=pct(ep.loc[same].native.mean()))
        res[f"fixed_benchmark_peak_cap{cap}"] = fb
        if cap == PRIMARY:
            et.to_csv(out / "fixed_benchmark_peak_cap60_events.csv")
            # common sample (both outcomes fully supported at 60 s)
            em = event_table(g, "markout", support(g, "markout", cap), native)
            common = em.index[em.support == "full"].intersection(same)
            c = et.loc[common]
            cp = ep.loc[common]
            cm = em.loc[common]
            res["common_cap60"] = dict(
                events=int(len(common)),
                markout_lower_pct=pct(cm.lo.mean()), markout_upper_pct=pct(cm.hi.mean()),
                markout_floor_pct=pct(cm.floor.mean()),
                peak_lower_pct=pct(cp.lo.mean()), peak_upper_pct=pct(cp.hi.mean()), peak_floor_pct=pct(cp.floor.mean()),
                fixed_lower_pct=pct(c.lo.mean()), fixed_upper_pct=pct(c.hi.mean()), fixed_floor_pct=pct(c.floor.mean()))
            if native is not None:
                res["common_cap60"].update(markout_native_pct=pct(cm.native.mean()), peak_native_pct=pct(cp.native.mean()),
                                           fixed_native_pct=pct(c.native.mean()))

    (out / "RR_RESULTS.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
