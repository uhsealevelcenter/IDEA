# CIndRA Conventions

## Controlled Parameters

Track `site_config_path`, `product_profile`, analysis period, validation period, tidal epoch or baseline, method ID, source versions, QC reference, report path, and repository reference where relevant.

## Time and Units

- Default trend epoch: `1993-2025`.
- Palau/Malakal validation epoch: `1993-2022` only when reproducing that case.
- Use the latest complete endpoint as a clearly labeled provisional fallback when complete 2025 data are unavailable.
- Use daily timestamps at `12:00 UTC` unless the source documents otherwise.
- Report trend rates in `mm/yr`.
- Report epoch change in `cm` or `cm/epoch`.
- Order storm-year months from May through April.

## Scientific and Product Rules

- Keep relative tide-gauge and absolute altimetry quantities distinct.
- Do not average tide-gauge stations without a documented aggregation rule.
- Compute minor flood frequency from daily maxima at `30 cm above MHHW`.
- Preserve true no-data periods as missing, empty, or `NaN`, never as zero.
- Generate final figures with approved helpers in `functions/sea_level_plotting.py`; do not use ad hoc inline plotting.
- Treat machine-readable inventory, parameter, source-version, method-version, provenance, validation, and issue-log files as the source of record.
- Treat PDF, DOCX, Markdown, notebook, and code-bundle outputs as review or reproducibility assemblies.
