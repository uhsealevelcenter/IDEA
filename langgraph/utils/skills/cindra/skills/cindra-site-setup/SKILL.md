---
name: cindra-site-setup
description: Use when configuring or validating a CIndRA sea-level site, selecting UHSLC stations, resolving EEZ or regional spatial settings, preparing station inventories, or creating the site JSON used by downstream CIndRA skills.
---

# CIndRA Site Setup

## Purpose

Create or validate the shared CIndRA site configuration used by downstream sea-level indicator workflows. Site setup resolves active product profile, selected UHSLC station(s), station metadata, spatial scope, EEZ/regional boundary metadata, analysis date bounds, CMEMS extraction settings, and provenance paths.

Site setup may record preliminary coverage and datum availability, but station suitability decisions are controlled by `cindra-quality-control`.

## Source of Truth

- Notebook: `notebooks/historical/0_site_setup.ipynb`
- Core module: `functions/sea_level.py`
- Downloaders: `functions/data_downloaders.py`
- Pilot configuration: Palau, Malakal tide gauge, UHSLC ID `7`

## Site Configuration Fields

Include fields where relevant:

- `site_name`, `site_lon`, `site_lat`
- `product_profile`
- `site_config_path`
- `start_date`, `end_date`
- `selected_uhslc_id`, `selected_station_name`, `station_country_filter`
- `site_eez_shapefile` or `site_boundary_path`
- `site_boundary_name`, `site_boundary_source`, `site_boundary_source_url`, `site_boundary_version_or_date`
- `site_boundary_geometry_type`, `site_boundary_bbox`, `site_boundary_use`
- `site_boundary_fallback_used`, `site_boundary_fallback_rationale`
- `cmems_bbox`
- `regional_mask_id`
- `spatial_scope_description`
- `station_ids`, `included_station_list`, `excluded_station_list`
- `coverage_source_selected`, `coverage_source_status`, `coverage_source_rule`
- `rqds_versions_available`, `rqds_version_used`
- `datum_availability`, `mhhw_availability`, `qc_status_reference`

## Workflow

1. Load requested site metadata and product profile.
2. Use repository site-preparation helpers where available.
3. Verify station identity using authoritative UHSLC metadata. If station ID/name is uncertain, use IDEA station lookup before proceeding.
4. For PICCM/Pacific Islands regional products, use the CIndRA regional station mask skill.
5. Record preliminary coverage, datum, and MHHW availability.
6. Save or update the site configuration.
7. Confirm downstream paths, station metadata, date ranges, boundary metadata, and CMEMS bounds are populated.

## Validation

Confirm station identity, active product profile, spatial scope, target trend period `1993-2025`, and boundary availability. For Palau validation, confirm Malakal, Palau, UHSLC ID `7`. Regional station-mask inclusion is not QC approval.
