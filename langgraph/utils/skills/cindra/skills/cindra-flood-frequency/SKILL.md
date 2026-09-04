---
name: cindra-flood-frequency
description: Use for CIndRA minor flood-frequency analyses using UHSLC hourly water levels, MHHW datum, 30 cm above MHHW threshold, daily maxima, storm-year flood counts, regional flood matrices, and approved flood-frequency plots.
---

# CIndRA Flood Frequency

## Purpose

Run or explain CIndRA minor sea-level flood-frequency workflows using UHSLC hourly water levels, MHHW datum, daily maxima, and May-April storm-year counts.

## Source of Truth

- Notebook: `notebooks/historical/c_sea_level_ff.ipynb`
- Core module: `functions/sea_level.py`
- Plotting module: `functions/sea_level_plotting.py`
- Downloaders: `functions/data_downloaders.py`

## Default Prototype Period

For Malakal/PICCM prototype products, use storm year `1983` through the latest complete storm year available unless the user specifies otherwise. A storm year runs May 1-April 30 and is labeled by starting year.

## Definition

Phase 1 minor flood frequency uses:

- UHSLC hourly tide-gauge water levels where available;
- MHHW datum where available and compatible;
- threshold: `30 cm above MHHW`;
- event basis: daily maximum water-level exceedance;
- aggregation: May-April storm year;
- primary metric: flood days/year.

A flood day occurs when daily maximum water level reaches or exceeds `30 cm above MHHW`. Do not calculate flood days from daily means. If MHHW is unavailable, do not fabricate datum or threshold context.

## Workflow

1. Confirm site setup exists.
2. Confirm station-level Level 2 screening for the requested timeframe.
3. Exclude stations failing the station-level Level 2 gate from primary products unless explicitly producing an exploratory sensitivity product.
4. For passing stations, retain all available years/storm-years/months and carry QC diagnostics forward.
5. Confirm MHHW datum.
6. Convert water levels to height relative to MHHW where needed.
7. Apply `30 cm above MHHW` threshold.
8. Compute daily maxima.
9. Count flood days.
10. Aggregate by May-April storm year and optionally by month.
11. Preserve true no-data periods as missing/empty/`NaN`, not zero.
12. Save tables and provenance.

## Approved Figures

Use approved helpers only, including:

- `plot_histogram_with_threshold`
- `plot_flood_counts_with_trend`
- `plot_flood_counts_with_oni`
- `plot_flood_days_heatmap`
- `plot_flood_matrix_summary`
- `plot_oni_only`
- `plot_monthly_contribution_vertical`
- `plot_regional_flood_frequency_overview`

For `Regional`, the report-facing product is the station-year flood-day matrix with annual regional total-count bar chart using `plot_regional_flood_frequency_overview`.

## Monthly Products

Use storm-year month order: `May, Jun, Jul, Aug, Sep, Oct, Nov, Dec, Jan, Feb, Mar, Apr`.

Distinguish monthly count figures from percent contribution figures. Percent contribution is monthly flood days divided by total flood days across all months in the analyzed period, multiplied by 100. Raw counts must never be labeled as percentages.

## Required Outputs Where Data Support Them

- Annual storm-year flood-day table and plot.
- For `Regional`, station-year matrix plus annual regional total-count bar chart.
- `flood_frequency_summary.csv`.
- Station-level Level 2 pass/fail table.
- Station-year/storm-year flood-day table with QC diagnostics.
- Total flood days, average flood days/year, maximum year, and trend/p-value where approved.
- Monthly contribution table if monthly contribution figures are generated.

## Validation

Confirm MHHW availability, station-level Level 2 screening, retention of partial-data periods as diagnostics, true no-data periods as missing, approved plotting helpers, complete storm years, daily maxima, May-April convention, and correct count/percent labels.
