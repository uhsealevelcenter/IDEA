# CIndRA Data Sources

## Implementation

Use `https://github.com/lauracagigal/PICCM_SeaLevel` as the implementation source of truth:

- Historical notebooks: `notebooks/historical`
- Core methods: `functions/sea_level.py`
- Downloaders: `functions/data_downloaders.py`
- Plotting helpers: `functions/sea_level_plotting.py`

## Tide Gauges

Use UHSLC for station metadata, relative sea-level trends, and minor flood frequency. Prefer Fast Delivery or hybrid coverage. Otherwise use only the latest Research Quality version; do not combine Research Quality versions.

NOAA CO-OPS may provide U.S.-affiliated station context, datum or threshold comparison, or explicitly requested NOAA API data. It must not replace UHSLC without approval.

## Altimetry

Use the project-provided CMEMS/Copernicus monthly `0.25 degree` gridded altimetry NetCDF:

`https://uhslc.soest.hawaii.edu/mwidlans/dev/SEA/SEAdata/cmems_altimetry.nc`

Do not substitute another altimetry product unless explicitly requested and labeled as a method update or exploratory sensitivity test.

## Boundaries

For `National/EEZ` products, use this hierarchy:

1. Project-approved boundary from site setup.
2. Approved Pacific/SPREP or Pacific Data Hub boundary.
3. Marine Regions / VLIZ EEZ boundary.
4. Documented EEZ-scale bounding box for `Draft / Experimental` diagnostics only.

Record dataset, provider, URL or path, version, access date, station or source IDs, variables, resolution, units, datum or reference, and source status.
