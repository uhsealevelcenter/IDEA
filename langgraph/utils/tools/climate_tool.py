"""
Climate Index Tool
Fetches and parses global climate indices (ENSO, PDO, PNA, etc.) into a tidy
(time, value) time series, returned as CSV text so the agent can save it to
a file (via write_file_tool) and analyze/plot it from the terminal.
"""

import re
from io import StringIO

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
        data = pd.read_csv(StringIO(raw_data), delim_whitespace=True, skiprows=1)
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

        monthly = df.resample('M').mean()
        monthly.index = monthly.index + pd.Timedelta(days=15)
        monthly = monthly.reset_index()
        return monthly[["time", "value"]]

    if climate_index_name in ["PMM-SST", "PMM-Wind", "AMM-SST", "AMM-Wind"]:
        columns = ["Year", "Month", "SST", "Wind"]
        data = pd.read_csv(StringIO(raw_data), delim_whitespace=True, names=columns, skiprows=1)
        data["time"] = pd.to_datetime(data[["Year", "Month"]].assign(Day=15))
        value_column = "SST" if "-SST" in climate_index_name else "Wind"
        data = data.rename(columns={value_column: "value"})
        data.sort_values("time", inplace=True)
        return data[["time", "value"]].reset_index(drop=True)

    raise ValueError(f"Unhandled climate index: {climate_index_name}")


@tool
def get_climate_index_tool(climate_index_name: str) -> str:
    """
    Fetch a global climate index time series as CSV text (columns: time, value).

    Always call this instead of scraping or re-implementing a fetch for a
    climate index. Use write_file_tool to save the CSV output to a file
    before plotting or further analysis.

    NOAA/NCEP CPC transitioned to the Relative Oceanic Nino Index (RONI) as
    the official ENSO monitoring/prediction index effective February 1, 2026;
    RONI is a 3-month running mean of Nino 3.4 SST anomalies made relative to
    the global tropics (20N-20S), rescaled to match traditional ONI
    amplitude, and uses the same +/-0.5C threshold for ENSO classification.
    Legacy ONI files remain available.

    Args:
        climate_index_name: One of "RONI", "ONI", "PDO", "PNA", "PMM-SST",
            "PMM-Wind", "AMM-SST", "AMM-Wind", "TNA", "AO", "NAO", "IOD".

    Returns:
        A message with row count, a short preview, and the full CSV data.
    """
    df = _get_climate_index_dataframe(climate_index_name)
    csv_text = df.to_csv(index=False)
    preview = df.head(5).to_string(index=False)
    return (
        f"Loaded {len(df)} rows for '{climate_index_name}'.\n"
        f"Preview:\n{preview}\n\n"
        f"Full CSV data:\n{csv_text}"
    )
