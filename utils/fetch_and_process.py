import requests
import json
import os

def fetch_and_process():
    # URLs for the JSON and GeoJSON data
    json_url = "https://uhslc.soest.hawaii.edu/metaapi/select2"
    geojson_url = "https://uhslc.soest.hawaii.edu/data/meta.geojson"


    json_response = requests.get(json_url)
    json_data = json_response.json()


    geojson_response = requests.get(geojson_url)
    geojson_data = geojson_response.json()

    # Extract IDs from the JSON data
    json_ids = {item['id'] for item in json_data['results']}

    # Extract features from the GeoJSON data
    geojson_features = geojson_data['features']

    # Filter features that match the IDs
    matched_features = [feature for feature in geojson_features if str(feature['properties']['uhslc_id']).zfill(3) in json_ids]

    output_geojson = {
        "type": "FeatureCollection",
        "features": matched_features
    }

    output_dir = "/tmp"
    output_file = f"{output_dir}/fd_metadata.geojson"


    with open(output_file, 'w') as f:
        json.dump(output_geojson, f)

    return output_file

if __name__ == "__main__":
    output_file = fetch_and_process()
    print(f"File saved to {output_file}")