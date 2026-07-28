---
name: cindra-sea-level-trend
description: Use for CIndRA sea-level trend analyses: absolute altimetry trends, relative tide-gauge trends, trend maps, regional trend analysis, National/EEZ combined maps, trend time series, station-altimetry comparisons, and Palau/Malakal trend validation.
---

# CIndRA Sea-Level Trend

## Purpose

Run or explain approved CIndRA sea-level trend workflows comparing absolute altimetry trends and relative tide-gauge trends. Preserve the physical distinction between satellite altimetry and tide gauges.

## Source of Truth

- Notebook: `notebooks/historical/a_sea_level_trend.ipynb`
- Core module: `functions/sea_level.py`
- Plotting module: `functions/sea_level_plotting.py`
- Downloaders: `functions/data_downloaders.py`
- Altimetry source: project CMEMS/Copernicus monthly `0.25 degree` NetCDF

## Required Inputs

- `site_config_path`
- `product_profile`: `Regional` or `National/EEZ`
- `trend_period`: default report epoch `1993-2025`; use `1993-2022` only for current Palau/Malakal validation.
- `tidal_epoch_or_baseline`: use `1993-2012` where applicable.
- `qc_reference` when station data are used.
- `source_versions` for UHSLC, CMEMS/Copernicus altimetry, and boundaries.

## Unit Policy

Normalize source sea-level values to `mm` for calculations unless repository helpers already do so. Report trend rates in `mm/yr`. Report total sea-level change over the epoch in `cm` or `cm/epoch` for maps. Do not label a `cm/epoch` map as `mm/yr`.

## Workflow

1. Load active site configuration.
2. Resolve station, boundary, CMEMS box, and trend dates.
3. Use target epoch `1993-2025`; if unavailable, use a clearly labeled provisional endpoint.
4. Load project CMEMS/Copernicus monthly `0.25 degree` altimetry NetCDF.
5. Load UHSLC station data through approved repository functions.
6. Apply QC references to station products.
7. Compute absolute altimetry trends and relative tide-gauge trends using approved repository helpers.
8. Keep station-relative and altimetry-absolute products separate.
9. Generate figures only through approved plotting helpers.
10. Save tables/metrics/provenance with method IDs, periods, units, sources, and spatial extraction metadata.

## Approved Trend Figures

Use approved helpers in `functions/sea_level_plotting.py`, including:

- `plot_magnitude_map`
- `plot_magnitude_map_background`
- `plot_altimetry_trend_timeseries`
- `plot_tide_gauge_trend_timeseries`
- `plot_combined_trends`
- `plot_enso_scatter`
- `plot_combined_altimetry_tide_gauge_trend_map`
- `plot_national_eez_combined_trend_map`
- `plot_regional_altimetry_trend_map_filled_tide_gauges`

Do not create final trend figures with ad hoc plotting code.

## Required Products Where Data Support Them

- Station-relative trend time series, trend line, rate table, sea-level change in cm, uncertainty/significance where supported, metadata, and limitations.
- Altimetry-absolute trend time series/map/table using the approved CMEMS source.
- Integrated station-altimetry comparison table/map/time-series with relative and absolute quantities side by side.
- For `National/EEZ`, combined satellite-plus-tide-gauge trend map/table where gridded altimetry and at least one QC-qualified UHSLC station are available.
- For `Regional`, regional gridded absolute altimetry trend map with domain-average trend annotation and optional station markers with clear absolute/relative labels.

## Label Requirements

Label altimetry as absolute sea level and tide gauges as relative sea level. Captions and legends for combined maps must state that the quantities are displayed together but not merged into a single metric.

ENSO-context products are optional. They must document climate-index source, preprocessing, regression method, sample size, lag/season definition if used, and limitations. They must not alter primary trend calculations unless an approved method requires it.

## Validation

Confirm overlapping periods before comparisons, approved altimetry source, station-to-grid or area-extraction method, units, boundary behavior, QC status, approved plotting helper, and clear absolute-versus-relative labels.
