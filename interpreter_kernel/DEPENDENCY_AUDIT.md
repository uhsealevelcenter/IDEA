# Microsandbox dependency audit

Last reviewed: 2026-08-10

## Scope and result

The `research` lock resolves 252 exact Python distributions for Python 3.12.
The locally built image passes `pip check` in both `/opt/idea-venv` and the
separate `/opt/guarddog-venv`. An installed-environment `pip-audit` scan found
no advisories in the GuardDog environment and reported two records in the
research environment:

- `click==8.2.1` / `PYSEC-2026-2132`: confirmed. Click's `edit()` helper is
  vulnerable before 8.3.3. `copernicusmarine==2.3.0` currently declares
  `click<8.3.0`, so the fixed Click release cannot be selected while retaining
  a consistent, supported Copernicus Marine installation. Exploitation requires
  an attacker-controlled editor value and a call to `click.edit()`; IDEA
  already gives that same user code execution inside their own dedicated
  microVM, which substantially limits additional impact. Do not call
  `click.edit()` on untrusted editor values. Upgrade as soon as Copernicus
  Marine relaxes its constraint.
- `intake==2.0.9` / `CVE-2026-33310`: scanner false positive. The advisory and
  OSV record both state that versions *before* 2.0.9 are affected and that
  2.0.9 contains the fix (`getshell=False` by default). Retain 2.0.9 or newer
  and never opt into shell expansion for untrusted catalogs.

The older uncoordinated legacy dependency set produced 176 audit findings in
32 packages and also broke the Open Terminal service dependency constraints.
It remains in `modules/original/` for historical comparison only.

## Recheck procedure

After changing `requirements.in` or either pinned base image:

1. Regenerate `requirements.lock` as documented in `README.md`.
2. Run `./interpreter_kernel/test_image.sh idea/oi-kernel:research-local`.
3. In a disposable container, install `pip-audit` into `/tmp` and audit
   `/opt/idea-venv/lib/python3.12/site-packages` and
   `/opt/guarddog-venv/lib/python3.12/site-packages` with `--path`.
4. Review Debian and base-image advisories for the two pinned OCI digests.
5. Record any accepted finding here with its actual exposure, compensating
   controls, owner, and removal condition. Never suppress a finding only to
   make CI green.

References: [Click advisory](https://osv.dev/vulnerability/PYSEC-2026-2132),
[Intake advisory](https://osv.dev/vulnerability/CVE-2026-33310).
