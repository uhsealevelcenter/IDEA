---
name: cora-aws-beta
description: Retrieve and validate NOAA Coastal Ocean Reanalysis (CORA) V1.1 beta water-level data from public AWS S3 Zarr and Kerchunk holdings for the U.S. East, Gulf, and Caribbean coasts. Use for CORA assimilated water levels, 500 m grid products, daily maximum products, and native-grid products.
---

# CORA AWS Beta

Use this skill when the user asks for NOAA Coastal Ocean Reanalysis (CORA) water-level data from Amazon Web Services, including CORA V1.1 assimilated water levels, 500 m grid water levels, daily maximum water levels, native/unstructured grid water levels, maps, point time series, spatial subsets, or derived exploratory products.

This skill is under development. Treat paths, domains, metadata conventions, and access behavior as beta guidance that must be verified at runtime. If the user wants to improve, revise, or propose updates to this skill, use `skill-creator` to draft an updated `SKILL.md` for their review or download.

CORA is a model/reanalysis product, not direct observations or official tidal datums. For this version, CORA data are available only for the U.S. East Coast, Gulf Coast, and Caribbean Coast domains. Do not use this skill for West Coast, Alaska, Hawaii, Pacific Islands, or other regions unless a future CORA version is verified to include them.

## Routing And Exclusions

- Use this skill for CORA V1.1 public AWS S3 holdings.
- Use `co-ops-api` when the user asks for NOAA CO-OPS API data, official station observations, predictions, metadata, or non-CORA NOAA station products.
- Use `co-ops-tadc` when the user asks to compute or compare tidal datums with NOAA CO-OPS/TADC workflows.
- Use `aquaview-ocean-data` only as a fallback for dataset discovery after CORA-specific holdings have been checked.
- If the requested location is outside the U.S. East, Gulf, or Caribbean Coast CORA domain, say so and route to a more appropriate source.

## Known AWS Holdings

Public bucket and prefix:

- `s3://noaa-nos-cora-pds/V1.1/assimilated/`

Known subtrees:

- `500m_grid/zarr/`: 500 m grid water-level holdings.
- `derived_products/500m_grid/daily_max/zarr/`: derived daily maximum 500 m grid products.
- `native_grid/zarr/`: native/unstructured grid water-level holdings.
- `native_grid_shoreline/`: shoreline-related native-grid holdings; Zarr coverage must be verified at runtime.

Observed useful paths:

- `500m_grid/zarr/500m_grid_zeta_1979-2022.zarr`: aggregate 500 m grid water levels.
- `500m_grid/zarr/500m_grid_zeta_YYYYMMDD.nc.zarr`: daily 500 m grid water-level shards.
- `derived_products/500m_grid/daily_max/zarr/daily_max_1979-2022.zarr`: aggregate daily maximum reference/store; verify whether it is a directory Zarr store or Kerchunk JSON reference.
- `derived_products/500m_grid/daily_max/zarr/daily_max_YYYYMM.nc.zarr`: monthly daily-maximum shards.
- `native_grid/zarr/fort.63_1979-2022.zarr`: aggregate native-grid water levels.
- `native_grid/zarr/fort.63_YYYY.nc.zarr`: annual native-grid shards.

## Product Selection

- For hourly or sub-daily gridded water levels, start with `500m_grid_zeta`.
- For daily maxima, start with `daily_max` products instead of recomputing from hourly values unless the user needs a custom maximum definition.
- For native/unstructured mesh fidelity, use `native_grid/fort.63` products.
- For a single day, prefer the daily 500 m grid shard if the aggregate store is slow.
- For a single month of daily maxima, prefer the monthly daily-maximum shard if the aggregate store is slow.
- For multi-day or multi-year operations, try aggregate stores first, but fall back to daily, monthly, or annual shards when metadata access or chunking is more efficient.

## Access Rules

- Use anonymous public S3 access with `anon=True` for `s3fs` or fsspec storage options.
- Confirm each S3 key exists before opening it.
- Do not download full CORA stores. Open lazily with xarray/fsspec and compute only after spatial and temporal subsetting.
- Treat `.zarr` suffixes as ambiguous until inspected: a path may be either a directory-like Zarr store or a single Kerchunk JSON reference object.
- If the target is a directory/prefix, try opening as Zarr with `consolidated=True`, then `consolidated=False`.
- If the target is file-like and the first non-whitespace byte is `{`, treat it as a Kerchunk JSON reference and open with fsspec `reference` filesystem using S3 as the remote protocol and anonymous remote options.
- For Kerchunk references, direct fsspec opening is acceptable. If direct opening fails, stream the JSON once to a local temporary or output file and open that reference.
- Verify dimension names, coordinate names, variable names, units, and fill/missing conventions after opening; do not assume `zeta`, `time`, `lat`, `lon`, or `nodes`.

## Runtime Inspection

Before data extraction or plotting, inspect and report when relevant:

- Product path and whether it opened as directory Zarr or Kerchunk JSON reference.
- Dataset dimensions and chunking.
- Data variables and coordinate variables.
- Candidate time coordinate name, time coverage, time step, and UTC interpretation.
- Candidate spatial coordinate names, longitude convention, and domain bounds.
- Variable long name, standard name, units, datum/reference surface, and missing-value indicators.
- Global attributes including CORA version, title, description, datum, and coverage fields when available.

## Domain And Location Validation

Always verify the requested location or bounding box lies inside the loaded product domain before extracting data.

- Compute and report dataset latitude and longitude bounds.
- Normalize requested longitudes to match the dataset convention (`-180..180` or `0..360`) before distance or bounding-box tests.
- If a requested point is outside the CORA V1.1 East/Gulf/Caribbean domain, do not silently choose the nearest node. Tell the user it is outside this CORA version's domain and route to another source or ask for an in-domain location.
- If a nearest point is more than about 10 km from the requested target for a coastal point time series, flag it as suspicious unless the user requested a broad demonstration.
- For point extraction, select the nearest valid finite node/cell, not merely the nearest coordinate, and report distance to target.
- For bounding boxes, confirm at least one valid finite node/cell exists inside the subset. If none exist, expand the search only if appropriate and document the expansion.

## Missing Data And Valid Nodes

- After extracting any sample, compute the missing-data fraction.
- If all values are missing, do not present the plot or table as successful. Diagnose whether the location is outside the wet domain, outside coverage, masked, or mismatched to the selected product.
- For point time series, examine a small ranked set of nearby candidate nodes/cells and choose the nearest candidate with finite values for the requested time range.
- Report selected node/cell identifier if available, coordinate, distance from target, missing fraction, and value range.
- For maps, report the percentage of finite cells/nodes in the plotted subset.

## Retrieval Workflow

1. Define the request: product type, variable, date/time range, point or bounding box, grid type, desired output, and whether exploratory approximations are acceptable.
2. Verify the request is in the U.S. East, Gulf, or Caribbean Coast CORA V1.1 domain.
3. List or verify relevant S3 keys.
4. Open lazily using the access rules above.
5. Inspect metadata and coordinate/domain bounds.
6. Validate requested time and space before reading the main data variable.
7. Subset first, then compute. Keep initial samples small.
8. For point time series, find the nearest valid finite node/cell and extract only that time series.
9. For spatial maps, limit the bounding box and downsample/bin dense native-grid output when needed.
10. Plot or save outputs only after confirming finite data exist.
11. Report provenance, validation checks, and caveats.

## Time Selection

- Confirm the requested date/time is covered by the selected product.
- For exact hourly data, prefer exact time selection. If nearest-time selection is used, report the requested time, selected time, and offset.
- For daily maximum products, clarify whether the time stamp represents the day, start of day, end of day, or another product-specific convention based on metadata.
- If spanning multiple daily, monthly, or annual shards, verify continuity and avoid duplicate or missing timestamps.

## Spatial Subsetting

- For 500 m grid products, use coordinate/domain checks before nearest-neighbor or bounding-box selection.
- For native/unstructured grids, load only coordinate arrays needed for the node mask before loading water levels.
- For dense native-grid maps, bin or interpolate selected nodes to a modest regular lat/lon grid for exploratory visualization unless the user explicitly needs native-node rendering.
- Cap exploratory map points/pixels to a manageable number and state any downsampling, binning, or interpolation.

## Output Standard

Save generated figures, CSVs, NetCDFs, metadata summaries, and logs under IDEA's standard output directory. Return clickable links for saved outputs.

For CORA-derived results, include:

- CORA version/product name.
- S3 bucket/key or path pattern.
- Access mode: directory Zarr or Kerchunk JSON reference.
- Variable name and units.
- Time range and selected time handling.
- Spatial selection method, target location/bounds, selected node/cell/pixel location, and distance for point selections.
- Missing-data fraction and finite value range.
- Any aggregation, binning, interpolation, nearest-neighbor selection, or downsampling.
- Caveat that CORA is a reanalysis/model product, not observations or official tidal datums.

## Caveats

- CORA public AWS holdings may be reorganized; always verify paths at runtime.
- Metadata may differ between aggregate and shard products; inspect each opened dataset.
- Longitude convention may vary by product or derived workflow; verify before selection.
- Coastal masks and wet/dry domains can produce all-missing values near shore or outside the model wet domain.
- A `.zarr` path can be a Kerchunk JSON object despite its suffix.
- Do not claim authoritative inventory completeness from a shallow S3 listing.
- Do not treat exploratory nearest-node samples as station observations.
