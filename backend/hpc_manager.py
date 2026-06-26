import logging
import os
import socket
import threading
import uuid

import paramiko

from backend.state import (
    HPC_ENABLED,
    HPC_HOST,
    HPC_USER,
    HPC_SSH_KEY_PATH,
    HPC_SSH_PORT,
    HPC_SCRATCH_DIR,
    HPC_DEFAULT_PARTITION,
    HPC_DEFAULT_ACCOUNT,
    HPC_DEFAULT_WALLTIME,
    HPC_DEFAULT_MEMORY,
    HPC_CONDA_ENV,
    HPC_MODULES,
)

logger = logging.getLogger(__name__)

_ssh_client: paramiko.SSHClient | None = None
_ssh_lock = threading.Lock()

_NOT_CONFIGURED = {
    "status": "error",
    "message": (
        "HPC not configured. Set HPC_HOST, HPC_USER, and HPC_SSH_KEY_PATH "
        "environment variables and restart the service."
    ),
}


def _load_private_key(path: str) -> paramiko.PKey:
    """Try common key types in order; return the first one that loads."""
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(path)
        except (paramiko.SSHException, ValueError):
            continue
    raise paramiko.SSHException(f"Cannot load private key from {path}")


def _duo_handler(title: str, instructions: str, prompt_list: list) -> list:
    """
    Keyboard-interactive handler that auto-selects Duo Push (option 1).
    The Duo Push is sent to the configured device; the user approves on their phone.
    """
    responses = []
    for prompt, _echo in prompt_list:
        p = prompt.strip().lower()
        if "passcode or option" in p or "enter a passcode" in p:
            responses.append("1")  # Select Duo Push
        else:
            responses.append("")
    return responses


def _get_ssh_client() -> paramiko.SSHClient:
    """Return a cached SSH client, creating one (and triggering Duo once) if needed."""
    global _ssh_client
    with _ssh_lock:
        if _ssh_client is not None:
            transport = _ssh_client.get_transport()
            if transport is not None and transport.is_active():
                return _ssh_client
            try:
                _ssh_client.close()
            except Exception:
                pass
            _ssh_client = None

        sock = socket.create_connection((HPC_HOST, HPC_SSH_PORT), timeout=30)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = 30
        transport.auth_timeout = 90  # Allow time for Duo Push approval on phone
        transport.start_client(timeout=30)

        # Factor 1: public key auth
        key = _load_private_key(HPC_SSH_KEY_PATH)
        transport.auth_publickey(HPC_USER, key)

        # Factor 2: keyboard-interactive (Duo) if the server still requires it
        if not transport.is_authenticated():
            transport.auth_interactive(HPC_USER, _duo_handler)

        if not transport.is_authenticated():
            transport.close()
            sock.close()
            raise paramiko.AuthenticationException(
                "SSH authentication failed after publickey + keyboard-interactive"
            )

        client = paramiko.SSHClient()
        client._transport = transport
        _ssh_client = client
        logger.info("HPC SSH connection established to %s", HPC_HOST)
        return _ssh_client


def _invalidate_client() -> None:
    """Close and discard the cached SSH client so the next call reconnects."""
    global _ssh_client
    with _ssh_lock:
        if _ssh_client is not None:
            try:
                _ssh_client.close()
            except Exception:
                pass
            _ssh_client = None


def _exec(client: paramiko.SSHClient, command: str) -> tuple[str, str, int]:
    _, stdout, stderr = client.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    return stdout.read().decode("utf-8", errors="replace").strip(), \
           stderr.read().decode("utf-8", errors="replace").strip(), \
           exit_code


def check_hpc_connection() -> dict:
    """
    Test SSH connectivity to the HPC cluster.

    Returns a dict with connection status and basic cluster info.
    """
    if not HPC_ENABLED:
        return _NOT_CONFIGURED
    try:
        client = _get_ssh_client()
        hostname, _, _ = _exec(client, "hostname")
        uptime, _, _ = _exec(client, "uptime")
        partitions, _, _ = _exec(
            client,
            "sinfo -h -o '%P %a %C' 2>/dev/null || echo 'sinfo unavailable'"
        )
        return {
            "status": "connected",
            "host": HPC_HOST,
            "remote_hostname": hostname,
            "uptime": uptime,
            "partitions": partitions,
        }
    except Exception as exc:
        _invalidate_client()
        logger.error("HPC connection check failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def submit_hpc_job(
    script: str,
    partition: str | None = None,
    nodes: int = 1,
    tasks: int = 1,
    memory: str | None = None,
    time: str | None = None,
    gpus: int = 0,
    job_name: str = "idea_job",
    local_output_dir: str | None = None,
) -> dict:
    """
    Submit a Python script as a SLURM batch job on the HPC cluster.

    Parameters
    ----------
    script : str
        Python source code to execute on the cluster.
    partition : str, optional
        SLURM partition/queue name. Defaults to HPC_DEFAULT_PARTITION.
    nodes : int
        Number of nodes (default 1).
    tasks : int
        Tasks per node (default 1).
    memory : str, optional
        Memory per node, e.g. '16G'. Defaults to HPC_DEFAULT_MEMORY.
    time : str, optional
        Walltime as HH:MM:SS. Defaults to HPC_DEFAULT_WALLTIME.
    gpus : int
        GPU count to request (default 0).
    job_name : str
        SLURM job name.
    local_output_dir : str, optional
        Container-local path where output files should be copied after job
        completion. Pass this to get_hpc_job_output later.

    Returns
    -------
    dict
        On success: {"status": "submitted", "job_id": str, "submit_dir": str, "message": str}
        On failure: {"status": "error", "message": str}
    """
    if not HPC_ENABLED:
        return _NOT_CONFIGURED

    partition = partition or HPC_DEFAULT_PARTITION
    memory = memory or HPC_DEFAULT_MEMORY
    time = time or HPC_DEFAULT_WALLTIME

    job_uuid = str(uuid.uuid4())[:8]

    account_line = f"#SBATCH --account={HPC_DEFAULT_ACCOUNT}" if HPC_DEFAULT_ACCOUNT else ""
    gpu_line = f"#SBATCH --gres=gpu:{gpus}" if gpus > 0 else ""
    module_lines = "\n".join(
        f"module load {mod}" for mod in HPC_MODULES.split() if mod
    ) if HPC_MODULES else ""
    # Use `conda run` instead of `conda activate` — activate requires sourcing conda's
    # shell integration first and silently no-ops in non-interactive SLURM batch scripts.
    python_cmd = (
        f"conda run --no-capture-output -n {HPC_CONDA_ENV} python"
        if HPC_CONDA_ENV else "python"
    )

    try:
        client = _get_ssh_client()

        submit_dir = f"{HPC_SCRATCH_DIR}/{job_uuid}"
        _, err, rc = _exec(client, f"mkdir -p {submit_dir}")
        if rc != 0:
            home_out, _, _ = _exec(client, "echo $HOME")
            home = home_out.strip() or f"/home/{HPC_USER}"
            submit_dir = f"{home}/idea_jobs/{job_uuid}"
            logger.warning("Configured scratch dir failed, falling back to %s", submit_dir)
            _, err2, rc2 = _exec(client, f"mkdir -p {submit_dir}")
            if rc2 != 0:
                return {"status": "error", "message": f"Failed to create job directory: {err2}"}

        slurm_script = (
            f"#!/bin/bash\n"
            f"#SBATCH --job-name={job_name}\n"
            f"#SBATCH --partition={partition}\n"
            f"{account_line}\n"
            f"#SBATCH --nodes={nodes}\n"
            f"#SBATCH --ntasks-per-node={tasks}\n"
            f"#SBATCH --mem={memory}\n"
            f"#SBATCH --time={time}\n"
            f"{gpu_line}\n"
            f"#SBATCH --output={submit_dir}/stdout.txt\n"
            f"#SBATCH --error={submit_dir}/stderr.txt\n\n"
            f"{module_lines}\n\n"
            f"cd {submit_dir}\n"
            f"{python_cmd} user_script.py\n"
        )

        sftp = client.open_sftp()
        with sftp.open(f"{submit_dir}/user_script.py", "w") as f:
            f.write(script)
        with sftp.open(f"{submit_dir}/job.slurm", "w") as f:
            f.write(slurm_script)
        sftp.close()

        stdout, stderr, rc = _exec(client, f"sbatch {submit_dir}/job.slurm")

        if rc != 0:
            return {"status": "error", "message": f"sbatch failed: {stderr}"}

        job_id = stdout.split()[-1]
        logger.info("Submitted HPC job %s in %s", job_id, submit_dir)
        return {
            "status": "submitted",
            "job_id": job_id,
            "submit_dir": submit_dir,
            "local_output_dir": local_output_dir,
            "message": (
                f"Job {job_id} submitted to partition '{partition}'. "
                f"Use poll_hpc_job('{job_id}') to check status, "
                f"then get_hpc_job_output('{job_id}', '{submit_dir}') to retrieve results."
            ),
        }

    except Exception as exc:
        _invalidate_client()
        logger.error("submit_hpc_job failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def poll_hpc_job(job_id: str) -> dict:
    """
    Check the current status of a submitted SLURM job.

    Returns
    -------
    dict
        {"job_id": str, "status": str, "elapsed": str, "reason": str}
        status values: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, UNKNOWN
    """
    if not HPC_ENABLED:
        return _NOT_CONFIGURED
    try:
        client = _get_ssh_client()

        stdout, _, _ = _exec(
            client, f"squeue -j {job_id} -h -o '%T %M %R' 2>/dev/null"
        )
        if stdout:
            parts = stdout.split(None, 2)
            return {
                "job_id": job_id,
                "status": parts[0] if parts else "UNKNOWN",
                "elapsed": parts[1] if len(parts) > 1 else "",
                "reason": parts[2] if len(parts) > 2 else "",
            }

        stdout, _, _ = _exec(
            client,
            f"sacct -j {job_id} -n -o State,Elapsed --noheader 2>/dev/null | head -1"
        )
        if stdout:
            parts = stdout.split()
            return {
                "job_id": job_id,
                "status": parts[0].rstrip("+") if parts else "UNKNOWN",
                "elapsed": parts[1] if len(parts) > 1 else "",
                "reason": "",
            }

        return {"job_id": job_id, "status": "UNKNOWN", "elapsed": "", "reason": "Job not found"}

    except Exception as exc:
        _invalidate_client()
        logger.error("poll_hpc_job failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def get_hpc_job_output(
    job_id: str,
    submit_dir: str,
    local_output_dir: str | None = None,
) -> dict:
    """
    Retrieve stdout, stderr, and output files from a completed HPC job.

    Parameters
    ----------
    job_id : str
        SLURM job ID.
    submit_dir : str
        Remote scratch directory returned by submit_hpc_job.
    local_output_dir : str, optional
        Container-local path to copy all output files into.
        Skips user_script.py and job.slurm (input files).

    Returns
    -------
    dict
        {"job_id": str, "stdout": str, "stderr": str,
         "output_files": list[str], "local_files": list[str]}
    """
    if not HPC_ENABLED:
        return _NOT_CONFIGURED
    try:
        client = _get_ssh_client()
        sftp = client.open_sftp()

        def _read_remote(path: str) -> str:
            try:
                with sftp.open(path, "r") as fh:
                    return fh.read().decode("utf-8", errors="replace")
            except Exception:
                return ""

        stdout_text = _read_remote(f"{submit_dir}/stdout.txt")
        stderr_text = _read_remote(f"{submit_dir}/stderr.txt")

        try:
            remote_files = sftp.listdir(submit_dir)
        except Exception:
            remote_files = []

        local_files: list[str] = []
        _skip = {"user_script.py", "job.slurm"}
        if local_output_dir and remote_files:
            os.makedirs(local_output_dir, exist_ok=True)
            for fname in remote_files:
                if fname in _skip:
                    continue
                try:
                    local_path = os.path.join(local_output_dir, fname)
                    sftp.get(f"{submit_dir}/{fname}", local_path)
                    local_files.append(local_path)
                except Exception as exc:
                    logger.warning("Could not copy %s: %s", fname, exc)

        sftp.close()
        return {
            "job_id": job_id,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "output_files": remote_files,
            "local_files": local_files,
        }

    except Exception as exc:
        _invalidate_client()
        logger.error("get_hpc_job_output failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def cancel_hpc_job(job_id: str) -> dict:
    """
    Cancel a running or pending SLURM job.

    Returns
    -------
    dict
        {"status": "cancelled", "job_id": str} on success.
    """
    if not HPC_ENABLED:
        return _NOT_CONFIGURED
    try:
        client = _get_ssh_client()
        _, stderr, rc = _exec(client, f"scancel {job_id}")
        if rc == 0:
            logger.info("Cancelled HPC job %s", job_id)
            return {"status": "cancelled", "job_id": job_id}
        return {"status": "error", "message": stderr or "scancel returned non-zero exit code"}
    except Exception as exc:
        _invalidate_client()
        logger.error("cancel_hpc_job failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def list_hpc_jobs(user: str | None = None) -> dict:
    """
    List active SLURM jobs for the configured HPC user (or a specific user).

    Returns
    -------
    dict
        {"jobs": list[dict], "count": int}
    """
    if not HPC_ENABLED:
        return _NOT_CONFIGURED
    target_user = user or HPC_USER
    try:
        client = _get_ssh_client()
        stdout, _, _ = _exec(
            client,
            f"squeue -u {target_user} -h -o '%i %j %T %P %D %M %R' 2>/dev/null"
        )
        jobs = []
        for line in stdout.splitlines():
            parts = line.split(None, 6)
            if len(parts) >= 6:
                jobs.append({
                    "job_id": parts[0],
                    "name": parts[1],
                    "status": parts[2],
                    "partition": parts[3],
                    "nodes": parts[4],
                    "time_used": parts[5],
                    "reason": parts[6] if len(parts) > 6 else "",
                })
        return {"jobs": jobs, "count": len(jobs)}
    except Exception as exc:
        _invalidate_client()
        logger.error("list_hpc_jobs failed: %s", exc)
        return {"status": "error", "message": str(exc)}
