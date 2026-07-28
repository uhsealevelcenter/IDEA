# CIndRA Validation Rules

## Tide-Gauge Completeness

- For hourly products, a usable day needs at least one valid observation in each UTC interval: `00:00-05:59`, `06:00-11:59`, `12:00-17:59`, and `18:00-23:59`.
- A month passes with at least `75%` usable-day coverage and no more than seven consecutive unusable days.
- A calendar or storm year passes with at least `75%` usable-day coverage over its full denominator, subject to method requirements.
- For a requested multi-year period, a station passes Level 2 only when at least 20 calendar years pass and no more than five calendar years are entirely missing.
- A partial year below 75% is failing, not missing. If a station passes the gate, retain available downstream data and carry period-level failures as diagnostics.

## Product Validation

Confirm the requested profile and spatial scope, station identity, source versions, datum compatibility, analysis period, units, QC reference, approved calculation and plotting helpers, provenance, and clear absolute-versus-relative labels.

For flood products, confirm MHHW, daily maxima, `30 cm above MHHW`, complete May-April storm-year handling, and correct count-versus-percent labels.

## Review Status

Use only:

- `Experimental`
- `Draft`
- `Scientist-reviewed`
- `Approved for report use`
- `Deferred`
- `Optional`

Do not use `Approved for report use` without human scientific review and complete validation and provenance records. A code repository bundle, annotated notebook, or product-to-code crosswalk is required only when explicitly requested, contractually required, or requested during review.
