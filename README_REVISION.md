# Revision analyses (Economics Letters R&R, MS EL70396)

`code/rr_sensitivity.py` computes, at freshness caps of 30, 60 and 300 seconds:

1. the clock envelope on fully supported events (reproducing the published counts and endpoints);
2. the varying-membership bound (letter Eq. 3), in which partially supported events enter at their
   lowest or highest defined value or drop out at an undefined second;
3. the fixed-benchmark peak (letter Eq. 4), with the reference price fixed at the last trade before
   the minute starts and only the ten-minute peak window shifted.

It needs only pandas and numpy and runs offline.

```bash
# verified sample (316 events)
python code/rr_sensitivity.py data/candidate_clock_outcomes.csv.gz results/revision/verified
# minute-only CPI application (7,920 events), from the cpi_envelopes.py run
python code/rr_sensitivity.py cpi_results/clock_grid.csv.gz results/revision/cpi
```

Each run writes `RR_RESULTS.json` plus three event-level files at the 60-second cap:

- `markout_cap60_membership.csv` and `peak_cap60_membership.csv`: for each event, the number of
  supported clocks, the minimum and maximum over defined clocks, the minute-floor value, the support
  class, and whether the event enters the lower and upper varying-membership endpoints.
- `fixed_benchmark_peak_cap60_events.csv`: event-level fixed-reference peak minima, maxima, and
  floor and native values.

The script checks the prefix rule for the varying-membership bound against exhaustive enumeration
on 200 random problems before it reads any data.
