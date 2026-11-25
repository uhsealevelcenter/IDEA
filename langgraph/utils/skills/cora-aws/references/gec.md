# CORA Gulf, Atlantic, and Caribbean (GEC)

Use the live `cora_gec/` prefix to confirm filenames and availability.

| Request | Product to check |
|---|---|
| Hourly water level | `native_grid/water_levels/zarr/`, `fort.63` integer map or time-series reference |
| Native maximum or its timing | `maxele.63` under native water levels. Inspect actual time fields and aggregation window, including `time_of_zeta_max` when present. |
| Hourly 500 m water level | `500m_grid/water_levels/`, `500m_grid_zeta` daily or aggregate reference |
| Published gridded statistic | `500m_grid/derived_products/`, choose `daily_max/`, `monthly_statistics/monthly_tidal_YYYY.nc`, or `peaks_over_threshold/peaks.nc` |
| Shoreline water level | `native_grid_shoreline/water_levels/`, choose the state/territory and use a matching `_mm_chunks=` Zarr directory where available. Some locations have only NetCDF. |
| Shoreline tidal constituents or high-tide flood outlooks | `native_grid_shoreline/derived_products/`, inspect `tide_constits.h5`, `beta.h5`, or `htf_predictions/` for the requested constituent, adjustment factor, or outlook run. |
| Wave height, direction, or peak period | `native_grid/waves/`, choose `swan_HS.63`, `swan_DIR.63`, or `swan_TPS.63` and the map or time-series layout. Verify direction convention before interpreting or aggregating angles. |
| Mesh or bathymetry | Read `x`, `y`, `element`, and `depth` from a native product if present, or use `native_grid/fort.14`. Check connectivity indexing before triangulation. |

- Examples of verified reference names in `native_grid/water_levels/zarr/` are `fort.63_2022_map.zarr` and `fort.63_1979-1983_timeseries.zarr`. Some unqualified annual and maximum products use floating-point meters. For the same grid, variable, statistic, and time range, prefer the verified integer variant where available. If a native maximum is available only as floats, keep the requested maximum window and use the published float product. For a custom period, compute from the integer native hourly series if appropriate.
- For a requested 500 m daily maximum, use a monthly shard such as `500m_grid/derived_products/daily_max/zarr/daily_max_202212.nc.zarr` for a short period. Inspect the array dimensions and coordinate names. This product exposes `daily_max_zeta(nodes, time)` in meters with `lat` and `lon` on the node dimension. Select nodes by geographic mask rather than assuming separate rectangular axes. Use the native product for custom native-grid maxima, and state the time window and completeness.
- Shoreline points are a subset of the mesh. Confirm the supplied region and available points before choosing a shoreline store. Published high-tide flood outlooks are derived experimental products. Distinguish their issue month and forecast meaning from dates of historical reanalysis.
