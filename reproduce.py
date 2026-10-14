"""Reproduce the clock-grid results and the 14 October red-passage additions.

Run from any directory: python code/reproduce.py [--output /path/to/results]
Requires numpy and pandas; no network, API, credentials, or original messages.
"""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def save_json(value, path):
    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, np.generic):
            return clean(x.item())
        if isinstance(x, float) and not np.isfinite(x):
            return None
        return x
    path.write_text(json.dumps(clean(value), indent=2, allow_nan=False) + "\n")


def save_csv(frame, path):
    frame.to_csv(path, index=False, float_format="%.17g")


def read_grid():
    p = ROOT / "data/candidate_clock_outcomes.csv.gz"
    metadata = json.loads((ROOT / "provenance/INPUT_PROVENANCE.json").read_text())
    assert hashlib.sha256(p.read_bytes()).hexdigest() == metadata["compressed_sha256"]
    assert hashlib.sha256(gzip.decompress(p.read_bytes())).hexdigest() == metadata["uncompressed_sha256"]
    integer = {c: "Int64" for c in ["clock_time_ns", "native_time_ns",
        "terminal_time_ns", "terminal_trade_id", "pre_time_ns", "pre_trade_id"]}
    d = pd.read_csv(p, dtype=integer, float_precision="round_trip")
    assert len(d) == 18960 and d.event_id.nunique() == 316
    assert not d.duplicated(["event_id", "candidate_second"]).any()
    assert d.groupby("event_id").candidate_second.apply(lambda x: set(x) == set(range(60))).all()
    assert (d.clock_time_ns - d.candidate_second * 10**9).mod(60 * 10**9).eq(0).all()
    assert (d.native_time_ns - d.clock_time_ns).eq((d.native_second - d.candidate_second) * 10**9).all()
    return d


def summarize(d):
    return dict(n=len(d), assets=d.canonical_asset_id.nunique(),
        channels=d.pump_channel.nunique(), mean_lower=d.lower.mean(),
        mean_upper=d.upper.mean(), mean_native=d.native_value.mean(),
        mean_floor=d.floor_value.mean(), median_width=d.width.median(),
        p90_width=d.width.quantile(.9), negative_all_clocks=int(d.upper.lt(0).sum()),
        nonnegative_all_clocks=int(d.lower.ge(0).sum()),
        sign_ambiguous=int((d.lower.lt(0) & d.upper.ge(0)).sum()))


def envelopes(raw):
    rows = []
    for cap in [30, 60, 300]:
        for outcome in ["markout", "peak"]:
            if outcome == "markout":
                support = raw.archive_covered & raw.n_buy.gt(0) & raw.markout.notna() & raw.terminal_age_seconds.le(cap)
            else:
                support = raw.archive_covered & raw.peak.notna() & raw.pre_age_seconds.le(cap) & ~raw.no_post_print
            d = raw.assign(supported=support)
            if cap == 60:
                assert support.eq(raw[outcome + "_supported"]).all()
            for event, g in d.groupby("event_id", sort=True):
                v = g[g.supported]
                n = len(v)
                lower = v[outcome].min() if n == 60 else np.nan
                upper = v[outcome].max() if n == 60 else np.nan
                nat = g[g.candidate_second.eq(g.native_second)].iloc[0]
                floor = g[g.candidate_second.eq(0)].iloc[0]
                rows.append(dict(event_id=event, outcome=outcome, cap_seconds=cap,
                    canonical_asset_id=g.canonical_asset_id.iloc[0], pump_channel=g.pump_channel.iloc[0],
                    supported_clocks=n, support="full" if n == 60 else "partial" if n else "zero",
                    lower=lower, upper=upper, width=upper-lower,
                    native_value=nat[outcome] if nat.supported else np.nan,
                    floor_value=floor[outcome] if floor.supported else np.nan))
    return pd.DataFrame(rows)


def wild_cluster_test(d, out):
    """Null-imposed WCR11 bootstrap-t for an intercept, lower-tail alternative.

    For H0: mu=0, the restricted residual is the original outcome y_i=u_i.
    All observations in a channel receive the same Rademacher multiplier.
    CRV1 correction G/(G-1)*(N-1)/(N-k) reduces to G/(G-1) for k=1.
    Enumerate all 2^G vectors, counting ties as extreme; no Monte Carlo seed.
    """
    z = d.groupby("pump_channel", sort=True).upper.agg(n="size", sum_upper="sum").reset_index()
    sizes = z.n.to_numpy(dtype=float)
    sums = z.sum_upper.to_numpy(dtype=float)
    N, G = len(d), len(z)
    assert N == 281 and G == 23
    mean = sums.sum()/N
    factor = G/(G-1)
    residual_sums = sums-sizes*mean
    se = np.sqrt(factor * np.dot(residual_sums, residual_sums))/N
    observed = mean/se
    count, ties, total = 0, 0, 2**G
    tolerance = 1e-12
    near_other = np.inf
    for start in range(0, total, 32768):
        numbers = np.arange(start, min(start+32768, total), dtype=np.uint32)
        weights = ((numbers[:, None] >> np.arange(G, dtype=np.uint32)) & 1).astype(float)*2-1
        boot_sums = weights*sums
        boot_means = boot_sums.sum(axis=1)/N
        residual = boot_sums-boot_means[:, None]*sizes
        boot_se = np.sqrt(factor*np.sum(residual**2, axis=1))/N
        assert np.all(boot_se > 0)
        statistics = boot_means/boot_se
        distance = np.abs(statistics-observed)
        count += int(np.sum(statistics <= observed+tolerance))
        ties += int(np.sum(distance <= tolerance))
        other = distance[distance > tolerance]
        if len(other):
            near_other = min(near_other, float(other.min()))
    z["mean_upper"] = z.sum_upper/z.n
    z["event_weight"] = z.n/N
    z["unrestricted_residual_sum"] = residual_sums
    save_csv(z, out/"bootstrap_channel_sums.csv")
    result = dict(outcome="event maximum purchase markout", N=N, channels=G,
        null="E[u_i] >= 0, evaluated at boundary zero", alternative="E[u_i] < 0",
        method="Null-imposed Rademacher wild cluster bootstrap-t, CRV1 (WCR11)",
        equal_event_mean=mean, cluster_robust_se=se, observed_t=observed,
        exhaustive_multiplier_assignments=total, lower_tail_count_including_ties=count,
        one_sided_p=count/total, ties=ties, tie_tolerance=tolerance,
        nearest_non_tied_statistic_distance=near_other,
        smallest_cluster=int(sizes.min()), largest_cluster=int(sizes.max()),
        largest_cluster_share=float(sizes.max()/N),
        sampling_scope="Exploratory model-based diagnostic; channel independence assumed. Not a random sample of all attempts.",
        enumeration_scope="Exact enumeration of the bootstrap distribution, not exact finite-sample population inference.")
    save_json(result, out/"wild_cluster_bootstrap.json")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"results")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    raw = read_grid()
    env = envelopes(raw)
    save_csv(env, out/"event_envelopes_all_caps.csv")
    save_csv(env.groupby(["outcome", "cap_seconds", "support"]).size().reset_index(name="events"), out/"support_counts.csv")
    main_env = env[env.cap_seconds.eq(60) & env.support.eq("full")]
    ids = {o: set(main_env[main_env.outcome.eq(o)].event_id) for o in ["markout", "peak"]}
    joint = ids["markout"] & ids["peak"]
    assert [len(ids["markout"]), len(ids["peak"]), len(joint)] == [281, 244, 225]
    save_csv(main_env[main_env.outcome.eq("markout")].sort_values("event_id"), out/"markout_upper_events.csv")
    summary = []
    for outcome in ["markout", "peak"]:
        for cap in [30, 60, 300]:
            g = env[env.outcome.eq(outcome) & env.cap_seconds.eq(cap) & env.support.eq("full")]
            summary.append(dict(outcome=outcome, cap_seconds=cap, sample="own_full", **summarize(g)))
        g = main_env[main_env.outcome.eq(outcome) & main_env.event_id.isin(joint)]
        summary.append(dict(outcome=outcome, cap_seconds=60, sample="joint_full", **summarize(g)))
    save_csv(pd.DataFrame(summary), out/"envelope_summary.csv")
    leave = []
    for outcome in ["markout", "peak"]:
        d = main_env[main_env.outcome.eq(outcome)]
        for unit in ["canonical_asset_id", "pump_channel"]:
            for name in sorted(d[unit].unique()):
                leave.append(dict(outcome=outcome, omission_unit=unit, omitted=name, **summarize(d[d[unit].ne(name)])))
    leave = pd.DataFrame(leave)
    assert len(leave) == 209
    save_csv(leave, out/"leave_one_group_out.csv")
    target = leave[leave.outcome.eq("peak") & leave.omission_unit.eq("pump_channel")].sort_values(["mean_upper", "omitted"]).iloc[0]
    channel = target.omitted
    pair = raw[raw.candidate_second.eq(raw.native_second)].merge(raw[raw.candidate_second.eq(0)], on="event_id", suffixes=("_native", "_floor"), validate="one_to_one")
    pair = pair[pair.event_id.isin(ids["peak"])].copy()
    pair["pump_channel"] = pair.pump_channel_native
    pair["canonical_asset_id"] = pair.canonical_asset_id_native
    pair["joint_full"] = pair.event_id.isin(joint)
    pair["reference_simple_return"] = pair.pre_price_native/pair.pre_price_floor-1
    pair["reference_log_change"] = np.log(pair.pre_price_native/pair.pre_price_floor)
    pair["above_simple_5pct"] = pair.reference_simple_return.gt(.05)
    pair["above_log_005"] = pair.reference_log_change.gt(.05)
    pair["maximum_component"] = np.log(pair.peak_price_floor/pair.peak_price_native)
    pair["delta_log_peak"] = np.log1p(pair.peak_floor)-np.log1p(pair.peak_native)
    pair["delta_peak"] = pair.peak_floor-pair.peak_native
    pair["delta_markout"] = pair.markout_floor-pair.markout_native
    error = (pair.delta_log_peak-pair.maximum_component-pair.reference_log_change).abs().max()
    assert error < 1e-12
    cols = ["event_id", "canonical_asset_id", "pump_channel", "joint_full",
        "pre_price_native", "pre_price_floor", "reference_simple_return", "reference_log_change",
        "above_simple_5pct", "above_log_005", "maximum_component", "delta_log_peak", "delta_peak", "delta_markout"]
    save_csv(pair[cols].sort_values("event_id"), out/"reference_event_statistics.csv")
    descriptive, by_channel = [], []
    for sample, sel in [("joint225", pair[pair.joint_full]), ("peak244", pair)]:
        hit = sel[sel.pump_channel.eq(channel)]
        outside = sel[sel.pump_channel.ne(channel)]
        ref = sel.reference_log_change
        s = dict(sample=sample, n=len(sel), channels=sel.pump_channel.nunique(),
            mean_reference_log=ref.mean(), median_reference_log=ref.median(),
            quantiles_reference_log={str(q): ref.quantile(q) for q in [0, .1, .25, .5, .75, .9, .95, 1]},
            above_simple_5pct=int(sel.above_simple_5pct.sum()), above_log_005=int(sel.above_log_005.sum()),
            fraction_above_log_005=sel.above_log_005.mean(),
            influential_channel=channel, channel_n=len(hit),
            channel_above_simple_5pct=int(hit.above_simple_5pct.sum()),
            channel_above_log_005=int(hit.above_log_005.sum()),
            channel_fraction_of_exceedances=hit.above_log_005.sum()/sel.above_log_005.sum(),
            channel_fraction_of_sample=len(hit)/len(sel),
            channel_share_sum_reference_log=hit.reference_log_change.sum()/ref.sum(),
            channel_mean_reference_log=hit.reference_log_change.mean(),
            outside_n=len(outside), outside_above_log_005=int(outside.above_log_005.sum()),
            outside_mean_reference_log=outside.reference_log_change.mean(),
            outside_median_reference_log=outside.reference_log_change.median())
        descriptive.append(s)
        for name, g in sel.groupby("pump_channel", sort=True):
            by_channel.append(dict(sample=sample, pump_channel=name, n=len(g),
                mean_reference_log=g.reference_log_change.mean(), median_reference_log=g.reference_log_change.median(),
                sum_reference_log=g.reference_log_change.sum(),
                share_total_reference_log=g.reference_log_change.sum()/ref.sum(),
                above_simple_5pct=int(g.above_simple_5pct.sum()), above_log_005=int(g.above_log_005.sum())))
    save_csv(pd.DataFrame(by_channel), out/"reference_by_channel.csv")
    j = pair[pair.joint_full]
    decomposition = dict(n=len(j), mean_log_change=j.delta_log_peak.mean(),
        mean_reference_component=j.reference_log_change.mean(), mean_maximum_component=j.maximum_component.mean(),
        reference_share_of_mean_log_change=j.reference_log_change.mean()/j.delta_log_peak.mean(),
        mean_simple_peak_change=j.delta_peak.mean(), median_simple_peak_change=j.delta_peak.median(),
        mean_markout_change=j.delta_markout.mean(), max_identity_error=error)
    raw_joint = raw[raw.event_id.isin(joint)].copy()
    raw_joint["positive_peak_negative_markout"] = raw_joint.peak.gt(0) & raw_joint.markout.lt(0)
    combos = raw_joint.groupby("event_id").positive_peak_negative_markout.agg(["all", "any"])
    nat = raw_joint[raw_joint.candidate_second.eq(raw_joint.native_second)]
    combo = dict(n=len(joint), native=int(nat.positive_peak_negative_markout.sum()),
        every_clock=int(combos["all"].sum()), some_clock=int(combos["any"].sum()))
    bootstrap = wild_cluster_test(main_env[main_env.outcome.eq("markout")], out)
    results = dict(reference_samples=descriptive, decomposition=decomposition,
        influential_channel=channel, peak_own_original_upper=float(main_env[main_env.outcome.eq("peak")].upper.mean()),
        peak_own_without_channel_upper=target.mean_upper, peak_remaining_n=int(target.n),
        joint_classifications=combo, bootstrap=bootstrap)
    save_json(results, out/"RED_PASSAGE_RESULTS.json")
    print(json.dumps({"bootstrap_p": bootstrap["one_sided_p"], "reference_joint225": descriptive[0], "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()
