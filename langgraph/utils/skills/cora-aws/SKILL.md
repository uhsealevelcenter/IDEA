---
name: cora-aws
description: Find, subset, and analyze NOAA Coastal Ocean Reanalysis (CORA) data on public AWS S3 for the Gulf, Atlantic, Caribbean, and Pacific. Use for CORA water levels, waves, mesh and shoreline data, derived products, maps, animations, or comparisons with tide gauges.
---

# NOAA CORA on AWS

CORA is a modeled reanalysis. CORA water levels are relative to mean sea level (MSL). Use the selected product's metadata and NOAA documentation to confirm units, encoding, and the vertical reference. Use `co-ops-api` for observed NOAA gauge data and official station datums, and `co-ops-tadc` for tidal datum calculation.

## Choose the product

- **GEC** covers the U.S. Gulf and Atlantic coasts and the Caribbean (1979–2022). Load the `gec` route for native-grid water levels and waves, 500 m grids, shoreline subsets, or derived products.
- **PAC** covers the U.S. West Coast, southeast Alaska, and Hawaiʻi (1979–2025). Load the `pac` route for Pacific water levels or waves. Check the selected product and wet mesh for actual availability at the requested time and location.
- Load `gauge-comparison` alongside the domain workflow when comparing with observations. For other questions, load only the applicable route or component via `view_skill`.
- For water levels, default to the native grid and a verified integer-encoded product. Choose the layout for the access pattern. A point series and a spatial animation can require different objects even for the same location and period. Honor requested grids, derived products, or encodings. Use a compatible floating-point native product if an integer native product cannot satisfy the request, and identify the encoding used. A shoreline subset is appropriate when it covers the requested coastal analysis.

## Subset, verify, and report

1. Resolve the exact AWS key through the live catalog or prefix listing. Verify the variable, time coverage, coordinate convention, grid, chunking, storage dtype, units, fill values, and scale/offset before reading the field. Decode missing values before unit conversion and avoid applying scaling twice.
2. Align the target longitude convention with the dataset and use the mesh or grid to confirm coverage. For point requests, pick the nearest valid wet node or cell from a small set of geographic candidates. Report distance and model depth when available. Flag a distant or poorly representative match. For a region, select intersecting nodes or cells and verify that the subset contains finite values.
3. Select variable, time, and space before materializing data. Use UTC and exact timestamps when available. Verify continuity and deduplicate shard boundaries. If nearest-time sampling is justified, report the offset. Match aggregate statistics to their published time windows.
4. Check finite range and missing fraction before plotting or exporting. Keep reusable subsets in `/workspace` and deliverables in `/outputs`. Include NOAA CORA attribution, domain, product, AWS key, variable, units and reference, time period, spatial selection, and any aggregation or interpolation.
