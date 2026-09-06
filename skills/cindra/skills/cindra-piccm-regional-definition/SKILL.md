---
name: cindra-piccm-regional-definition
description: Use when defining or auditing the CIndRA PICCM/Pacific Islands regional station domain, station-mask inclusion/exclusion, regional inventory provenance, or regional limitation language. Do not use as a polygonal EEZ mask for gridded altimetry extraction.
---

# CIndRA PICCM/Pacific Islands Regional Definition

## Purpose

Use this skill for PICCM/Pacific Islands regional station selection and provenance. The regional definition is a station-level UHSLC mask, not a final polygonal EEZ boundary for gridded altimetry extraction, clipping, or area-weighted statistics.

## Canonical Labels

- Region definition name: `CIndRA PICCM Pacific-island regional station mask`
- Region definition version: `v0.2`
- Regional instruction document version: `v0.3`
- Recommended station inventory version: `v03`
- Status: `Draft / Experimental`
- Canonical path: `regional_definitions/CIndRA_PICCM_Regional_Definition_Instructions_v03.md`

## Source Boundary

Starting boundary is based on the Pacific Islands Climate Change Monitor Report 2021 sea-level trend map, visually interpreted as:

- West: `125°E`
- East: `115°W` / `245°E`
- South: `35°S`
- North: `35°N`

In UHSLC ERDDAP `0-360` longitude convention:

- `125.0 <= longitude_degrees_east <= 245.0`
- `-35.0 <= latitude_degrees_north <= 35.0`

## Inclusion Criteria

Include UHSLC stations satisfying at least one documented inclusion criterion:

- Pacific island countries and territories inside the base filter;
- Hawaiʻi and U.S.-administered Pacific island stations;
- Lahaina, Maui as a manual Hawaiʻi override where needed;
- Easter Island / Rapa Nui, UHSLC ID `22`, as a manual regional extension.

## Exclusion Criteria

Exclude stations in the bounding box that are not intended Pacific-island regional products, unless a later approved definition states otherwise:

- Australia;
- Indonesia;
- Japan;
- Mexico;
- Philippines;
- U.S. West Coast / California;
- mainland or large-continental-margin stations.

## Inventory Coverage Rule

Use the global UHSLC coverage source rule: Fast Delivery / hybrid first; otherwise latest Research Quality version only. Do not combine RQDS versions.

## Required Provenance

Preserve region definition name/version/status, base bounding box, source map, manual additions/overrides, UHSLC station source, datasets checked, longitude convention, temporal coverage method, selected coverage source, access date, limitations, and inclusion/exclusion rationale.

## Limitations

This v0.2 mask is not a final geospatial product boundary. It was derived from a report-map image and manual station metadata rules. Validate with project scientists before promotion beyond `Draft / Experimental`.
