import os as _os

_HPC_MODULES = _os.getenv("HPC_MODULES", "").strip()
_HPC_CONDA_ENV = _os.getenv("HPC_CONDA_ENV", "").strip()

_env_block = ""
if _HPC_MODULES:
    _env_block += f"#   Modules loaded:    {_HPC_MODULES}\n"
if _HPC_CONDA_ENV:
    _env_block += f"#   Conda environment: {_HPC_CONDA_ENV} (activated via `conda run`)\n"
if not _env_block:
    _env_block = "#   No modules or conda env configured — system Python will be used.\n"

hpc_tool_string = f"""
# HPC Cluster Tools
# These functions are injected into the interpreter sandbox.
# They delegate to backend.hpc_manager which handles the actual SSH/SLURM logic.
from backend.hpc_manager import (
    check_hpc_connection,
    submit_hpc_job,
    poll_hpc_job,
    get_hpc_job_output,
    cancel_hpc_job,
    list_hpc_jobs,
)

# =============================================================================
# IMPORTANT — Writing scripts for submit_hpc_job()
# =============================================================================
# The SLURM job wrapper automatically sets up the Python environment BEFORE
# running user_script.py. The pre-configured environment is:
{_env_block}#
# Rules for scripts passed to submit_hpc_job():
#   1. DO NOT use pip install, conda install, or any package manager commands.
#      All required scientific packages (numpy, xarray, pandas, scipy, netCDF4,
#      matplotlib, etc.) are already available in the pre-configured environment.
#   2. Write pure Python — no %%magic, no shell commands, no Jupyter syntax.
#   3. Save results to files (CSV, NetCDF, JSON, PNG) in the current directory.
#      The job runs in a unique scratch directory; use relative paths for output.
#   4. If a package is genuinely missing, report that to the user and ask them
#      to add it to the conda environment — do NOT attempt to install it inline.
# =============================================================================
"""
