from datetime import datetime
import io
import requests
import pandas as pd
import json
import pickle
from bs4 import BeautifulSoup
from collections import defaultdict

# Base URL where the CSV files are hosted
base_url = "https://uhslc.soest.hawaii.edu/data/csv/fast/"
output_json_path = "_sea_level_data_single_date_all_v2.json"
output_pickle_path = "_sea_level_data_single_date_all_v2.pkl"

# Function to fetch and process a CSV file from the URL
def fetch_and_process_station_data(station_number, frequency):
    url = f"{base_url+frequency}/{station_number}.csv"
    response = requests.get(url)
    
    # Check if the file is accessible
    if response.status_code != 200:
        print(f"Error fetching data for station {station_number}")
        return None
    
    # Load CSV data into a pandas DataFrame
    csv_data = pd.read_csv(io.StringIO(response.text), header=None)
    
    # Rename the columns
    if frequency == "hourly":
        csv_data.columns = ['Year', 'Month', 'Day', 'Hour', 'Sea_Level']
    elif frequency == "daily":
        csv_data.columns = ['Year', 'Month', 'Day', 'Sea_Level']
    
    
    # Create a nested dictionary structure: Year -> Month -> Day -> list of (Hour, Sea_Level)
    station_data = defaultdict(list)
    for i, row in csv_data.iterrows():
        if frequency == "hourly":
            year, month, day, hour, sea_level = row
        elif frequency == "daily":
            year, month, day, sea_level = row
        if i == 0:
            if frequency == "hourly":
                start_date = datetime(year, month, day, hour).strftime("%Y%m%d%H")
            elif frequency == "daily":
                start_date = datetime(year, month, day).strftime("%Y%m%d")
            # start_date = str(year)+str(month)+str(day)+str(hour)
        station_data["sea_level_data"].append(sea_level)
        station_data["start_date"] = start_date
        # station_data["station_id"] = station_number[1:]  # remove the h or d

    # Create a nested dictionary structure: Year -> Month -> Day -> list of (Hour, Sea_Level)
    # station_data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    # for _, row in csv_data.iterrows():
    #     year, month, day, hour, sea_level = row
    #     station_data[year][month][day].append([hour, sea_level])
    
    return station_data

# Function to merge the data into the overall structure
def merge_station_data(all_data, station_number, station_data, frequency):
    all_data[frequency][station_number] = station_data

# Function to scrape available station file names from the webpage
def get_available_station_numbers(frequency="hourly"):
    response = requests.get(base_url+frequency+"/")
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Find all href elements that match the pattern hXXX.csv (where XXX is a three-digit station number)
    station_files = []
    total_size = 0
    for link in soup.find_all('a'):
        # info_text = link.next_sibling.strip()
        # if info_text:
        #     size_text = info_text.split()[-1]
        #     if size_text.isdigit():
        #         total_size += int(size_text)
        # if total_size/1024/1024 > 512:
        #     print(f"Station {href.replace('.csv', '')} should be carried over to a new file")
        href = link.get('href')
        startswith = href.startswith('h') if frequency == "hourly" else href.startswith('d')
        if href and startswith and href.endswith('.csv'):
            station_files.append(href.replace('.csv', ''))  # Extract station number (e.g., h001)
    print(f"Total size of all files: {total_size/1024/1024} MB")
    return station_files

# Main function to download all stations and save to JSON and Pickle
def download_and_save_all_stations(frequency="hourly"):
    all_data = {frequency: {}}

    # Fetch all available station numbers from the base url
    station_numbers = get_available_station_numbers(frequency=frequency)
    # station_numbers = ["h001","h002","h003","h004","h057"] # For prototyping, getting only
    #  individual stations
    # station_numbers = ["h001","h002","h003"]

    for station_number in station_numbers:
        print(f"Processing station {station_number}...")
        station_data = fetch_and_process_station_data(station_number, frequency=frequency)
        
        if station_data:
            # Merge the station data into the overall structure
            merge_station_data(all_data, station_number[1:], station_data, frequency)
    
    # Save to JSON
    with open(frequency+output_json_path, 'w') as json_file:
        json.dump(all_data, json_file, separators=(',', ':'))
    
    # Save to Pickle
    with open(frequency+output_pickle_path, 'wb') as pickle_file:
        pickle.dump(all_data, pickle_file, protocol=pickle.DEFAULT_PROTOCOL)
    
    print(f"Data saved to {output_json_path} and {output_pickle_path}")

def download_and_save_all_stations_chunked(chunk_size=5):
    all_data = {}
        # Fetch all available station numbers from the base url
    station_numbers = get_available_station_numbers()
    station_numbers = ["h001","h002","h003","h004","h057"] # For prototyping, getting only
    #  individual stations
    # station_numbers = ["h057"]

    for station_number in station_numbers:
        print(f"Processing station {station_number}...")
        station_data = fetch_and_process_station_data(station_number)
        
        if station_data:
            # Merge the station data into the overall structure
            merge_station_data(all_data, station_number, station_data)

    # Function to calculate the size of a JSON string
    def get_json_size(data):
        return len(json.dumps(data, separators=(',', ':')).encode('utf-8'))

    # Initialize variables for file splitting
    max_file_size = chunk_size * 1024 * 1024 # chunk size in bytes
    current_file_data = {}
    file_counter = 1

    for station_number, station_data in all_data.items():
        # Check if adding this station would exceed the file size limit
        if get_json_size(current_file_data) + get_json_size({station_number: station_data}) > max_file_size:
            # Save current file and start a new one
            output_json_path = f"sea_level_data_combined_{file_counter}.json"
            with open(output_json_path, 'w') as json_file:
                json.dump(current_file_data, json_file, separators=(',', ':'))
            print(f"Data saved to {output_json_path}")
            
            # Reset for next file
            current_file_data = {}
            file_counter += 1

        # Add station data to current file
        current_file_data[station_number] = station_data

    # Save any remaining data
    if current_file_data:
        output_json_path = f"sea_level_data_combined_{file_counter}.json"
        with open(output_json_path, 'w') as json_file:
            json.dump(current_file_data, json_file, separators=(',', ':'))
        print(f"Data saved to {output_json_path}")

def load_and_process_data():
    """
    Helper function to load and display the data for testing
    """
    from datetime import datetime

    with open('hourly_sea_level_data_combined.json', 'r') as f:
        sea_level_data = json.load(f)

    station_id = "h057"
    station_data = sea_level_data.get(station_id, {})

    # Prepare data for plotting
    dates = []
    all_records = []

    # for station, years in station_data.items():
    for year, months in station_data.items():
        for month, days in months.items():
            for day, hours in days.items():
                for hour, sea_level in hours:
                    timestamp = datetime(int(year), int(month), int(day), int(hour))
                    all_records.append({
                        'station': station_id,
                        'timestamp': timestamp,
                        'sea_level': sea_level
                    })
    df = pd.DataFrame(all_records)
    
    df.set_index('timestamp', inplace=True)

    return df

def plot_sea_level_data(df):
    import numpy as np
    import matplotlib.pyplot as plt

    # Group by station and plot
    # for station in df['station'].unique():
    #     station_data = df[df['station'] == station]
    #     plt.figure(figsize=(12, 6))
    #     plt.plot(station_data.index, station_data['sea_level'])
    #     plt.title(f'Sea Level Data for Station {station}')
    #     plt.xlabel('Date')
    #     plt.ylabel('Sea Level')
    #     plt.grid(True)
    #     plt.tight_layout()
    #     plt.savefig(f'sea_level_plot_{station}.png')
    #     plt.close()

    plt.figure(figsize=(12, 6))
    plt.plot(df.index, df['sea_level'])
    plt.title(f'Sea Level Data for Station')
    plt.xlabel('Date')
    plt.ylabel('Sea Level')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(f'sea_level_plot_.png')
    plt.close()


# Download and save data for all available stations
# download_and_save_all_stations(frequency="hourly")
download_and_save_all_stations(frequency="hourly")
# download_and_save_all_stations_chunked(chunk_size=4)
# print
# df = load_and_process_data()
# plot_sea_level_data(df)
# get_available_station_numbers()