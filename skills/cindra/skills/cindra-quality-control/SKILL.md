---
name: cindra-quality-control
description: Use for CIndRA UHSLC tide-gauge QC, station suitability, data completeness, missing-data handling, Level 2 screening, daily/monthly/calendar-year/storm-year checks, and QC outputs. Trigger on QC, data completeness, missing years, station suitability, Level 2 rule, or whether a station can be used.
---

# CIndRA Quality Control

## Purpose

Evaluate whether UHSLC tide-gauge data are suitable for CIndRA sea-level indicators under the Level 2 prototype completeness rule. This skill controls station suitability, missing-data rules, datum/QC blocking conditions, and QC terminology.

## Required Inputs

- UHSLC station ID and metadata.
- Tide-gauge time series with timestamps, sea level, units, flags where available, and datum/reference metadata.
- Intended method: trend, anomaly, flood frequency, rankings, or report synthesis.
- Intended aggregation: hourly, daily mean, daily maximum, monthly, annual, or storm year.
- Intended period; trend default is `1993-2025` unless reproducing validation.

## Daily Rule

For hourly-method products, a daily value is usable only if at least one valid observation exists in each six-hour UTC interval:

- `00:00-05:59`
- `06:00-11:59`
- `12:00-17:59`
- `18:00-23:59`

For workflows that start from daily products, this test is `not_applicable_daily_input` unless subdaily data exist.

## Monthly Rule

A month passes if usable-day coverage is at least `75%` and there are no more than seven consecutive unusable days. Failed days and flagged months must be recorded, not silently discarded.

## Storm-Year Rule

Storm years run May 1-April 30 and are labeled by starting year. Storm-year completeness is computed over the full storm-year denominator. A storm year passes if usable-day coverage is at least `75%`. Month-level failures are diagnostic flags, not automatic exclusions, unless a downstream method explicitly requires month exclusion.

## Calendar-Year Rule

Calendar years pass if usable daily coverage across the full calendar year is at least `75%`, subject to indicator-specific requirements.

## Station-Level Level 2 Gate

For a requested multi-year timeframe, a station passes Level 2 only if both conditions are met:

1. At least `20` calendar years pass annual Level 2.
2. No more than `5` calendar years are missing.

A missing year has zero usable daily values. A partial year below `75%` coverage is a failing annual Level 2 year, not a missing year unless it has zero usable values.

Station-level Level 2 is a station/timeframe gate, not a year-by-year exclusion rule. If the station passes the gate, retain all available downstream data within the timeframe, carrying period-level QC status as diagnostics. True no-data periods remain missing/empty/`NaN` and must not be converted to valid zero-count periods.

## Daily Timestamp Convention

Use daily timestamps at `12:00 UTC` as the canonical daily convention unless the source documents otherwise. Preserve original decoded timestamps in provenance and document any offset normalization.

## Output Requirements

QC outputs should include station ID/name/country, period, method ID, temporal resolution, datum/reference, year or storm year, completeness fractions, maximum consecutive missing days, six-hour interval status, flags, QC status, QC reason, station-level Level 2 fields, missing years, failing non-missing years, and pass/fail decision.

## Validation

Do not mark a station suitable without listing specific passing years/months and Level 2 status. If a station fails, recommend alternatives such as a narrower period, another station, or an explicitly labeled exploratory sensitivity product.
