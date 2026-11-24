---
name: gesla-uhslc
description: Discover, retrieve, and validate Global Extreme Sea Level Analysis (GESLA) tide-gauge records through UHSLC ERDDAP. Use when GESLA is requested or when its contributed high-frequency records are needed for sea-level or extremes analysis coverage.
---

# GESLA through UHSLC ERDDAP

## Scope

GESLA (Global Extreme Sea Level Analysis) compiles contributed high-frequency tide-gauge records for sea-level and extremes research. Attribute access to UHSLC ERDDAP and measurements to the record's original contributor.

Keep Fast Delivery (FD) as SEA's default unless GESLA is requested or needed for coverage. Use exact GESLA `record_id` strings instead of three-digit UHSLC IDs. GESLA selection is independent of `fd_span` and uses its own units, fill values, and vertical reference. Multiple records may represent one physical station.

## Access

Dataset ID: `global_hourly_gesla`.

1. Inspect the [ERDDAP metadata](https://uhslc.soest.hawaii.edu/erddap/info/global_hourly_gesla/index.html) for current definitions, units, fill values, and reference information.
2. Discover records using the metadata-only inventory request below. Select an exact `record_id`, resolving names with coordinates and contributor metadata. Preserve station codes as text.
3. Retrieve `time,sea_level,flag1,flag2,record_id` for that record and a bounded UTC interval. Prefer an inclusive start and exclusive end. Encode quotes and comparison operators as in the example.
4. Check HTTP success before parsing and surface ERDDAP's original error message on failure. Standard `.csv` has a units row immediately after its header. Skip that row and retain all observations. `.csvp` includes units in column labels.
5. Parse time as UTC, sort, and check duplicates, actual cadence, gaps, flag counts, and the finite value range. Compute coverage and range from the selected record. Preserve both flags and document exclusions.

[Metadata inventory](https://uhslc.soest.hawaii.edu/erddap/tabledap/global_hourly_gesla.csv?record_id,station_name,station_code,agency_id,station_country_code,latitude,longitude&distinct%28%29)

[Tested one-day request for Honolulu](https://uhslc.soest.hawaii.edu/erddap/tabledap/global_hourly_gesla.csv?time,sea_level,flag1,flag2,record_id&record_id=%22honolulu-1612340-usa-noaa%22&time%3E=2024-01-01T00:00:00Z&time%3C2024-01-02T00:00:00Z)

## Interpretation

- **Sea level** is in meters at this endpoint. Mask non-finite values and the declared fill value (previously `-99.9999`). ERDDAP CSV may return `NaN`.
- **Vertical reference** is **Unspecified** in the endpoint metadata. Do not assume Station Zero or a tidal datum, infer datum conversions, or interpret these values as flood thresholds. Check authoritative record-specific references and datum continuity before absolute-height comparisons or long-term trend interpretation.
- **Time** is UTC at this endpoint. Check timestamps to establish cadence.
- **Coordinates** are in degrees. Longitude has used 0–360°. Verify and normalize when needed.
- **`flag1`** uses 0 for no QC, 1 for correct, 2 for interpolated, 3 for doubtful, 4 for isolated spike or wrong value, and 5 for missing.
- **`flag2`** uses 1 for recommended for analysis and 0 for do not use. Default to finite values with `flag2 == 1`. This can include unchecked and interpolated values. Describe that distinction and state any stricter selection.
- **Verify the dataset release** independently of the file format version, such as `5.0`.

In results, identify the record, contributor, UHSLC access source, UTC period, units, vertical reference, and QC selection.

## Direct users to these sources

- [ERDDAP data-access form](https://uhslc.soest.hawaii.edu/erddap/tabledap/global_hourly_gesla.html) for variables, constraints, and download formats.
- [ERDDAP subset interface](https://uhslc.soest.hawaii.edu/erddap/tabledap/global_hourly_gesla.subset) for browsing record metadata.
- [GESLA About](https://gesla787883612.wordpress.com/about/) for purpose and background.
- [GESLA Format](https://gesla787883612.wordpress.com/format/) for original GESLA-3 text-file headers and flags. Use the ERDDAP metadata for CSV field definitions.
- [GESLA Downloads](https://gesla787883612.wordpress.com/downloads/) for releases, access terms, and citation guidance. Follow the current guidance, retain contributor attribution, and verify bibliographic details before giving formal citations.
