import osmnx as ox
import networkx as nx
import geopandas as gpd
import pandas as pd
from datetime import datetime
import pytz
import streamlit as st
import anthropic

# ------------------------ SETTINGS ------------------------
ox.settings.use_cache = True
ox.settings.requests_timeout = 300
client = anthropic.Anthropic()

# ------------------------ Extracting the 'Walk' network for BCN ------------------------
gpkg_path = "noise_data_2017.gpkg"
@st.cache_data #Caching the graph to speed up subsequent runs, especially during development.
def load_graph(address, dist=3000):
    print("Scanning Barcelona...")
    G = ox.graph_from_address(address, dist, network_type="walk")
    _, edges = ox.graph_to_gdfs(G) #leaving nodes (the underscore) as we are working with roads, not intersections.
    return G, edges
G, edges = load_graph("Barcelona, Spain")

#FETCHING TIME IN BARCELONA - HARDCODING LOCAL TIME TO AVOID ISSUES WITH TIMEZONE CONFIGURATION IN DIFFERENT ENVIRONMENTS. 
#FOR A MUlTICITY APPLICATION, THIS WOULD NEED TO BE CONFIGURED TO FETCH LOCAL TIME BASED ON THE CITY IN QUESTION.
def get_local_time():
    tz = pytz.timezone('Europe/Madrid')
    bcn_hour = datetime.now(tz).hour
    return bcn_hour

# Selecting Noise Column Based on time
def get_noise_column():
    hour = get_local_time()
    if 7 <= hour < 19: 
        col = 'TOTAL_D'
    elif 19 <= hour < 23:
        col = 'TOTAL_E'
    else: 
        col = 'TOTAL_N'
    return col

noise_column = get_noise_column() #Calling function outside the caching that comes below so that it can rerun based on datetime.now

# -------------------- STREAMLIT SLIDER FOR NOISE SENSITIVITY (k) --------------------
k_label = st.select_slider(    #Tuning parameter to adjust influence of noise on the overall cost. 
        'Walking preference',
        options=['Fastest', 'Balanced', 'Quiet', 'Serene'],
        value='Balanced'    #Hardcoded starting point
    )
mapping = {
        'Fastest': 0.5,
        'Balanced': 1.5,
        'Quiet': 3,
        'Serene': 5
    }
k = mapping[k_label]


# ------------- Joining the GeoPackage noise data with the OSMNX data -------------
@st.cache_data
def map_data_join(_edges, gpkg_path, noise_column):     #Have put "_edges" so that it doesn't cache edges. 
    noise_gdf = gpd.read_file(gpkg_path, layer='2017_Tramer_Mapa_Estrategic_Soroll_BCN')  

    # Coordinate Reference System (CRS) Alignment (Degrees vs Meters in different maps (OpenData BCN vs. OSMNX) need to be homogenised)
    noise_gdf = noise_gdf.to_crs(_edges.crs)
    bcn_crs = "EPSG:25831" # UTM Zone 31N, commonly used for Barcelona.

    # Spatial Join (Snapping closest noise data to the streets)
    edges_projected = _edges.to_crs(bcn_crs)
    noise_projected = noise_gdf.to_crs(bcn_crs)
    
    joined = gpd.sjoin_nearest(
        edges_projected, 
        noise_projected, 
        how="left", 
        distance_col="dist" 
    )

    joined = joined[~joined.index.duplicated(keep='first')] 
    
    # Converting column to floats as they're stored as strings in the GeoPackage. Added regex to find upper bound of noise range, and take only two digits.
    noise_values = pd.to_numeric(joined[noise_column].str.extract("- (\d+)")[0], errors='coerce').fillna(75) # Assuming 75 dB for streets without noise data, which is a conservative estimate to avoid false positives.

    edges_projected['noise_values'] = noise_values
    noise_normalised = (noise_values - noise_values.min()) / (noise_values.max() - noise_values.min()) # Normalising values between 0 and 1. 
    return noise_normalised, edges_projected




# ---------------------- Applying Noise Constraints to the Edges --------------------

def set_noise_constraints(noise_normalised, edges_projected, k):
    return edges_projected['length'] + (noise_normalised ** k) * 500 #decibels are logarithmic, so we apply the exponent to reflect the non-linear increase in perceived noise. 

noise_normalised, edges_projected = map_data_join(edges, gpkg_path, noise_column) #Joining the noise data to the edges, and normalising it. This is cached to speed up subsequent runs, as this is the most computationally expensive part of the process.
weighted_cost = set_noise_constraints(noise_normalised, edges_projected, k) #Applying the noise constraints to the edges, with the selected k value.

# ------------------------------------ Routing Comparison ------------------------------------
edges['weighted_cost'] = weighted_cost  #Converting back to a Series of values to push back to the graph. 
weights_dict = edges['weighted_cost'].to_dict() #Precautionary step to ensure weights are formatted such that they can be pushed back easily to graph.

nx.set_edge_attributes(G, weights_dict, 'weighted_cost') # Push scores back to the graph

# ------------------------------ USER INPUT FOR START AND END POINTS ------------------------------

start_input = st.text_input("Enter your starting point (e.g., 'Plaça de Catalunya, Barcelona'):", value="Parc Joan Miró, Barcelona")
end_input = st.text_input("Enter your destination (e.g., 'Sagrada Família, Barcelona'):", value="Sagrada Família, Barcelona")

if not start_input or not end_input:
    st.error("Please enter a starting point and a destination.")
    st.stop() #Stop the app if either input is missing, to avoid errors in geocoding.
try: 
    start_point = ox.geocoder.geocode(start_input)
except Exception as e:
    st.error(f"Couldn't geocode starting point: {e}")
    st.stop()
if start_point is None:
    st.error("Couldn't geocode starting point. Please check your input and try again.")
    st.stop()

try:
    end_point = ox.geocoder.geocode(end_input)
except Exception as e:
    st.error(f"Couldn't geocode destination: {e}")
    st.stop()
if end_point is None:
    st.error("Couldn't geocode destination. Please check your input and try again.")
    st.stop()

# ------------ ROUTE MAPPING AND VISUALISATION -------------
if st.button("Find route"):
    orig = ox.distance.nearest_nodes(G, X=start_point[1], Y=start_point[0])
    dest = ox.distance.nearest_nodes(G, X=end_point[1], Y=end_point[0])

    with st.spinner("Calculating routes..."):
        route_fast = ox.shortest_path(G, orig, dest, weight='length') #weight = which edges attribute to minimise
        route_quiet = ox.shortest_path(G, orig, dest, weight='weighted_cost')
        route_fast_edges = ox.routing.route_to_gdf(G, route_fast)
        route_quiet_edges = ox.routing.route_to_gdf(G, route_quiet)
    
    #Finding which roads are in the quiet but not in the fast route.
    quiet_road_names = route_quiet_edges['name'].explode().unique().tolist()
    fast_road_names = route_fast_edges['name'].explode().unique().tolist()
    main_roads_avoided = [road for road in fast_road_names if road not in quiet_road_names] #List comprehension to find which roads are in the fast route but not in the quiet route.
    
    fast_noise = edges_projected.loc[route_fast_edges.index, 'noise_values'].mean().round() 
    quiet_noise = edges_projected.loc[route_quiet_edges.index, 'noise_values'].mean().round()


    len_fast = route_fast_edges['length'].sum()
    len_quiet = route_quiet_edges['length'].sum()

    fast_time = (len_fast / 1000 * 12).round() #Assuming an average walking speed of 5 km/h, which is 12 minutes per km. This is a simplification and could be improved by using more granular speed data based on road type, slope, etc.
    quiet_time = (len_quiet / 1000 * 12).round()

    st.metric(label="Fast Route", value=f"{len_fast:.2f} meters. Estimated time: {fast_time} minutes")
    st.metric(label=f"Quiet Route, {k_label} mode", value=f"{len_quiet:.2f} meters. Estimated time: {quiet_time} minutes.")
    
    st.write(fast_noise)
    st.write(quiet_noise)

    user_prompt = f"""Fast route time: {fast_time} mins. Quiet route time: {quiet_time} mins.
        Average noise on fast route: {fast_noise} dB.
        Average noise on quiet route: {quiet_noise} dB.
        Main roads avoided: {', '.join(main_roads_avoided[:3])}."""
    
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=f"""You are a helpful walking assistant that provides a concise summary of the features of a quietness-optimised route through barcelona, 
        in contrast to the fastest route. The user has chosen {k_label} as their mode. 
        Focus on the noise levels, roads avoided, and time difference in your summary. 
        Note that decibels are logarithmic — their increase and decrease is not linear, so even a small difference in dB can have a significant impact on perceived noise.
        Avoid giving specific percentages and instead use qualitative language like "noticeably quieter" or "significantly reduced".
        Reflect this in your summary so the user understands the real impact of the noise difference.
        2 sentences maximum.""",
        messages=[
        {"role": "user", "content": user_prompt}
    ]
    )

    st.write(response.content[0].text)
    

    #Plotting routes using OSMNX's built-in plotting function, with custom colours and line widths for better visibility. Nodes are hidden for a cleaner look.
    fig, ax = ox.plot_graph_routes(G, [route_fast, route_quiet], 
                                route_colors=['r', 'g'], 
                                route_linewidth=4, node_size=0)
    st.pyplot(fig)