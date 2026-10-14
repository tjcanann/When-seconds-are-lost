# When seconds are lost

Replication outputs for **When seconds are lost: Timestamp coarsening in pump-and-dump event studies**, Taylor J. Canann, Louisiana Tech University.

Version: 14 October 2026. This release adds the requested channel-clustered bootstrap and reference-price concentration results to the audited clock analysis.

## Reproduce

With Python 3.12, from this directory:

```bash
python -m pip install -r requirements.txt
python code/reproduce.py
python code/verify_bootstrap.py
```

The analysis runs offline after installation. It uses the included derived grid: 316 events, 60 candidate seconds per event, and 18,960 rows. Results are saved in `results/`. No API keys or original Telegram histories are needed. The complete Rademacher bootstrap distribution has 8,388,608 assignments; enumeration usually takes seconds to a few minutes depending on hardware.

To preserve the supplied results while rerunning:

```bash
python code/reproduce.py --output reproduced_results
```

The verification script reads `results/`. All outcome values are decimal returns, not percentages; multiply by 100 for percentages or percentage-point changes. Log components are natural log points.

## Main outputs

| File in `results/` | Contents |
|---|---|
| `RED_PASSAGE_RESULTS.json` | All requested new numbers, with explicit samples |
| `wild_cluster_bootstrap.json` | One-sided p-value, statistic, standard error, exact multiplier counts and assumptions |
| `markout_upper_events.csv` | The 281 event-level observations underlying inference |
| `bootstrap_channel_sums.csv` | Sizes, sums, and residual sums for the 23 channels |
| `reference_event_statistics.csv` | Native/floor pre-prices, simple and log changes, and threshold flags for 244 peak events; `joint_full` identifies the 225-event common sample |
| `reference_by_channel.csv` | Concentration statistics for both the 225- and 244-event samples |
| `event_envelopes_all_caps.csv` | All 1,896 event/outcome/freshness-cap rows, including unsupported events |
| `support_counts.csv` | Full, partial, and zero-support counts |
| `envelope_summary.csv` | Headline means, bounds, sign counts, and freshness sensitivity |
| `leave_one_group_out.csv` | All 209 asset/channel omission runs |

## Results

The purchase-markout mean upper endpoint is −2.95098%. The one-sided null-imposed wild channel bootstrap-t test gives p = 0.0016913414, using 281 events and 23 channels. This is a model-based diagnostic requiring independence across channel clusters. Exact enumeration removes simulation error; it does not establish exact finite-sample inference or population representativeness.

On the 225-event common sample, 98 events (43.56%) have a pre-reference simple price increase greater than 5%, and the same 98 exceed 0.05 log points. The thresholds are different; their counts happen to coincide. Hit Pump Angels contains 39 of these 98 events and accounts for 58.06% of the signed sum of reference log changes. The median reference term is 0.024896 log points. These are accounting descriptions of recorded transaction prices, not evidence of first disclosures, insider trading, or causal price effects.

## Data and provenance

`data/candidate_clock_outcomes.csv.gz` is a losslessly compressed copy of the audited analytical CSV. `DATA_DICTIONARY.txt` describes its fields. `provenance/INPUT_PROVENANCE.json` supplies hashes and archive lineage. The source file includes outcome values at unsupported clocks for diagnosis; use the support flags or the explicitly implemented freshness rules before aggregation.

Public sources:

- PumpSense v1.1: https://doi.org/10.5281/zenodo.19616802 and https://github.com/AhmedMahrous00/icbc_2026_reproducibility
- Native-history source associated with Hu et al. (2023): https://github.com/Bayi-Hu/Pump-and-Dump-Detection-on-Cryptocurrency ; paper DOI https://doi.org/10.1145/3588686
- Binance public trades: https://data.binance.vision/ and https://github.com/binance/binance-public-data
- Separate CPI provenance exercise: https://gitlab.com/bristol-university-work/crypto-pump-and-dump at commit `25d37ef4c797aba66e55b595784c4e694f1dc15c`.

`provenance/binance_trade_archive_manifest.json` lists the 432 archived parsed-file sources, exchange ZIP URLs, retrieval times, exchange checksums, parsed-file hashes, row counts, and coverage windows. These files contain 5,801,643 retained executions across the broader inherited universe; they are not all observations in the 316-event clock analysis. A file hash identifies bytes, not economic authenticity.

This compact release reproduces the clock-summary statistics and the new red-passage calculations from the saved analytical grid. It does **not** include raw Telegram text, native Telegram IDs, raw exchange tapes, private correspondence, or third-party paper PDFs. It does not rerun source collection, historical clock corrections, original native-message matching, or the separate CPI reconciliation. Those audit inputs remain in the author's dated full archive; do not describe this compact release as containing every original input.

## Optional trade-tape verification

The author's full archive is `PumpDump_SSRN_Audit_and_Replication_2026-10-13.zip` (SHA-256 in `INPUT_PROVENANCE.json`). After extraction, run its `code/prepare_inputs.py`. The trade directory is then:

`PumpDump_SSRN_Audited_2026-10-13/tmp/PumpDump_Existing_Data_Revision_2026-10-12/inputs/trades/`

To independently reconstruct the analytical grid from those preserved tapes:

```bash
python code/verify_against_trades.py --trade-dir /absolute/path/to/inputs/trades
```

The saved `provenance/TRADE_RECHECK.json` documents the 14 October rerun: 18,960 clock rows, 316 events, and 305 relevant parsed files; reference, terminal, and peak prices matched exactly. Maximum markout difference was 6.67e-16 in return units. `BOOTSTRAP_RECHECK.json` records a second enumeration using an independent variance calculation. `BASELINE_RECHECK.json` compares the new aggregation code with prior audited numerical results. `CLEAN_RERUN.json` records the clean rerun check of the included outputs.

## Inference and interpretation

The fixed-tape bounds require no probability distribution for missing seconds. The additional bootstrap separately tests the mean of each event's upper endpoint, using channel-level Rademacher weights and CRV1 studentization under a zero-mean null. Since any compatible markout is at most its event upper endpoint, a negative expected upper endpoint is a sufficient condition for a negative expected markout under any clock rule on the same eligible population. This logical conservativeness does not guarantee bootstrap test size. Channels have 1–79 events; clustering does not address dependence across channels, inherited selection, or representativeness. The analysis is exploratory.

Method references: Cameron and Miller (2015), *A Practitioner's Guide to Cluster-Robust Inference*, https://faculty.econ.ucdavis.edu/faculty/cameron/research/Cameron_Miller_JHR_2014_July_09.pdf ; MacKinnon, Nielsen and Webb (2023), *Cluster-robust inference: A guide to empirical practice*, https://doi.org/10.1016/j.jeconom.2022.04.001 (open version https://arxiv.org/abs/2205.03285).

## Attribution and terms

Retain `licenses/UPSTREAM_NOTICES.txt`. Binance-derived data and analytical indicators are distributed with attribution to Binance Vision under its stated CC BY-NC-SA 4.0 terms; upstream source rights remain with their owners. The release does not imply endorsement by any source. A separate license for the author's new code has not been selected. Do not apply a blanket MIT license to the entire repository.

## Citation

Canann, Taylor J. (2026). *When seconds are lost: Timestamp coarsening in pump-and-dump event studies*. Replication outputs, version 2026-10-14. Louisiana Tech University. See `CITATION.cff`.

No repository URL or DOI has been assigned in this package. Add the actual public URL to the paper after uploading this directory's contents. This package does not publish anything automatically.
