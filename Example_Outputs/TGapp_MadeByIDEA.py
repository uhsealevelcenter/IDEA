import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import Draw
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, shape
import matplotlib.pyplot as plt
import plotly.express as px

st.set_page_config(layout="wide")
st.title("UHSLC Tide Gauge Stations - Fast Delivery Database")

# Load ALL station metadata
meta_path = './data/metadata/fd_metadata.geojson'
gdf = gpd.read_file(meta_path)
gdf = gdf.reset_index(drop=True)

# Robust longitude conversion to -180 to 180
def wrap_lon(x):
    return ((x + 180) % 360) - 180
gdf['lon'] = gdf.geometry.x.apply(wrap_lon)
gdf['lat'] = gdf.geometry.y

# Remove rows with missing coordinates
gdf = gdf.dropna(subset=['lon', 'lat'])

# Map center and zoom (global view)
center = [0, 160]
m = folium.Map(location=center, zoom_start=2)

# Add markers for each station
for idx, row in gdf.iterrows():
    if pd.notnull(row['lat']) and pd.notnull(row['lon']):
        folium.Marker(
            location=[row['lat'], row['lon']],
            popup=f"{row['name']} (ID: {str(row['uhslc_id']).zfill(3)})",
            tooltip=f"{row['name']}"
        ).add_to(m)

# Add drawing tools (only one drawing at a time)
Draw(export=True, filename='draw.geojson', draw_options={'polyline': False, 'rectangle': False, 'circle': False, 'marker': False, 'circlemarker': False}, edit_options={'edit': True, 'remove': True}).add_to(m)

st.markdown("Draw a polygon around stations to select them. The Fast Delivery daily (long-term means removed) for each station will be plotted below.", unsafe_allow_html=True)
st.markdown("<div style='margin-bottom: -30px;'></div>", unsafe_allow_html=True)

# Streamlit-Folium interaction
output = st_folium(m, width=800, height=500, returned_objects=["last_active_drawing", "all_drawings"])

selected_ids = []
if output and output.get("last_active_drawing"):
    geojson = output["last_active_drawing"]
    if geojson and geojson['geometry']['type'] == 'Polygon':
        poly = shape(geojson['geometry'])
        for idx, row in gdf.iterrows():
            pt = Point(row['lon'], row['lat'])
            if poly.contains(pt):
                selected_ids.append(str(row['uhslc_id']).zfill(3))

if selected_ids:
    st.success(f"Selected station IDs: {', '.join(selected_ids)}")
    # Download and plot daily sea level anomaly for selected stations
    all_data = []
    for sid in selected_ids:
        url = f"https://uhslc.soest.hawaii.edu/erddap/tabledap/global_daily_fast.csvp?sea_level%2Ctime&uhslc_id={sid}"
        try:
            df = pd.read_csv(url)
            df.columns = ['sea_level', 'time']
            df['sea_level'] = df['sea_level'].replace(-32767, pd.NA).astype(float)
            df['time'] = pd.to_datetime(df['time'])
            # Subtract mean from each station's time series
            df['sea_level_anom'] = df['sea_level'] - df['sea_level'].mean(skipna=True)
            station_name = gdf.loc[gdf['uhslc_id'] == int(sid), 'name'].values[0]
            df['Station'] = f"{station_name} (ID: {sid})"
            all_data.append(df[['time', 'sea_level_anom', 'Station']])
        except Exception as e:
            st.warning(f"Could not load data for station {sid}: {e}")
    if all_data:
        plot_df = pd.concat(all_data, ignore_index=True)
        fig = px.line(
            plot_df,
            x='time',
            y='sea_level_anom',
            color='Station',
            labels={
                'sea_level_anom': 'Sea Level Anomaly (mm, Station Zero Datum)',
                'time': 'Date (UTC)'
            },
            title='Fast Delivery daily (long-term means removed)'
        )
        fig.update_layout(legend_title_text='Station')
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No daily sea level data could be loaded for the selected stations.")
else:
    st.info("Select stations by drawing a polygon on the map.")

# if selected_ids:
#     st.success(f"Selected station IDs: {', '.join(selected_ids)}")
#     # Download and plot daily sea level anomaly for selected stations
#     fig, ax = plt.subplots(figsize=(10, 4))
#     plotted = False
#     for sid in selected_ids:
#         url = f"https://uhslc.soest.hawaii.edu/erddap/tabledap/global_daily_fast.csvp?sea_level%2Ctime&uhslc_id={sid}"
#         try:
#             df = pd.read_csv(url)
#             df.columns = ['sea_level', 'time']
#             df['sea_level'] = df['sea_level'].replace(-32767, pd.NA).astype(float)
#             df['time'] = pd.to_datetime(df['time'])
#             # Subtract mean from each station's time series
#             df['sea_level_anom'] = df['sea_level'] - df['sea_level'].mean(skipna=True)
#             # Plot each station as a separate line
#             ax.plot(df['time'], df['sea_level_anom'], label=f"{gdf.loc[gdf['uhslc_id'] == int(sid), 'name'].values[0]} (ID: {sid})")
#             plotted = True
#         except Exception as e:
#             st.warning(f"Could not load data for station {sid}: {e}")
#     if plotted:
#         ax.set_ylabel('Sea Level Anomaly (mm, Station Zero Datum)')
#         ax.set_xlabel('Date (UTC)')
#         ax.set_title('Fast Delivery daily (long-term means removed)')
#         ax.legend(fontsize='small')
#         plt.tight_layout()
#         st.pyplot(fig)
#     else:
#         st.warning("No daily sea level data could be loaded for the selected stations.")
# else:
#     st.info("Select stations by drawing a polygon on the map.")

# --- STATION TABLE AT THE BOTTOM ---
st.markdown("---")
st.write('All UHSLC Fast Delivery stations (lon, lat):')
st.dataframe(gdf[['uhslc_id', 'name', 'country', 'lon', 'lat']])