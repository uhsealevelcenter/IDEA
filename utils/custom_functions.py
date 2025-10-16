custom_tool = """
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import requests
from io import StringIO
from datetime import datetime, timedelta, timezone
from litellm import completion 
from utils.station_list_appendix import station_list_appendix # Station List Appendix (id and name)
import os

def get_datetime():
    now_utc = datetime.now(timezone.utc)
    if now_utc.microsecond >= 500_000:
        now_utc += timedelta(seconds=1)
    now_utc = now_utc.replace(microsecond=0)
    return {
        "iso": now_utc.isoformat(),
        "human": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    }

def fractional_year_to_datetime(year):
    # Convert fractional year (e.g., 1992.7978142) to datetime.
    year_int = int(year)
    fraction = year - year_int
    start_of_year = datetime(year_int, 1, 1)
    days_in_year = (datetime(year_int + 1, 1, 1) - start_of_year).days
    return start_of_year + timedelta(days=fraction * days_in_year)

def get_station_info(station_query):
    # LiteLLM 
    station_query_response = completion(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": station_list_appendix},
            {"role": "user", "content": station_query}
        ],
        stream=False
    )
    return {"station_query_response": station_query_response.choices[0].message.content}

def get_climate_index(climate_index_name):
    # Parameters:
    #     climate_index_name (str): Abbreviation of the climate index (e.g., 'ONI', 'PDO').
    # Returns:
    #     pd.DataFrame: A DataFrame containing the climate index data in the format (time, value).
    
    urls = {
        "ONI": "https://psl.noaa.gov/data/correlation/oni.data",
        "PDO": "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat",
        "PNA": "https://psl.noaa.gov/data/correlation/pna.data",
        "PMM-SST": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/PMM.txt",
        "AMM-SST": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/AMM.txt",
        "PMM-Wind": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/PMM.txt",
        "AMM-Wind": "https://www.aos.wisc.edu/dvimont/MModes/RealTime/AMM.txt",
        "TNA": "https://psl.noaa.gov/data/correlation/tna.data",
        "AO": "https://psl.noaa.gov/data/correlation/ao.data",
        "NAO": "https://psl.noaa.gov/data/correlation/nao.data",
        "IOD": "https://sealevel.jpl.nasa.gov/api/v1/chartable_values/?category=254&per_page=-1&order=x+asc"
    }
    missing_values = {
        "ONI": -99.90,
        "PDO": 99.99,
        "PNA": -99.90,
        "PMM-SST": None,  # Handled directly by pandas
        "AMM-SST": None,  # Handled directly by pandas
        "PMM-Wind": None,  # Handled directly by pandas
        "AMM-Wind": None,  # Handled directly by pandas
        "TNA": -99.99,
        "AO": -999.000,
        "NAO": -99.90
    }
    if climate_index_name not in urls:
        raise ValueError(f"Unknown climate index: {climate_index_name}")
    url = urls[climate_index_name]
    response = requests.get(url)
    response.raise_for_status()
    raw_data = response.text
    if climate_index_name in ["ONI", "PNA", "TNA", "AO", "NAO"]:
        lines = raw_data.splitlines()
        start_year, end_year = map(int, lines[0].split()[:2])
        data = []
        for line in lines[1:]:
            if line.strip() and line.split()[0].isdigit():
                year_data = [float(x) if x != missing_values[climate_index_name] else np.nan for x in line.split()]
                if year_data[0] == missing_values[climate_index_name]:
                    break
                data.append(year_data)
        df = pd.DataFrame(data, columns=["Year"] + [f"Month_{i}" for i in range(1, 13)])
        df = df.melt(id_vars=["Year"], var_name="Month", value_name="value")
        df["Month"] = df["Month"].str.extract(r"(\d+)").astype(int)
        df["time"] = pd.to_datetime(df[["Year", "Month"]].assign(Day=15))
        df["value"] = df["value"].replace(missing_values[climate_index_name], np.nan)
        df.sort_values(by="time", inplace=True)
        return df[["time", "value"]]
    elif climate_index_name == "PDO":
        # Read the data, skipping the first metadata line
        data = pd.read_csv(
            StringIO(raw_data),
            delim_whitespace=True,
            skiprows=1  # Skip the first line containing "ERSST PDO Index:"
        )
        # Reshape from wide to long format
        data = data.melt(
            id_vars=["Year"],
            var_name="Month",
            value_name="value"
        )
        # Convert Month column to numeric (Jan, Feb, etc.)
        months = {month: index for index, month in enumerate(
            ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}
        data["Month"] = data["Month"].map(months)
        # Drop rows where Month is NaN
        data = data.dropna(subset=["Month"])
        # Ensure Month is an integer
        data["Month"] = data["Month"].astype(int)
        # Create a datetime column
        data["time"] = pd.to_datetime(data[["Year", "Month"]].assign(Day=15))
        # Replace missing values with NaN
        missing_value = missing_values.get("PDO", np.nan)
        data["value"] = data["value"].replace(missing_value, np.nan)
        # Sort by time and return cleaned data
        data.sort_values(by="time", inplace=True)
        return data[["time", "value"]]
    if climate_index_name == "IOD":
        response = requests.get(url)
        response.raise_for_status()
        iod_data = response.json()
        # Verify structure
        if 'items' not in iod_data:
            raise ValueError("Unexpected data structure: 'items' key not found.")
        items = iod_data['items']
        data = {
            "time": [fractional_year_to_datetime(float(item['x'])) for item in items],
            "value": [float(item['y']) for item in items]
        }
        df = pd.DataFrame(data)
        # Resample to monthly frequency, compute mean, and center on the 15th
        df = df.set_index('time')
        monthly_means = df.resample('M').mean()
        monthly_means.index = monthly_means.index + pd.Timedelta(days=15)  # Shift to center on the 15th
        monthly_means.reset_index(inplace=True)
        return monthly_means
    elif climate_index_name in ["PMM-SST", "PMM-Wind", "AMM-SST", "AMM-Wind"]:
        columns = ["Year", "Month", "SST", "Wind"]
        data = pd.read_csv(StringIO(raw_data), delim_whitespace=True, names=columns, skiprows=1)
        data["time"] = pd.to_datetime(data[["Year", "Month"]].assign(Day=15))
        # Determine whether to use "SST" or "Wind" as the value column based on the index name
        value_column = "SST" if "-SST" in climate_index_name else "Wind"
        data = data.rename(columns={value_column: "value"})
        data.sort_values(by="time", inplace=True)
        return data[["time", "value"]]
    raise ValueError(f"Unhandled climate index: {climate_index_name}")

def web_search(web_query):
    # LiteLLM 
    web_query_response = completion(
        model="gpt-4o-search-preview",
        messages=[
            {"role": "system", "content": "You are a concise research assistant."},
            {"role": "user", "content": web_query}
        ],
        stream=False
    )
    return {"web_query_response": web_query_response.choices[0].message.content}    
"""
