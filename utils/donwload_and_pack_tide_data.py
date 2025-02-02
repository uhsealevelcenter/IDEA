import pandas as pd
import pickle
import os
from datetime import datetime, timedelta

from data_endpoints import get_tide_data
from download_and_pack_sealevel_data import get_available_station_numbers

def generate_date_range(start_date, end_date):
    current_date = datetime.strptime(start_date, "%Y%m")
    end = datetime.strptime(end_date, "%Y%m")
    while current_date <= end:
        yield current_date.strftime("%Y%m")
        current_date += timedelta(days=32)
        current_date = current_date.replace(day=1)

def save_tide_data(station_ids, start_date, end_date):
    os.makedirs('tide_data', exist_ok=True)
    
    all_station_data = {}
    
    for station_id in station_ids:
        station_with_error = None
        all_tide_data = []
        notes = None
        
        for date in generate_date_range(start_date, end_date):
            try:
                tide_data, month_notes = get_tide_data(station_id, date)
                all_tide_data.append(tide_data)
                
                if notes is None:
                    notes = month_notes
            except Exception as e:
                print(f"Error getting tide data for station {station_id} on {date}: {e}")
                station_with_error = station_id
                continue
        if station_with_error:
            continue
        combined_tide_data = pd.concat(all_tide_data)
        
        # Convert index to list, handling both RangeIndex and DatetimeIndex
        if isinstance(combined_tide_data.index, pd.DatetimeIndex):
            index = combined_tide_data.index.strftime('%Y-%m-%d %H:%M:%S').tolist()
        else:
            index = combined_tide_data.index.tolist()
        
        tide_data_dict = {
            'index': index,
            'data': combined_tide_data.to_dict(orient='list')
        }
        
        all_station_data[station_id] = {
            'metadata': {
                'station_id': station_id,
                'date_range': f"{start_date}-{end_date}",
                'created_at': datetime.now().isoformat(),
                'notes': notes
            },
            'tide_data': tide_data_dict
        }
        
        print(f"Processed data for station {station_id}")
    
    # Save all station data to a single file
    file_path = f'tide_data/all_stations_{start_date}_{end_date}.pkl'
    
    with open(file_path, 'wb') as f:
        pickle.dump(all_station_data, f, protocol=pickle.HIGHEST_PROTOCOL)
    
    print(f"Saved data for all stations to {file_path}")

# Example usage
station_ids = get_available_station_numbers("hourly")
# station_ids = ['117','118', '119']  # Add all your station IDs here
start_date = "202401"
end_date = "202612"

save_tide_data(station_ids, start_date, end_date)

def load_tide_data(station_id):
    file_path = f'tide_data/all_stations_{start_date}_{end_date}.pkl'
    
    with open(file_path, 'rb') as f:
        data = pickle.load(f)
    
    tide_data_dict = data[station_id]['tide_data']
    df = pd.DataFrame(tide_data_dict['data'], index=tide_data_dict['index'])
    
    return df

def plot_tide_data(station_id):
    import matplotlib.pyplot as plt
    df = load_tide_data(station_id)
    
    plt.figure(figsize=(12, 6))
    plt.plot(df['DateTime'], df['Tide'], label='Water Level')
    plt.title(f"Tide Data for Station {station_id}")
    plt.xlabel('Date')
    plt.ylabel('Water Level')
    plt.legend()
    plt.grid(True)
    
    # Rotate and align the tick labels so they look better
    plt.gcf().autofmt_xdate()
    
    # Use a tight layout
    # plt.tight_layout()
    
    # Save the plot
    plt.savefig(f'tide_data_plot_{station_id}.png')
    print(f"Plot saved as tide_data_plot_{station_id}.png")
    
    # Show the plot
    plt.show()


# plot_tide_data("119")