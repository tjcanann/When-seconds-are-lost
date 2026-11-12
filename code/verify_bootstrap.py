"""Independent verification of the bootstrap using the expanded variance identity.

Does not import reproduce.py. Enumerates half the sign vectors and exploits
t(-w)=-t(w); also checks event-level OLS calculations on 100 fixed sign draws.
"""
from pathlib import Path
import json
import math
import numpy as np
import pandas as pd

R = Path(__file__).resolve().parents[1]
d = pd.read_csv(R/"results/event_envelopes_all_caps.csv", float_precision="round_trip")
d = d[(d.outcome=="markout") & (d.cap_seconds==60) & (d.support=="full")]
groups = sorted(d.pump_channel.unique())
y = d.upper.to_numpy()
gi = d.pump_channel.map({g:i for i,g in enumerate(groups)}).to_numpy()
n = np.bincount(gi).astype(float)
s = np.array([math.fsum(y[gi==g]) for g in range(len(groups))])
N, G = len(y), len(groups)
factor = G/(G-1)
mu = math.fsum(y)/N
se = math.sqrt(factor*sum(math.fsum(y[gi==g]-mu)**2 for g in range(G)))/N
t = mu/se
count = 0
max_direct_error = 0.
for start in range(0, 2**(G-1), 16384):
    indices = np.arange(start, min(start+16384, 2**(G-1)), dtype=np.uint32)
    w = np.ones((len(indices), G))
    w[:, 1:] = 2*((indices[:, None] >> np.arange(G-1, dtype=np.uint32))&1)-1.0
    totals = np.sum(w*s, axis=1)
    means = totals/N
    # sum_g (w_g*s_g-n_g*mean)^2, expanded independently.
    variance_sums = np.sum(s*s)-2*means*np.sum(w*(n*s), axis=1)+means**2*np.sum(n*n)
    tb = totals/np.sqrt(factor*variance_sums)
    count += int(np.sum(tb <= t+1e-12) + np.sum(-tb <= t+1e-12))
    if start == 0:
        for k in range(100):
            yy = y*w[k, gi]
            mm = math.fsum(yy)/N
            ss = math.sqrt(factor*sum(math.fsum(yy[gi==g]-mm)**2 for g in range(G)))/N
            max_direct_error = max(max_direct_error, abs(mm/ss-tb[k]))
stored = json.loads((R/"results/wild_cluster_bootstrap.json").read_text())
assert count == stored["lower_tail_count_including_ties"]
assert abs(t-stored["observed_t"]) < 1e-12
assert abs(se-stored["cluster_robust_se"]) < 1e-12
assert max_direct_error < 1e-12
report = dict(status="PASS", numerator=count, denominator=2**G, one_sided_p=count/2**G,
    independent_enumeration="Half-grid plus sign symmetry, expanded cluster-variance identity, math.fsum group inputs",
    direct_event_level_draws_checked=100, max_direct_t_difference=max_direct_error)
(R/"provenance/BOOTSTRAP_RECHECK.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
