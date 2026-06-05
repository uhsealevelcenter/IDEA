---
name: aquaview-ocean-data
description: Discover, access, and visualize oceanographic, atmospheric, and marine datasets through the AquaView MCP server as a fallback after IDEA's preferred built-in data sources, but before general web search.
---

# AquaView Ocean Data

Use this skill when IDEA's primary data paths do not cover the user's requested oceanographic, atmospheric, or marine dataset, and before resorting to a general web search. Prefer IDEA's existing system instructions, UHSLC data tools, configured MCP tools, and specialized skills first when they directly apply.

## Scope

AquaView is useful for broad dataset discovery and access, including SST, SST anomalies, buoys and stations, NDBC observations, ERDDAP assets, gridded ocean products, satellite products, Argo products, bathymetry, model products, and regional ocean or atmosphere maps.

## MCP Endpoint And Tools

The AquaView MCP endpoint is `https://mcp.aquaview.org/mcp`.

First try IDEA's built-in MCP helpers: `list_mcp_tools()` and `call_mcp_tool(...)`. If the registry is empty or tool IDs fail, connect directly to the AquaView endpoint with MCP JSON-RPC: `initialize`, `notifications/initialized`, `tools/list`, then `tools/call`.

Use AquaView tools in this order:

- `list_collections` with `output_format="json"` to identify source collections.
- `search_datasets` with `q`, `bbox`, `datetime`, `collections`, `include_assets=false`, `limit`, and `output_format="json"` to search.
- `get_item` with `collection`, `item_id`, and `output_format="json"` to inspect full metadata and assets.
- `aggregate` to summarize coverage or counts before fetching detailed records, when supported.

## Search Rules

- Prefer explicit bounding boxes over vague region names: `west,south,east,north`.
- Search all collections only for discovery, then rerun targeted searches in likely collections.
- Use `include_assets=false` for broad searches, then inspect selected items with `get_item`.
- Check title, variables, source URL, spatial coverage, temporal coverage, and latest update before using a dataset.
- Verify underlying asset metadata when possible, especially for dates, units, variable names, and latency.
- For gridded maps, consider collections such as `COASTWATCH`, `COASTWATCH_CWCGOM`, `HYCOM`, `RTOFS_3D`, `CMEMS_GLORYS`, and regional IOOS ERDDAP collections.
- For station or buoy data, consider `NDBC`, `IOOS`, regional IOOS collections, and `COOPS` when appropriate.

## Dataset Selection

Rank candidates by scientific fit, not just text match:

- Product class: satellite analysis, numerical model analysis, forecast, reanalysis, in situ time series, or trajectory/profile.
- Variable match, preferably exact variable or CF standard name.
- Spatial overlap, resolution, and whether the data are gridded, point, profile, or trajectory.
- Temporal coverage, latest valid data time, and latency. Do not treat catalog update time as the data time.
- Asset quality: prefer OPeNDAP, ERDDAP griddap/tabledap, NetCDF, or Zarr assets over HTML landing pages for data access.

Do not overclaim. Say "discovered via AquaView MCP" and attribute the underlying provider. Do not conflate model fields, forecasts, reanalyses, satellite analyses, and in situ observations.

## Asset And Time Handling

- OPeNDAP: open with xarray, inspect variables/coordinates first, subset before `.load()`.
- ERDDAP griddap: use constrained URLs or xarray; ERDDAP tabledap: request only needed variables, time range, and bounding box.
- NetCDF: download only when remote subsetting is unavailable. Zarr: prefer cloud-native access when available. HTML pages are metadata, not primary data.
- Inspect coordinate names before assuming `lat`, `lon`, `time`, or `depth`; handle `0..360` vs `-180..180` longitude conventions.
- For surface fields, select the shallowest depth only when scientifically appropriate and label that choice.
- Check time units, calendar, coordinate time, model run initialization time, forecast valid time, and catalog updated time. If time decoding fails, reopen with `decode_times=False` and decode manually when possible.
- For recent products, use the latest valid time not later than current UTC unless the user asks for a forecast. If only future times exist, label the result as a forecast.

## Common Workflows

- Gridded SST/anomaly maps: search products such as MUR, OISST, GHRSST, blended SST, CoastWatch, or PolarWatch; subset spatially and temporally; label product, dates, baseline, units, and source.
- Station or buoy observations: search likely station collections such as NDBC, COOPS, or IOOS sensors; inspect station variables and source assets; do not mix station and gridded products without explaining the observing-system difference.
- ERDDAP-backed assets: inspect `.das` and `.dds`; subset time/lat/lon before downloading; confirm whether temperature is in degrees C or kelvin.

## Troubleshooting

- Empty `list_mcp_tools()` registry: use direct MCP JSON-RPC.
- Search results too broad: add `collections`, `bbox`, `datetime`, and exact variables.
- Search result lacks assets: call `get_item`.
- Huge remote dataset: subset before loading.
- Longitude mismatch: convert coordinates before subsetting.
- Forecast returned unintentionally: choose the latest valid time not later than now, or label as forecast.

## Output Standard

Save generated data, figures, and notes under IDEA's standard output directory. Report the AquaView collection and item ID, dataset title, underlying provider/source, asset key and URL type, variable name and units, product class, selected time and meaning, spatial bounds, resolution when available, transformations applied, substituted datasets or dates, and caveats.
