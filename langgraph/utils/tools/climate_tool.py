"""
Climate Index Tool
Fetches and parses global climate indices (ENSO, PDO, PNA, etc.) and writes
them directly into the current user's sandbox. Large time series never pass
through the model as tool-result text.
"""

import hashlib
import json
import posixpath
import re
import time
from datetime import datetime, timezone
from io import StringIO
from typing import Callable

import numpy as np
import pandas as pd
import requests
from langchain_core.tools import tool

_URLS = {
    # CPC seasonal ONI (3-month means)
    "ONI": "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
    # CPC seasonal RONI (3-month means)
    "RONI": "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt",
    "PDO": "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat",
    "PNA": "https://psl.noaa.gov/data/correlation/pna.data",
    "PMM-SST": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/PMM.txt",
    "AMM-SST": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/AMM.txt",
    "PMM-Wind": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/PMM.txt",
    "AMM-Wind": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/AMM.txt",
    "TNA": "https://psl.noaa.gov/data/correlation/tna.data",
    "AO": "https://psl.noaa.gov/data/correlation/ao.data",
    "NAO": "https://psl.noaa.gov/data/correlation/nao.data",
    "IOD": "https://sealevel.jpl.nasa.gov/api/v1/chartable_values/?category=254&per_page=-1&order=x+asc",
}

_MISSING_VALUES = {
    "ONI": -99.9,
    "RONI": -99.9,
    "PDO": 99.99,
    "PNA": -99.90,
    "PMM-SST": None,
    "AMM-SST": None,
    "PMM-Wind": None,
    "AMM-Wind": None,
    "TNA": -99.99,
    "AO": -999.000,
    "NAO": -99.90,
}

_SEASON_TO_MIDMONTH = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}
_MAX_INDICES_PER_CALL = len(_URLS)
_CANONICAL_INDEX_NAMES = {name.upper(): name for name in _URLS}


def _parse_cpc_oni_like(text: str, value_col: str = "value") -> pd.DataFrame:
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.upper().startswith("SEAS"):
            continue
        parts = re.split(r"\s+", s)
        if len(parts) < 3:
            continue
        seas = parts[0].upper()
        if seas not in _SEASON_TO_MIDMONTH:
            continue
        try:
            year = int(parts[1])
        except Exception:
            continue
        try:
            val = float(parts[-1])
        except Exception:
            val = np.nan

        month = _SEASON_TO_MIDMONTH[seas]
        time = pd.Timestamp(year=year, month=month, day=15)
        rows.append((time, val))

    df = pd.DataFrame(rows, columns=["time", value_col]).drop_duplicates("time", keep="last")
    df[value_col] = df[value_col].replace([-99.9, -99.90, -99.99, -999, -999.0, 99.99], np.nan)
    df = df.sort_values("time").reset_index(drop=True)
    return df


def _fractional_year_to_datetime(y: float) -> pd.Timestamp:
    year = int(np.floor(y))
    frac = y - year
    start = pd.Timestamp(year=year, month=1, day=1)
    end = pd.Timestamp(year=year + 1, month=1, day=1)
    return start + (end - start) * frac


def _get_climate_index_dataframe(climate_index_name: str) -> pd.DataFrame:
    """Load a climate index into a tidy DataFrame with columns (time, value)."""
    if climate_index_name not in _URLS:
        raise ValueError(f"Unknown climate index: {climate_index_name}")

    url = _URLS[climate_index_name]
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    raw_data = resp.text

    if climate_index_name in ["ONI", "RONI"]:
        return _parse_cpc_oni_like(raw_data, value_col="value")

    if climate_index_name in ["PNA", "TNA", "AO", "NAO"]:
        lines = raw_data.splitlines()
        data = []
        for line in lines[1:]:
            if line.strip() and line.split()[0].isdigit():
                tokens = line.split()
                year = int(tokens[0])
                vals = []
                for x in tokens[1:13]:
                    try:
                        fx = float(x)
                    except Exception:
                        fx = np.nan
                    if _MISSING_VALUES.get(climate_index_name) is not None and fx == _MISSING_VALUES[climate_index_name]:
                        fx = np.nan
                    vals.append(fx)
                if len(vals) == 12:
                    data.append([year] + vals)

        df = pd.DataFrame(data, columns=["Year"] + [f"Month_{i}" for i in range(1, 13)])
        df = df.melt(id_vars=["Year"], var_name="Month", value_name="value")
        df["Month"] = df["Month"].str.extract(r"(\d+)").astype(int)
        df["time"] = pd.to_datetime(df[["Year", "Month"]].assign(Day=15))
        df.sort_values("time", inplace=True)
        return df[["time", "value"]].reset_index(drop=True)

    if climate_index_name == "PDO":
        data = pd.read_csv(StringIO(raw_data), sep=r"\s+", skiprows=1)
        data = data.melt(id_vars=["Year"], var_name="Month", value_name="value")
        months = {month: index for index, month in enumerate(
            ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}
        data["Month"] = data["Month"].map(months)
        data = data.dropna(subset=["Month"])
        data["Month"] = data["Month"].astype(int)
        data["time"] = pd.to_datetime(data[["Year", "Month"]].assign(Day=15))
        mv = _MISSING_VALUES.get("PDO", np.nan)
        data["value"] = data["value"].replace(mv, np.nan)
        data.sort_values("time", inplace=True)
        return data[["time", "value"]].reset_index(drop=True)

    if climate_index_name == "IOD":
        iod_data = resp.json()
        if 'items' not in iod_data:
            raise ValueError("Unexpected IOD data structure: 'items' key not found.")

        items = iod_data['items']
        df = pd.DataFrame({
            "time": [_fractional_year_to_datetime(float(item['x'])) for item in items],
            "value": [float(item['y']) for item in items],
        }).set_index("time")

        # Pandas 3 removed the ambiguous ``M`` alias. Resample at month start
        # and then use the 15th as the common timestamp convention shared by
        # every index returned by this tool.
        monthly = df.resample("MS").mean()
        monthly.index = monthly.index + pd.Timedelta(days=14)
        monthly = monthly.reset_index()
        return monthly[["time", "value"]]

    if climate_index_name in ["PMM-SST", "PMM-Wind", "AMM-SST", "AMM-Wind"]:
        columns = ["Year", "Month", "SST", "Wind"]
        data = pd.read_csv(
            StringIO(raw_data), sep=r"\s+", names=columns, skiprows=1
        )
        data["time"] = pd.to_datetime(data[["Year", "Month"]].assign(Day=15))
        value_column = "SST" if "-SST" in climate_index_name else "Wind"
        data = data.rename(columns={value_column: "value"})
        data.sort_values("time", inplace=True)
        return data[["time", "value"]].reset_index(drop=True)

    raise ValueError(f"Unhandled climate index: {climate_index_name}")


def normalize_climate_output_path(output_path: str) -> tuple[str, str]:
    """Return safe CSV/provenance paths under the private workspace."""
    normalized = posixpath.normpath(str(output_path or "").strip())
    if normalized == "/workspace" or not normalized.startswith("/workspace/"):
        raise ValueError("output_path must name a CSV file under /workspace")
    if normalized == "/workspace/uploads" or normalized.startswith(
        "/workspace/uploads/"
    ):
        raise ValueError("output_path may not overwrite synchronized uploads")
    if not normalized.lower().endswith(".csv"):
        raise ValueError("output_path must end in .csv")
    provenance_path = normalized[:-4] + ".provenance.json"
    return normalized, provenance_path


def normalize_climate_index_names(climate_index_names: list[str]) -> list[str]:
    """Validate, uppercase, and de-duplicate requested climate indices."""
    if not isinstance(climate_index_names, list) or not climate_index_names:
        raise ValueError("climate_index_names must contain at least one index")
    if len(climate_index_names) > _MAX_INDICES_PER_CALL:
        raise ValueError(
            f"at most {_MAX_INDICES_PER_CALL} climate indices may be fetched "
            "in one call"
        )

    normalized = []
    for value in climate_index_names:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("each climate index name must be a non-empty string")
        requested_name = value.strip().upper()
        name = _CANONICAL_INDEX_NAMES.get(requested_name)
        if name is None:
            raise ValueError(
                f"unknown climate index {value!r}; supported indices are "
                f"{', '.join(_URLS)}"
            )
        if name not in normalized:
            normalized.append(name)
    return normalized


def build_climate_indices_bundle(
    climate_index_names: list[str],
    output_path: str,
    *,
    fetch_index: Callable[[str], pd.DataFrame] | None = None,
    retrieved_at: datetime | None = None,
) -> tuple[bytes, bytes, dict]:
    """Build one long-form CSV and compact machine-readable provenance."""
    names = normalize_climate_index_names(climate_index_names)
    csv_path, provenance_path = normalize_climate_output_path(output_path)
    fetch_index = fetch_index or _get_climate_index_dataframe
    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    frames = []
    index_metadata = []
    starts = []
    ends = []
    started = time.monotonic()

    for name in names:
        frame = fetch_index(name).copy()
        if not {"time", "value"}.issubset(frame.columns):
            raise ValueError(
                f"{name} data must contain time and value columns"
            )
        frame = frame[["time", "value"]]
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
        if frame["time"].isna().any():
            raise ValueError(f"{name} data contains invalid timestamps")
        if frame.empty:
            raise ValueError(f"{name} data source returned no rows")
        if frame["time"].duplicated().any():
            raise ValueError(f"{name} data contains duplicate timestamps")
        frame = frame.sort_values("time").reset_index(drop=True)

        start = frame["time"].iloc[0]
        end = frame["time"].iloc[-1]
        starts.append(start)
        ends.append(end)
        index_metadata.append({
            "name": name,
            "source_url": _URLS[name],
            "rows": int(len(frame)),
            "missing_values": int(frame["value"].isna().sum()),
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        })
        frame.insert(1, "index", name)
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    combined = combined[["time", "index", "value"]].sort_values(
        ["time", "index"]
    )
    csv_text = combined.to_csv(
        index=False,
        date_format="%Y-%m-%d",
        lineterminator="\n",
    )
    csv_bytes = csv_text.encode("utf-8")
    common_start = max(starts)
    common_end = min(ends)

    metadata = {
        "schema_version": 1,
        "dataset_path": csv_path,
        "provenance_path": provenance_path,
        "format": "long_csv",
        "columns": ["time", "index", "value"],
        "retrieved_at": retrieved_at.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "total_rows": int(len(combined)),
        "sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "common_period": (
            {
                "start": common_start.strftime("%Y-%m-%d"),
                "end": common_end.strftime("%Y-%m-%d"),
            }
            if common_start <= common_end
            else None
        ),
        "indices": index_metadata,
    }
    provenance_bytes = (
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return csv_bytes, provenance_bytes, metadata


def make_get_climate_indices_tool(
    write_bytes: Callable[[str, bytes], int],
):
    """Create a climate tool bound to one user's sandbox writer."""

    @tool
    def get_climate_indices_tool(
        climate_index_names: list[str],
        output_path: str = "/workspace/climate_indices.csv",
    ) -> str:
        """
        Fetch one or more global climate indices directly into the private
        workspace as one tidy long-form CSV with columns time,index,value.
        A provenance JSON file is written beside it. The dataset itself is
        never returned through model context.

        Always call this instead of scraping or reimplementing a climate-index
        fetch. Batch indices needed for one analysis into the same call. Read
        the returned dataset_path with Python for plotting or analysis.

        NOAA/NCEP CPC transitioned to RONI as the official ENSO index effective
        February 1, 2026; legacy ONI remains available.

        Args:
            climate_index_names: One or more of RONI, ONI, PDO, PNA, PMM-SST,
                PMM-Wind, AMM-SST, AMM-Wind, TNA, AO, NAO, or IOD.
            output_path: Destination CSV under /workspace.

        Returns:
            Compact JSON metadata containing paths, source URLs, row counts,
            date coverage, missing-value counts, and a checksum.
        """
        csv_bytes, provenance_bytes, metadata = build_climate_indices_bundle(
            climate_index_names,
            output_path,
        )
        for path, data in (
            (metadata["dataset_path"], csv_bytes),
            (metadata["provenance_path"], provenance_bytes),
        ):
            written = write_bytes(path, data)
            if written != len(data):
                raise RuntimeError(
                    f"incomplete sandbox write for {path}: expected "
                    f"{len(data)} bytes, wrote {written}"
                )
        return json.dumps(metadata, separators=(",", ":"), sort_keys=True)

    return get_climate_indices_tool
