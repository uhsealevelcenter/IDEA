# CIndRA Skill Library

CIndRA (Climate Indicator Report Assistant) is under development. Runtime
instructions begin in `SKILL.md`; `manifest.yaml` lists the shared references
and modular skills.

## TODO

The following upstream dependencies are intentionally deferred and must be
resolved before the affected workflows are promoted beyond draft or
experimental use.

### Regional definition instructions

Expected source-of-truth repository path:

`regional_definitions/CIndRA_PICCM_Regional_Definition_Instructions_v03.md`

This file is not present in the IDEA repository, elsewhere under the current
local workspace, or at the expected path on the default branch of:

`https://github.com/lauracagigal/PICCM_SeaLevel`

TODO: locate the reviewed source document and add it to the source-of-truth
repository at the expected path, or update the CIndRA regional-definition skill
with its authoritative location.

### Plotting helpers

The following approved helper names referenced by the CIndRA trend and
flood-frequency skills are not currently implemented in
`functions/sea_level_plotting.py` on the default branch of
`PICCM_SeaLevel`:

- `plot_combined_altimetry_tide_gauge_trend_map`
- `plot_national_eez_combined_trend_map`
- `plot_regional_altimetry_trend_map_filled_tide_gauges`
- `plot_regional_flood_frequency_overview`

TODO: implement and review these helpers upstream, or revise the affected
CIndRA skills to use approved existing helpers.
