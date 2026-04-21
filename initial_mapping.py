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
ox.settings.use_cache = False #Turning off caching in OSMNX to avoid issues with stale data during development. In a production environment, this should be turned on for performance.
ox.settings.requests_timeout = 300
client = anthropic.Anthropic()


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

    html, body, [class*="css"], .stMarkdown {
        font-family: 'Share Tech Mono', monospace;
    }
    
    /* Make headers look like terminal prompts */
    h1::before {
        content: "> ";
        color: #00FF41;
    }
    </style>
    """,
    unsafe_allow_html=True
)




def clean_location_input(raw_text):
    response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=50,
            system="""You must clean and standardise user input for geocoding.
            The user will input a location in Barcelona, but they may use different formats, abbreviations, or include extra information.
            Extract only the relevant location information and standardise it for geocoding.
            For example, if the user inputs "Parc Joan Miró, Barcelona", you should return "Parc Joan Miró, Barcelona". 
            If they input "Joan Miró Park near Plaça Espanya", you should return "Parc Joan Miró, Barcelona". 
            If they input "Plaça de Catalunya", you should return "Plaça de Catalunya, Barcelona".
            Return only the place name, no explanation, no punctuation other than commas, nothing else.
            If and only if the input is not a recognisable location for Barcelona, return only the word INVALID and nothing else.
            """,
            messages = [{"role": "user", "content": raw_text}])

    clean_text = response.content[0].text.strip()
    if clean_text == "INVALID":
        return None
    else: 
        return clean_text



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
        'noise_normalised': None,
        'edges_with_noise': None,
        'route_fast_edges': None,
        'mid_lat': 41.3851, # Default to Barcelona center
        'mid_lon': 2.1734, # Default to Barcelona center
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
    bcn_crs = "EPSG:25831" # UTM Zone 31N, used by OSMNX for Barcelona. Need to convert noise data to same CRS for spatial join.
    edges_with_noise = _edges.to_crs(bcn_crs)
    bbox = tuple(edges_with_noise.total_bounds)
    noise_gdf = gpd.read_file(gpkg_path, layer='2017_Tramer_Mapa_Estrategic_Soroll_BCN', bbox=bbox) #Reading only the relevant subset of noise data based on the bounding box of the graph edges, to save memory and speed up processing.


    # Coordinate Reference System (CRS) Alignment (Degrees vs Meters in different maps (OpenData BCN vs. OSMNX) need to be homogenised)
    noise_gdf = noise_gdf.to_crs(_edges.crs)
    
    noise_projected = noise_gdf.to_crs(bcn_crs)
    
    # Spatial Join (Snapping closest noise data to the streets)
    joined = gpd.sjoin_nearest(
        edges_with_noise, 
        noise_projected, 
        how="left", 
        distance_col="dist" 
    )

    joined = joined[~joined.index.duplicated(keep='first')] 

    
    # Converting column to floats as they're stored as strings in the GeoPackage. Added regex to find upper bound of noise range, and take only two digits.
    noise_values = pd.to_numeric(joined[noise_column].str.extract(r"- (\d+)")[0], errors='coerce').fillna(75) # Assuming 75 dB for streets without noise data, which is a conservative estimate to avoid false positives.


    edges_with_noise = _edges.copy()#Creating copy of the original edges GeoDataFrame to avoid modifying it directly, which can cause issues with caching and data integrity in Streamlit.
    edges_with_noise['noise_values'] = noise_values
    noise_normalised = (noise_values - noise_values.min()) / (noise_values.max() - noise_values.min()) # Normalising values between 0 and 1. 
    noise_normalised = noise_normalised.clip(0, 1) #Ensuring normalisation doesn't produce outliers. 

    return noise_normalised, noise_values, edges_with_noise





# ------------ ROUTE MAPPING, VISUALISATION AND SUMMARY ------------- #Button block is separated to allow for faster iterations on routing. 
if st.sidebar.button("Find route"): 
    with st.spinner("Calculating routes..."):
        if not start_input or not end_input:
            st.error("Please enter a starting point and a destination.")
            st.stop() #Stop the app if either input is missing, to avoid errors in geocoding.
        clean_start_input = clean_location_input(start_input)
        if clean_start_input is None:
            st.error("Couldn't understand starting point. Please check your input and try again.")
            st.stop()
        try: 
            start_point = ox.geocoder.geocode(clean_start_input)
        except Exception as e:
            st.error(f"Couldn't geocode starting point: {e}")
            st.stop()
        if start_point is None:
            st.error("Couldn't geocode starting point. Please check your input and try again.")
            st.stop()
        clean_end_input = clean_location_input(end_input)
        if clean_end_input is None:
            st.error("Couldn't understand destination. Please check your input and try again.")
            st.stop()
        try:
            end_point = ox.geocoder.geocode(clean_end_input)
        except Exception as e:
            st.error(f"Couldn't geocode destination: {e}")
            st.stop()
        if end_point is None:
            st.error("Couldn't geocode destination. Please check your input and try again.")
            st.stop()


        st.info("Loading graph data for the specified area...")
        st.session_state.mid_lat = (start_point[0] + end_point[0]) / 2
        st.session_state.mid_lon = (start_point[1] + end_point[1]) / 2

        distance = ox.distance.great_circle(start_point[0], start_point[1], end_point[0], end_point[1])

        st.write(f"Distance: {distance:.0f}m, Graph dist: {int(distance/2)+500}m")
        G, edges = load_graph(f"{st.session_state.mid_lat},{st.session_state.mid_lon}", dist=int(distance/2) + 500)

        
        

        st.session_state.G = G
        st.session_state.edges = edges
        st.session_state.noise_normalised, noise_values, st.session_state.edges_with_noise = map_data_join(edges, gpkg_path, noise_column) #Joining normalised noise data to the edges
        st.session_state.orig = ox.distance.nearest_nodes(st.session_state.G, X=start_point[1], Y=start_point[0])    
        st.session_state.dest = ox.distance.nearest_nodes(st.session_state.G, X=end_point[1], Y=end_point[0])
        st.session_state.route_fast = ox.shortest_path(st.session_state.G, st.session_state.orig, st.session_state.dest, weight='length')
        st.session_state.route_fast_edges = ox.routing.route_to_gdf(st.session_state.G, st.session_state.route_fast)


    

if st.session_state.orig is not None:

    if st.session_state.noise_normalised is not None:
        penalty = ((1 + st.session_state.noise_normalised) ** k) - 1
        weighted_costs = st.session_state.edges['length'] * (1 + penalty) #Applying the noise constraints to the edges, with the selected k value.
        weighted_costs = weighted_costs.fillna(1e-6).clip(lower=1e-6) # Avoiding NaN, zero or negative weights which can mess with Dijkstra's algorithm. 
        nx.set_edge_attributes(st.session_state.G, weighted_costs.to_dict(), 'weighted_costs') # Push scores back to the graph
        st.session_state.edges['weighted_costs'] = weighted_costs #Converting back to a Series of values to push back to the graph. 


    route_quiet = ox.shortest_path(st.session_state.G, st.session_state.orig, st.session_state.dest, weight='weighted_costs')
    route_quiet_edges = ox.routing.route_to_gdf(st.session_state.G, route_quiet)

    route_fast_gdf = st.session_state.route_fast_edges.to_crs("EPSG:4326")
    route_quiet_gdf = route_quiet_edges.to_crs("EPSG:4326")
    
    #Finding which roads are in the quiet but not in the fast route.
    quiet_road_names = route_quiet_edges['name'].explode().unique().tolist()
    fast_road_names = st.session_state.route_fast_edges['name'].explode().unique().tolist()
    main_roads_avoided = [road for road in fast_road_names if road not in quiet_road_names and road is not None and isinstance(road, str)] #Some roads have no names.
    
    fast_noise = st.session_state.edges_with_noise.loc[st.session_state.route_fast_edges.index, 'noise_values'].mean().round() 
    mask = st.session_state.edges_with_noise.index.isin(st.session_state.route_fast_edges.index)
    fast_noise = st.session_state.edges_with_noise.loc[mask, 'noise_values'].mean().round()
    quiet_noise = st.session_state.edges_with_noise.loc[route_quiet_edges.index, 'noise_values'].mean().round()
    mask = st.session_state.edges_with_noise.index.isin(route_quiet_edges.index)
    quiet_noise = st.session_state.edges_with_noise.loc[mask, 'noise_values'].mean().round()


    len_fast = st.session_state.route_fast_edges['length'].sum()
    len_quiet = route_quiet_edges['length'].sum()

    fast_time = (len_fast / 1000 * 12).round() #Assuming an average walking speed of 5 km/h, which is 12 minutes per km. This is a simplification and could be improved by using more granular speed data based on road type, slope, etc.
    quiet_time = (len_quiet / 1000 * 12).round()

    st.metric(label="Fast Route", value=f"{len_fast/1000:.1f} km. Estimated time: {int(fast_time)} minutes")
    st.metric(label=f"Quiet Route, {k_label} mode", value=f"{len_quiet/1000:.1f} km. Estimated time: {int(quiet_time)} minutes.")
 
    # ----------------------- Generating LLM Summary of Route Differences ---------------------- Summary before map as enhances UX. 

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
            If and only if {k_label} is "Serene" and the difference between fast and quiet route noise is less than 2dB, 
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

# -------------- Plotting routes using Folium for interactive map ------------------
   
    m = folium.Map(location=[st.session_state.mid_lat, st.session_state.mid_lon], zoom_start=15, tiles="cartodbpositron")
    folium.GeoJson(route_fast_gdf, name="Fast Route", style_function=lambda x: {'color': 'red', 'weight': 4, 'opacity': 0.7}).add_to(m)
    folium.GeoJson(route_quiet_gdf, name="Quiet Route", style_function=lambda x: {'color': 'green', 'weight': 5, 'opacity': 0.9}).add_to(m)
    folium.LayerControl().add_to(m)
    st_folium(m, width=700, height=500, returned_objects=[]) #returned objects means we don't have to process user interactions with the map. 

    


   