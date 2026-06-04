---
name: co-ops-api
description: Use NOAA CO-OPS APIs for tide gauge stations outside the UHSLC network, or when the user explicitly requests NOAA CO-OPS API data. Do not use CO-OPS as the default source for UHSLC-network tide gauge data.
---

# CO-OPS API

Use this skill only for NOAA CO-OPS data requests, or for tide gauge stations that are not in the UHSLC network. For UHSLC-network stations, prefer IDEA's UHSLC data tools unless the user specifically asks for NOAA CO-OPS.

## APIs

- Data Retrieval API: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter`
- Data docs: `https://api.tidesandcurrents.noaa.gov/api/prod/`
- Metadata API: `https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/`

Use the Data Retrieval API for observations and predictions. Use the Metadata API for station metadata and official datums; do not use an old Data API `datums` product for official datum tables.

## Workflow

- Confirm the NOAA station ID, name, coordinates, product, units, time zone, and datum before analysis.
- Use `format=json`, `application=IDEA`, `units=metric`, and `time_zone=gmt` unless the user asks otherwise.
- Include `datum=` for water-level products such as `water_level`, `hourly_height`, `high_low`, `predictions`, `daily_maximum`, `daily_minimum`, and `monthly_mean`.
- Retrieve official datums from `/stations/<station_id>/datums.json?units=metric` before datum-relative plots or conversions.
- Chunk long requests: yearly chunks for `hourly_height`, monthly or shorter chunks for 6-minute `water_level`, and shorter chunks for 1-minute data.
- Preserve NOAA `error` responses and adjust the request rather than hiding failures.

## Common Products

- `hourly_height`: verified hourly water levels, good for long historical records.
- `water_level`: 6-minute water levels, good for events and detailed timing.
- `predictions`: tide predictions, not observations.
- `high_low`: verified tidal extrema.
- `monthly_mean`: compact long-term mean water-level summaries.
- Meteorology: `wind`, `air_temperature`, `water_temperature`, `air_pressure`, `humidity`, `salinity`, and related products.

## Output Standard

For generated analyses, save CSVs, figures, and notes under IDEA's standard output directory. State the station name/id, product, datum, units, time zone, coverage period, endpoint family used, and any gaps or caveats.
