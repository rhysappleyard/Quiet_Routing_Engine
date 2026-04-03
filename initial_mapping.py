import osmnx as ox
import networkx as nx
import geopandas as gpd
import pandas as pd
from datetime import datetime
import pytz
import streamlit as st
import anthropic
import folium 
from streamlit_folium import st_folium

# ------------------------ SETTINGS ------------------------
ox.settings.use_cache = True
ox.settings.requests_timeout = 300
client = anthropic.Anthropic()

# ------------------------ Streamlit Session State Initialisation ------------------------
def init_session_state():#Turned into a function as had too many variables to initialise. 
    defaults = {
        'route_fast': None,
        'orig': None,
        'dest': None,
        'last_k_label': None,
        'summary': None,
        'last_route': None,
        'G': None,
        'edges': None,
        'edges_projected': None,
        'noise_normalised': None,
        'route_fast_edges': None,
        'mid_lat': 41.3851, # Default to Barcelona center
        'mid_lon': 2.1734, # Default to Barcelona center
        'north': None,
        'south': None,
        'east': None,
        'west': None
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
init_session_state()


# ------------------------ Extracting the 'Walk' network for BCN ------------------------
gpkg_path = "noise_data_2017.gpkg"

# ------------------------------ USER INPUT FOR START AND END POINTS ------------------------------
start_input = st.sidebar.text_input("Enter your starting point (e.g., 'Plaça de Catalunya, Barcelona'):", placeholder="Parc Joan Miró, Barcelona")
end_input = st.sidebar.text_input("Enter your destination (e.g., 'Sagrada Família, Barcelona'):", placeholder="Sagrada Família, Barcelona")


@st.cache_data
def load_graph(address, dist):
    G = ox.graph_from_address(address, dist=dist, network_type="walk", simplify=True)
    _, edges = ox.graph_to_gdfs(G) #leaving nodes blank (hence the underscore) as we are working with roads, not intersections.
    return G, edges


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
k_label = st.sidebar.select_slider(    #Tuning parameter to adjust influence of noise on the overall cost. 
        'Walking preference',
        options=['Efficient', 'Balanced', 'Quiet', 'Serene'],
        value='Balanced'    #Hardcoded starting point
    )
mapping = {
        'Efficient': 0.5,
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
    penalty = ((1 + noise_normalised) ** k) - 1 #decibels are logarithmic, so we apply the exponent to reflect the non-linear increase in perceived noise. 
    return edges_projected['length'] * (1+penalty) #Adding 1 to ensure the cost is always positive


if st.session_state.noise_normalised is not None:
    weighted_cost = set_noise_constraints(st.session_state.noise_normalised, st.session_state.edges_projected, k) #Applying the noise constraints to the edges, with the selected k value.
    # ------------------------------------ Routing Comparison ------------------------------------
    st.session_state.edges['weighted_cost'] = weighted_cost  #Converting back to a Series of values to push back to the graph. 
    weights_dict = st.session_state.edges['weighted_cost'].to_dict() #Precautionary step to ensure weights are formatted such that they can be pushed back easily to graph.
    nx.set_edge_attributes(st.session_state.G, weights_dict, 'weighted_cost') # Push scores back to the graph


# ------------ ROUTE MAPPING, VISUALISATION AND SUMMARY ------------- #Button block is separated to allow for faster iterations on routing. 
if st.sidebar.button("Find route"): 
    with st.spinner("Calculating routes..."):
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


        st.info("Loading graph data for the specified area...")#Better UX
        st.session_state.mid_lat = (start_point[0] + end_point[0]) / 2
        st.session_state.mid_lon = (start_point[1] + end_point[1]) / 2

        distance = ox.distance.great_circle(start_point[0], start_point[1], end_point[0], end_point[1])
        G, edges = load_graph(f"{st.session_state.mid_lat},{st.session_state.mid_lon}", dist=int(distance/2) + 500)
        

        

        st.session_state.G = G
        st.session_state.edges = edges
        st.session_state.noise_normalised, st.session_state.edges_projected = map_data_join(edges, gpkg_path, noise_column) #Joining normalised noise data to the edges

        st.session_state.orig = ox.distance.nearest_nodes(st.session_state.G, X=start_point[1], Y=start_point[0])    
        st.session_state.dest = ox.distance.nearest_nodes(st.session_state.G, X=end_point[1], Y=end_point[0])
        st.session_state.route_fast = ox.shortest_path(st.session_state.G, st.session_state.orig, st.session_state.dest, weight='length')
        st.session_state.route_fast_edges = ox.routing.route_to_gdf(st.session_state.G, st.session_state.route_fast)

if st.session_state.orig is not None:
    route_quiet = ox.shortest_path(st.session_state.G, st.session_state.orig, st.session_state.dest, weight='weighted_cost')
    route_quiet_edges = ox.routing.route_to_gdf(st.session_state.G, route_quiet)

    route_fast_gdf = ox.routing.route_to_gdf(st.session_state.G, st.session_state.route_fast).to_crs("EPSG:4326") #Folium needs lat/lon coordinates, so we convert the CRS back to EPSG:4326 -
    route_quiet_gdf = ox.routing.route_to_gdf(st.session_state.G, route_quiet).to_crs("EPSG:4326")                #for both routes to ensure they are in the same format for plotting.


    
    #Finding which roads are in the quiet but not in the fast route.
    quiet_road_names = route_quiet_edges['name'].explode().unique().tolist()
    fast_road_names = st.session_state.route_fast_edges['name'].explode().unique().tolist()
    main_roads_avoided = [road for road in fast_road_names if road not in quiet_road_names and road is not None]
    
    fast_noise = st.session_state.edges_projected.loc[st.session_state.route_fast_edges.index, 'noise_values'].mean().round() 
    quiet_noise = st.session_state.edges_projected.loc[route_quiet_edges.index, 'noise_values'].mean().round()


    len_fast = st.session_state.route_fast_edges['length'].sum()
    len_quiet = route_quiet_edges['length'].sum()

    fast_time = (len_fast / 1000 * 12).round() #Assuming an average walking speed of 5 km/h, which is 12 minutes per km. This is a simplification and could be improved by using more granular speed data based on road type, slope, etc.
    quiet_time = (len_quiet / 1000 * 12).round()

    st.metric(label="Fast Route", value=f"{len_fast/1000:.1f} km. Estimated time: {int(fast_time)} minutes")
    st.metric(label=f"Quiet Route, {k_label} mode", value=f"{len_quiet/1000:.1f} km. Estimated time: {int(quiet_time)} minutes.")
 
# -------------- Plotting routes using Folium for interactive map ------------------
   
    m = folium.Map(location=[st.session_state.mid_lat, st.session_state.mid_lon], zoom_start=15, tiles="cartodbpositron")
    m.fit_bounds(route_fast_gdf.total_bounds[[1,0,3,2]].tolist()) #fit map to bounds of route. Reordered indices because of how fit_bounds expects them (southwest, northeast) and how total_bounds outputs them (minx, miny, maxx, maxy).
    folium.GeoJson(st.session_state.route_fast_edges, name="Fast Route", style_function=lambda x: {'color': 'red', 'weight': 4, 'opacity': 0.7}).add_to(m)
    folium.GeoJson(route_quiet_edges, name="Quiet Route", style_function=lambda x: {'color': 'green', 'weight': 5, 'opacity': 0.9}).add_to(m)
    folium.LayerControl().add_to(m)
    st_folium(m, width=700, height=500, returned_objects=[]) #returned objects means we don't have to process user interactions with the map. 

    if st.session_state.last_k_label != k_label or st.session_state.last_route != route_quiet: #Only call the LLM if the user has changed their preference or the quiet route. 
        # calling LLM for summary
        user_prompt = f"""Fast route time: {fast_time} mins. Quiet route time: {quiet_time} mins.
            Average noise on fast route: {fast_noise} dB.
            Average noise on quiet route: {quiet_noise} dB.
            Main roads avoided: {', '.join(main_roads_avoided[:3])}.""" #Limiting to 3 main roads avoided for brevity. 
        
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=f"""You are a helpful walking assistant that provides a concise summary of the features of a quietness-optimised route through Barcelona, 
            in contrast to the fastest route. The user has chosen {k_label} as their mode. 
            Focus on the noise levels, roads avoided, and time difference in your summary. 
            Note that decibels are logarithmic — their increase and decrease is not linear: 
            Even a small difference in dB can have a significant impact on perceived noise. 
            If {k_label} is "Serene" and the difference between fast and quiet route noise is less than 2dB, 
            note that this area of Barcelona is uniformly loud and even the quietest available route has limited noise reduction.
            Avoid giving specific percentages and instead use qualitative language like "noticeably quieter" or "significantly reduced".
            Reflect this in your summary so the user understands the real impact of the noise difference.
            2 sentences maximum.""",
            messages=[
            {"role": "user", "content": user_prompt}
        ]
        )
        st.session_state.summary = response.content[0].text
        st.session_state.last_k_label = k_label
        st.session_state.last_route = route_quiet
    st.write(st.session_state.summary)


   