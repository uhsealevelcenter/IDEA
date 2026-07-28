---
name: cindra-sea-level-governance
description: Use for global CIndRA sea-level MVP rules: scope, source hierarchy, controlled parameters, product profiles, provenance, approved figure policy, review labels, and unsupported-request handling. Use before domain workflow skills when a request needs routing or policy interpretation.
---

# CIndRA Sea-Level Governance

## Purpose

Apply this skill for global rules that govern all CIndRA Phase 1 sea-level products. This skill controls scope, product profiles, data-source hierarchy, provenance, figure policy, and review status. Domain workflow skills must not override these rules.

## Product Profiles

Active sea-level product profiles are:

- `Regional`
- `National/EEZ`

Both profiles share scientific calculations, data-source hierarchy, QC rules, method IDs, provenance schema, and validation categories. Profiles differ only in spatial scope, aggregation/report layout, table granularity, required report-facing inventory, captions, limitations, and validation checks.

## Approved Data Sources

### Tide gauges

Use **UHSLC** as the working authoritative source for station metadata, station-relative trends, and minor flood frequency. NOAA CO-OPS may be used for U.S.-affiliated station context, datum/threshold comparison, or explicit NOAA API requests, but must not replace UHSLC unless approved.

### Altimetry

Use the project-provided CMEMS/Copernicus monthly `0.25 degree` gridded altimetry NetCDF as the working authoritative source for CIndRA altimetry products:

- URL: `https://uhslc.soest.hawaii.edu/mwidlans/dev/SEA/SEAdata/cmems_altimetry.nc`
- Format: NetCDF
- Cadence: monthly
- Resolution: `0.25 degree`

Do not substitute another altimetry product unless explicitly requested and labeled as a method update or exploratory sensitivity test.

### Boundaries

For `National/EEZ` products, use project-approved national or EEZ boundaries where available. Preferred hierarchy:

1. Project-approved boundary from site setup.
2. Approved Pacific/SPREP or Pacific Data Hub boundary.
3. Marine Regions / VLIZ EEZ boundary.
4. Documented EEZ-scale bounding box for `Draft / Experimental` diagnostics only.

Record boundary name, source, URL/path, version/date, geometry type, bounding box, use, fallback rationale, and limitations.

## UHSLC Coverage Source Rule

For station inventories, use this hierarchy:

1. UHSLC Fast Delivery / hybrid coverage where available.
2. If unavailable, UHSLC Research Quality data.
3. For Research Quality, inspect versions and use only the most recent version.
4. Do not combine Research Quality versions.
5. Record available versions, selected version, and coverage status in provenance.

## Provenance Requirements

For every output table, metric, figure, map, or narrative claim, preserve or report dataset name, provider, access path/repository, version, access date, DOI/citation, station/source ID, variable names, temporal resolution, units, datum/reference, spatial extraction method, source priority, and source status.

Machine-readable backbone files are the source of record, including product inventory, parameters, source versions, method versions, provenance, validation checklist, and issue log. PDF/DOCX/Markdown report files are assemblies only.

## Scientific Distinctions

Keep tide-gauge and altimetry products distinct:

- Tide gauges measure **relative sea level** at a station.
- Satellite altimetry measures **absolute sea level** in a geocentric frame.
- Combined displays may show both but must not merge them into a single metric unless an approved method exists.

Do not average tide-gauge stations without a documented aggregation rule.

## Figure Policy

Final CIndRA figures must be produced by approved code in `PICCM_SeaLevel`, especially helpers in `functions/sea_level_plotting.py`, or by a new helper first added to that module. Do not create final CIndRA figures using ad hoc inline plotting code. If a requested figure is unsupported, propose a new plotting helper with name, inputs, outputs, and method purpose.

## Review Status

Use these labels only:

- `Experimental`
- `Draft`
- `Scientist-reviewed`
- `Approved for report use`
- `Deferred`
- `Optional`

Do not mark products `Approved for report use` without human scientific review and complete validation/provenance records.

## Code Repository Furnishing Policy

Do not furnish a `Code repository/` bundle by default. Preserve code/repository reference, method IDs, source files, run record, and provenance by default. Furnish annotated notebooks, product-code crosswalks, and code bundle contents only when explicitly requested or contractually required. If not requested, record code bundle status as `not_furnished_unless_requested`, not as missing or failed.
