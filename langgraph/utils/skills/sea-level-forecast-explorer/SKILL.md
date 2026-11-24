---
name: sea-level-forecast-explorer
description: Explore and visualize UHSLC monthly sea-level forecasts and daily high-water and low-water outlooks. Use for forecast data retrieval, anomaly and skill analysis, uncertainty and threshold probabilities, or forecast maps and time series.
---

# Sea Level Forecast Explorer

### Scope & Workflow
- Use this skill for UHSLC forecast exploration, analysis, and graphics. Support scientific judgment, not autonomous warnings. IDEA's shared instructions take precedence; use only available tools.
- For advanced tasks, briefly plan, then act. Clarify consequential ambiguities; disclose defaults. Use `run_python_tool` for analysis/plots, reuse live data/completed checks, and bound retrieval to the request.
- Workflow: inspect catalog → select verified targets/files → apply relevant monthly/daily rules below → analyze/plot → validate → deliver results/provenance. Consult daily schema only when needed; do not require daily analysis for monthly-only tasks.
- Ordinary exploration needs no approval gates. Formal discussions follow the linked discussion skill's evidence, layout, and preliminary/review-ready pauses; never label them final without authorization.

### Sources & Defaults
- Product root: https://uhslc.soest.hawaii.edu/research/monthly_sea_level_forecast/
- Catalog: https://uhslc.soest.hawaii.edu/research/monthly_sea_level_forecast/data/catalog.json
- Schema/probability reference: https://uhslc.soest.hawaii.edu/research/monthly_sea_level_forecast/discussion/skills/monthly-sea-level-forecast-discussion/references/data-products.md
- Discussion skill: https://uhslc.soest.hawaii.edu/research/monthly_sea_level_forecast/discussion/skills/monthly-sea-level-forecast-discussion/SKILL.md
- Inspect catalog for new tasks: `cycle`, `target_data_cycle`, `created_utc`, `leads`, `valid_times`, models/targets, stations, grid/masks, paths, notes. Reuse on follow-ups unless scope/currency changes. Distinguish initialization, observation cutoff, valid months; NetCDF mid-month timestamps denote monthly periods, not release dates.
- Defaults: latest published cycle; Top-5 MME (`ensemble_top5_ACC_withDP`); all available monthly station leads; local-calendar-day/combined uncertainty for daily guidance. Disclose unavailable defaults; never silently substitute. Unspecified monthly overviews may compare observations with leads 3 and 6.
- Resolve `data/...`/`downloads/...` against product root; `layers/...`, `tide_gauge_points/...`, `altimetry_rows/...` against product root + `data/`. Avoid `data/data/`.
- Published paths, availability permitting:
  - Observations: `data/layers/observed_altimetry_m+0.json`.
  - Forecasts: `data/layers/altimetry_<model_id>_lead<lead>.json`.
  - Station/monthly grid series: `data/tide_gauge_points/point_NNN.json`; `data/altimetry_rows/row_NNN.json`.
  - High waters: station's `tide_flooding_json`; low waters: `data/low_tide_exposure/point_NNN.json`.
  - High-water verification: `data/daily_max_skill/point_NNN.json`; NetCDF/CSV: advertised downloads.
- Verify accessibility; manifests do not guarantee archives. Report missing sources/conflicts; do not propagate stale cycle-specific caveats.

### Identity & Alignment
- Use `get_station_info_tool` for UHSLC station lookups/regional surveys, then match catalog; preserve ID leading zeros. **`point` is a zero-based catalog index, not `station_id` or `station_file_id`.** Never construct paths from remembered IDs.
- Regional surveys use full catalog and explicit geographic criteria; distinguish samples from exhaustive coverage and report unsupported products.
- Read coordinates/dimensions: layers use `values[ilat][ilon]`; row series use `ilat` and longitude indices. Handle longitude/dateline; align coordinates before comparisons. Apply ocean masks; distinguish land, nulls, observation gaps, reliability masks. Null is not zero.
- Location series use one documented ocean cell across periods; report coordinates/distance/missingness, not silent relocation. Open-ocean cells do not resolve harbors.
- Align observations by `observed_labels`/`observed_offsets`, forecasts by leads/valid months. Inspect `observed_sources`; altimetry proxies are not measured gauge observations.

### Monthly Anomalies & Skill
- Verify units, climatology, detrending, inverse-barometer treatment. Previously inspected convention: meters of linearly detrended, IB-inclusive SSH anomaly; 1993–2016 monthly climatology removed, slope/intercept fitted over 1993–2025. Verify periods; do not hardcode them.
- Separate detrended anomalies, trend-adjusted displays, daily datum-relative levels. Never infer flooding from anomalies or add trend twice. Convert to cm if useful; retain internal precision.
- Top-5 membership varies by location/lead through retrospective ACC. Verify target-specific members/availability. Prefer supplied `p16`/`p84`, `p025`/`p975` (68%/95% predictive intervals); Gaussian-mixture bounds may be asymmetric, not confidence intervals of a mean.
- Verify skill target, period, baseline, definition. Some ensemble ACC displays are selected-member averages, not independent ensemble verification. Inspected RMSESS: `1 − RMSE/RMSE_reference`, not MSE-based. Correlation proves neither calibration nor hazard skill.

### Daily References & Gotchas
- Read schema/file metadata before calculations. High: `daily_max_m`/`observed_daily_max_m`, above `percentile_98`. Low: `daily_min_m`/`observed_daily_min_m`, below `percentile_02`; retain raw `exposure_probability_percent` in provenance.
- State units/reference/epoch/proxy status; inspected references are empirical 2002–2020 MHHW/MLLW proxies from observed daily extrema, not automatically NOAA datums. Read thresholds from selected block.
- `noaa_minor` applies only to high waters with finite value, verified station mapping, datum conversion, applicability; never use low-tide `noaa_minor` as an exposure threshold.
- Supplied totals include astronomical tide, monthly anomaly, documented trend add-back; inspect fallback metadata, never add components again. Cross-check extreme/event/reference/array names: low-tide descriptions may inherit maximum/exceedance wording.
- Keep `local_time` dates/products/arrays/thresholds/reference/method together; never mix with root UTC-day fields. Identify IANA/DST-aware, fixed-offset, or UTC timing accurately.

### Uncertainty & Probabilities
- Select model/day basis/threshold/mode together. Total monthly bands use `p16_m`, `p84_m`, `p025_m`, `p975_m`; `combined_*` is distinct. Monthly-component bands are not total uncertainty. High-frequency mode has separate center/bounds/threshold-keyed probabilities; verify recentering, never double-add its center.
- Probabilities are percentages, specific to threshold/mode. Exact mixture quantiles do not establish exact tail probabilities.
- For derived dashboard-equivalent monthly/combined probabilities, verify frontend methodology: documented normal approximation uses sigma = half the 68% width, fallback 95% width/3.92. Upper tail for maxima, lower for minima; label approximation, handle zero-width crossing conventions, never invent missing uncertainty.
- Optional threshold categories: unlikely <5%, possible 5%–<50%, likely ≥50%; not impact categories. Restrict outlooks to forecast periods. Summed daily probabilities estimate expected crossing-days, not independent events or window-event probability; the latter requires dependence information.
- Hourly centers support timing only when supplied; water-level bands do not quantify timing uncertainty. High-water verification does not validate low waters. Crossings do not establish inundation, exposed area, navigation depth, or impacts; acknowledge unresolved storms/waves/coastal processes/bathymetry and defer urgent safety decisions to official authorities.

### Graphics, Validation & Delivery
- Prefer matplotlib/seaborn; folium for suitable interactive maps; applicable design skills for dashboards/posters/LaTeX. Maps need aligned coordinates/references, comparable extent/units/diverging scales, distinct missingness. Series separate observations/proxies/forecasts/tide-only guidance and mark forecast transitions.
- Label units, cycle/valid period, model/target, UHSLC source, reference, uncertainty. Daily graphics also specify extreme, datum/epoch, day basis, threshold, mode, probability method; skill graphics specify metric/period/baseline/provenance. Attribute uploads/supplemental sources accurately; visually inspect plots and avoid color-only distinctions.
- Validate retrieval, dates, station identity, shapes/coordinates/masks, finite values, units/reference, ordered intervals, 0–100% probabilities, observation/forecast separation. Cross-check representations when useful; claim only performed checks.
- Record source URLs, selections, transformations, methods, caveats, checks; checkpoint substantial work and revise only affected evidence. Use `get_climate_indices_tool` for supported climate indices; correlation does not establish local causation.
- `/workspace`: private working files; `/outputs`: deliverables; `/app/data`: read-only. Publish workspace artifacts with `publish_artifact_tool`; link exact `sandbox:/outputs/...` paths. HTML must be self-contained. Deliver results, interpretation, limitations, downloads; extracts/scripts when useful/requested. Artifact creation authorizes no installation, deployment, archive update, or messaging.
