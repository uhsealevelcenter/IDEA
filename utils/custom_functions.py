custom_tool = """
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import requests
from io import StringIO
from datetime import datetime, timedelta


def get_people():
    # URL of the UHSLC personnel directory
    url = 'https://uhslc.soest.hawaii.edu/about/people/'

    # Fetch the webpage
    response = requests.get(url)

    # Parse the HTML content
    soup = BeautifulSoup(response.content, 'html.parser')

    # Extract names and roles
    people = []
    for person in soup.find_all('div', class_='person-desc'):
        name_tag = person.find('span', class_='person-name')
        person_title = person.find('span', class_='person-title')
        person_content = person.find('div', class_='person-content')
        name = name_tag.get_text(strip=True)
        role = person_title.get_text(strip=True)
        # about = person_content.get_text(strip=True)
        people.append((name, role))

    return people


def fractional_year_to_datetime(year):
    # Convert fractional year (e.g., 1992.7978142) to datetime.
    year_int = int(year)
    fraction = year - year_int
    start_of_year = datetime(year_int, 1, 1)
    days_in_year = (datetime(year_int + 1, 1, 1) - start_of_year).days
    return start_of_year + timedelta(days=fraction * days_in_year)

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

"""
