---
name: co-ops-tadc
description: Use NOAA CO-OPS Tidal Analysis Datum Calculator (TADC) to compute tidal datums from observed water-level time series. Cross-listed with co-ops-api for NOAA CO-OPS station data, high/low waters, monthly means, control station metadata, and official datums.
---

# CO-OPS TADC

Use this skill when the user asks to calculate tidal datums from a water-level time series, run the NOAA CO-OPS Tidal Analysis Datum Calculator, compare computed datums with NOAA CO-OPS datums, or prepare observed water levels for datum analysis.

This skill is cross-listed with `co-ops-api`. Use `co-ops-api` when NOAA CO-OPS observations, predictions, high/low waters, monthly means, station metadata, control station data, or official datums are needed as inputs or comparison products.

## Scope

TADC computes tidal datums from water-level time series. It identifies tidal high and low waters after applying a low-pass Butterworth digital filter that removes high-frequency variability above 4 cycles per day. NOAA CO-OPS official datums are produced with Curve Fit Manual Verification (CFMV), which includes human oversight, so TADC results should be described as calculator-derived unless they are official CO-OPS products.

For method context, cite:

Licate LA, Huang L and Dusek G (2017) *A Comparison of Datums Derived from CO-OPS Verified Data Products and Tidal Analysis Datum Calculator*. NOAA Technical Report NOS CO-OPS 085.

For NOAA CO-OPS support, refer users to `tide.predictions@noaa.gov` when appropriate.

## Running TADC

The `tadc` Python package is expected to be installed in the IDEA environment.

Run with exactly one input source:

- `fname`: path to a CSV file following the NOAA Tidal Analysis Datums Calculator User's Guide format.
- `data`: a Pandas DataFrame containing the same required time-series fields.

Required location arguments:

- `Subordinate_Lat`
- `Subordinate_Lon`

Example patterns:

- File input: `tadc.run(fname="test_timeseries_simple.csv", Subordinate_Lat=37, Subordinate_Lon=-79)`
- DataFrame input: read the CSV with Pandas, then call `tadc.run(data=timeseries, Subordinate_Lat=37.8, Subordinate_Lon=-79.6)`

Do not pass both `fname` and `data`. Do not omit both.

## Outputs

The returned object commonly includes:

- `readme`: run summary and notes.
- `high_lows`: identified high and low waters.
- `plots`: monthly plots.
- `subordinate_monthly_means`: subordinate station monthly means.
- `datums`: computed datum results.

Save generated tables, figures, and notes under IDEA's standard output directory. Report the input source, coordinates, datum/reference level if known, units, time zone, coverage period, data gaps, TADC warnings, and whether results are TADC-derived or official CO-OPS values.

## Module Map

- `run.py`: main orchestration for CSV or DataFrame inputs.
- `qa.py` and `qc.py`: input data checks; preserve known errors and explain failed checks.
- `filter_defs.py`: low-pass Butterworth filter definitions.
- `control_data.py`: retrieves and prepares CO-OPS control station monthly means, high/lows, accepted datums, and determines computation method.
- `tides.py`: identifies and classifies tides as lower low, higher low, lower high, or higher high using semi-diurnal or diurnal picking logic.

## Validation

- Confirm the input file or DataFrame follows NOAA TADC format before running.
- Confirm timestamp timezone, water-level units, vertical datum/reference, sampling interval, and record coverage.
- Inspect QA/QC failures instead of suppressing them.
- Compare against CO-OPS official datums only when station metadata and datum definitions are aligned.
- Attribute source code as NOAA CO-OPS TADC: `https://github.com/NOAA-CO-OPS/CO-OPS-Tidal-Analysis-Datum-Calculator`.
