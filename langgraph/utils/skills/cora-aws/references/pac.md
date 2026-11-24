# CORA Pacific (PAC)

Use the live `cora_pac/native_grid/` prefix. The bucket currently publishes native-grid water levels and waves for PAC. Check the live listing before assuming a GEC 500 m, shoreline, or derived product has a PAC counterpart.

- For hourly water level, select a `water_levels/zarr/fort.63` integer reference. An annual map example is `fort.63_2024_map.nc.zarr` under that prefix. A multi-year point-series example is `fort.63_2023-2025_timeseries.nc.zarr`. The references checked contain `zeta(time, node)`, `x` and `y` for coordinates, `depth`, and `element` for connectivity. Confirm index origin before triangulating. For published native maxima, look for `maxele.63` in the same prefix and verify the period and available time-of-maximum fields.
- For hourly wave height, direction, or peak period, look for `swan_HS.63`, `swan_DIR.63`, or `swan_TPS.63` under `waves/` or `waves/zarr/`. Choose the appropriate chunk layout and inspect units and wave direction conventions. For mesh or bathymetry, use embedded native mesh data when available.
- NOAA documents a roughly 16-hour oscillation in PAC water levels and periods of larger gauge discrepancies, notably June–December 2009 and March–August 2019. When relevant, cite the current NOAA known issues before interpreting variability or comparisons.

## Regional maps and animations

- For a region such as Oʻahu, define and verify geographic bounds. Keep the native nodes unless interpolation is requested. Normalize longitudes and build a spatial mask once from the mesh. Report the count of selected wet and dry/invalid nodes.
- Open the annual map reference for the requested time range. Read one full time slice at a time and immediately retain only the selected regional nodes. Cache the compact subset in `/workspace` with timestamps, coordinates, node indices, depth, units, MSL reference for water levels, and source key. Render repeat plots and animations from this subset.
- Label the figure with product, units, UTC timestamp, and reference for water levels. Render dry or invalid nodes separately. For animations, use one linear normalization, consistent ticks, and one colorbar across frames. State any clipping. Use a shared palette for GIFs or MP4 when an encoder is available.
