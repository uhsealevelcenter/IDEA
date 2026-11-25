---
name: uhslc-incoming-tide-gauges
description: Use for UHSLC incoming, non-quality-controlled tide-gauge data, individual sensor diagnostics, or comparisons with Fast Delivery (FD). Retain SEA's FD-first policy for routine sea-level analysis.
---

# UHSLC Incoming Tide-Gauge Data

## Source and discovery

- Use the [UHSLC incoming data directory](https://uhslc.soest.hawaii.edu/tech/tg_postproc/). Always label results **UHSLC incoming data — not quality controlled**. Screening does not make them FD or Research Quality (RQ).
- Use `get_station_info_tool` for canonical station metadata and preserve three-digit IDs. Resolve filename codes from `uhslc_code` in the [UHSLC station catalog](https://uhslc.soest.hawaii.edu/data/meta.geojson). Honolulu (`057`) uses `hono`. For incoming records outside Fast Delivery coverage, identify the station in the full catalog.
- Inspect the live listing for `{code}_combined.{YY}.csv`. For example, `.23` means 2023. Discover available years from the listing, including earlier files. Report errors or unavailable files without silently substituting another product.
- Read the actual headers and download only needed station-years to `/workspace`. Retain raw files, source URLs, retrieval times, and available server modification metadata.

## Parse and validate

- Columns are `Day`, `Time`, and variable sensor channels. Build timestamps from January 1 of the filename year + (`Day` − 1) days + `Time`. Validate integer day-of-year, leap-year and time bounds, parsing, ordering, and duplicates. Preserve seconds and skip FD hourly snapping for incoming data.
- Headers do not establish the time zone. Verify UTC/GMT or label the time basis provisional. Confirm the time basis before claiming alignment with FD.
- Determine cadence, first and last valid observations, and coverage **per sensor**. One-minute rows may hold six-minute readings, and subminute rows occur. Blanks may be unsampled intervals. Use actual sensor readings to determine coverage. Preserve schema changes and conflicting duplicates, and document any gap filling.

## Interpret and screen sensors

- Confirm channel meanings. Convert identified water levels from **m relative to Station Zero** to **mm relative to Station Zero** (`mm = m × 1000`), retaining the original values. Treat `PWI`, `RAD`, `RA2`, `RAS`, and `PRS` as candidate water-level channels. Verify each channel before selecting it or combining data.
- `ATM` is barometric. `SW1` and `SW2` are switch diagnostics with potentially nonbinary values. Verify auxiliary units and encoding. Keep undocumented channels unclassified and off water-level axes.
- Parse missing cells and tokens as NaN and mask SEA's `-32767` sentinel in water-level channels before conversion. Flag nonfinite values, spikes, flatlines, extreme constants, offsets, and cadence changes. Investigate values such as `9.999` and `-131.072` before treating them as missing. Retain valid zero and negative levels.
- Keep raw and screened results distinct. Document screening rules and counts and any interpolation, correction, or sensor substitution.

## Analyze and report

- Inventory sensor meanings and units, cadence, valid ranges, coverage, and flags. Without a documented primary sensor, provide the inventory and ask which sensor to analyze. Keep sensors distinct.
- State the averaging method, missing-data handling, and minimum coverage. Calculate coverage against the verified sensor cadence and account for irregular sampling.
- Label plots and exports with station and ID, sensor, time basis, units, Station Zero reference for water levels, and non-QC status. Save requested deliverables under `/outputs` and provide download links.
- For FD comparisons, follow SEA's FD workflow and verify time basis, quantity, units, averaging windows, and datum realization or sensor offsets. Confirm alignment even if both products say “Station Zero.” Use authoritative UHSLC datum tables and SEA's conversion formula for datum conversions. Label any fitted offset as such. Treat apparent extremes or flooding as unverified until corroborated.
