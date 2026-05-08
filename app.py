from routing import get_noise_column, normalise, apply_penalty, find_quiet_route
from llm import clean_location_input, generate_route_summary
import geopandas as gpd
import streamlit as st
import osmnx as ox
import folium
from streamlit_folium import st_folium


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

ox.settings.use_cache = False #Turning off caching in OSMNX to avoid issues with stale data during development.

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
        'mid_lat': 41.3874, #Defaulting to central Barcelona coordinates for map centering before user input.
        'mid_lon': 2.1686, 
        'noise_normalised': None,
        'route_fast_edges': None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
init_session_state()

BCN_CRS = "EPSG:25831" #Setting Coordinate Reference System for Barcelona, which is used in the noise data. OSM edges are converted to BCN_CRS for the spatial join, 
MAP_CRS = "EPSG:4326"  #and then convert back to WGS84 (EPSG:4326) for mapping and routing.



@st.cache_resource(show_spinner="Loading Barcelona street network (88MB)...")
def load_graph():
    # Load the local GraphML file for Barcelona.
    G = ox.load_graphml("data/barcelona_walk_v2.graphml") 
    # Convert to GeoDataFrame once and cache
    _, edges = ox.graph_to_gdfs(G)
    return G, edges


G_GLOBAL, EDGES_GLOBAL = load_graph() #Loading the graph globally to avoid repeated loading during development.

noise_column = get_noise_column() 

@st.cache_data(show_spinner="Loading noise datasets (14MB)...")
def load_preprocessed():
    return gpd.read_parquet("data/edges_preprocessed_v2.parquet")

edges_preprocessed = load_preprocessed()

st.title("Barcelona Quiet Route Finder")

st.sidebar.header("Route Preferences")



start_input = st.sidebar.text_input("Enter your starting point (e.g., 'Plaça de Catalunya, Barcelona'):", placeholder="Parc Joan Miró, Barcelona")
end_input = st.sidebar.text_input("Enter your destination (e.g., 'Sagrada Família, Barcelona'):", placeholder="Sagrada Família, Barcelona")

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


# ------------ ROUTE MAPPING, VISUALISATION AND SUMMARY ------------- #Button block is separated to allow for faster iterations on routing. 

pb = st.empty() #allows for switching slider without placeholder error

if st.sidebar.button("Find route"):
    if not start_input or not end_input:
        st.sidebar.error("Please enter both a start and destination.")
    else:
        pb.progress(0)
        with st.status("Analysing Barcelona noise levels...", expanded=True) as status:
            clean_start_input = clean_location_input(start_input)
            if clean_start_input is None: #LLM returns None if it can't understand the input, which is handled here.
                pb.empty()
                st.error("Couldn't understand starting point. Please check your input and try again.")
                status.update(state="error")
                st.stop()
            try: 
                start_point = ox.geocoder.geocode(clean_start_input)
            except Exception as e:
                pb.empty()
                st.error(f"Couldn't geocode starting point: {e}")
                status.update(state="error")
                st.stop()
            if start_point is None:
                pb.empty()
                st.error("Couldn't geocode starting point. Please check your input and try again.")
                status.update(state="error")
                st.stop()
            clean_end_input = clean_location_input(end_input)
            if clean_end_input is None:
                pb.empty()
                st.error("Couldn't understand destination. Please check your input and try again.")
                status.update(state="error")
                st.stop()
            try:
                end_point = ox.geocoder.geocode(clean_end_input)
            except Exception as e:
                pb.empty()
                st.error(f"Couldn't geocode destination: {e}")
                status.update(state="error")
                st.stop()
            if end_point is None:
                pb.empty()
                st.error("Couldn't geocode destination. Please check your input and try again.")
                status.update(state="error")
                st.stop()
            pb.progress(20)


            st.session_state.mid_lat = (start_point[0] + end_point[0]) / 2
            st.session_state.mid_lon = (start_point[1] + end_point[1]) / 2

            st.session_state.G = G_GLOBAL
            st.session_state.edges = EDGES_GLOBAL


            mask = edges_preprocessed.index.isin(EDGES_GLOBAL.index)
            noise_normalised = normalise(edges_preprocessed.loc[mask, noise_column]).reindex(EDGES_GLOBAL.index)

            st.session_state.noise_normalised = noise_normalised
            st.session_state.orig = ox.distance.nearest_nodes(st.session_state.G, X=start_point[1], Y=start_point[0])    
            st.session_state.dest = ox.distance.nearest_nodes(st.session_state.G, X=end_point[1], Y=end_point[0])
            st.session_state.route_fast = ox.shortest_path(st.session_state.G, st.session_state.orig, st.session_state.dest, weight='length')
            st.session_state.route_fast_edges = ox.routing.route_to_gdf(st.session_state.G, st.session_state.route_fast)

            status.update(label="Locations Found", state="complete", expanded=False)
            pb.progress(40)


st.sidebar.markdown("<br>" * 8, unsafe_allow_html=True)
with st.sidebar.expander("About this app"):
    st.caption(
        """
        This app helps you find quieter walking routes in Barcelona by applying noise penalties to the road network.""")
    st.caption("""**Data sources:** OpenStreetMap, Barcelona noise dataset (Ajuntament de Barcelona).
               See https://coneixement-eu.bcn.cat/widget/atles-viewer-eng/index.html?map=ar_er_cac_nsoroll for a visualisation of the noise data.""")
    st.caption("Developed by Rhys Appleyard 2026.")

    

if st.session_state.orig is not None:
    with st.spinner("Applying noise penalties to roads..."):
        if st.session_state.noise_normalised is not None:
            weighted_costs = apply_penalty(st.session_state.edges, k, st.session_state.noise_normalised)
            st.session_state.weighted_costs = weighted_costs
        else:
            st.error("Noise data not available. Cannot calculate quiet route.")
            st.stop()   
        

    with st.spinner("Optimising quiet route..."):
        route_quiet = find_quiet_route(st.session_state.G, st.session_state.orig, st.session_state.dest, weighted_costs)
        route_quiet_edges = ox.routing.route_to_gdf(st.session_state.G, route_quiet)

        route_fast_gdf = st.session_state.route_fast_edges.to_crs(MAP_CRS)
        route_quiet_gdf = route_quiet_edges.to_crs(MAP_CRS)
        
        pb.progress(60)
        #Finding which roads are in the quiet but not in the fast route.
        quiet_road_names = route_quiet_edges['name'].explode().unique().tolist()
        fast_road_names = st.session_state.route_fast_edges['name'].explode().unique().tolist()
        main_roads_avoided = [road for road in fast_road_names if road not in quiet_road_names and road is not None and isinstance(road, str)] #Some roads have no names.
        
        mask = edges_preprocessed.index.isin(st.session_state.route_fast_edges.index)
        fast_noise = edges_preprocessed.loc[mask, noise_column].mean().round()

        mask = edges_preprocessed.index.isin(route_quiet_edges.index)
        quiet_noise = edges_preprocessed.loc[mask, noise_column].mean().round()


        len_fast = st.session_state.route_fast_edges['length'].sum()
        len_quiet = route_quiet_edges['length'].sum()

        fast_time = (len_fast / 1000 * 12).round() #Assuming an average walking speed of 5 km/h, which is 12 minutes per km. This is a simplification and could be improved by using more granular speed data based on road type, slope, etc.
        quiet_time = (len_quiet / 1000 * 12).round()

        
        col1, col2 = st.columns(2)
        with col1:
            st.metric(label="Fast Route", value=f"{len_fast/1000:.1f} km", delta=f"{int(fast_time)} mins", delta_color="inverse")
        with col2:
            st.metric(label=f"Quiet ({k_label})", value=f"{len_quiet/1000:.1f} km", delta=f"{int(quiet_time)} mins")
        
        
    




# ----------------------- Generating LLM Summary of Route Differences ---------------------- Summary before map as enhances UX. 

    if st.session_state.last_k_label != k_label or st.session_state.last_route != route_quiet: #Only call the LLM if the user has changed their preference or the quiet route. 
        # calling LLM for summary
        with st.spinner("Generating route summary..."):
            st.session_state.summary = generate_route_summary(fast_noise=fast_noise, quiet_noise=quiet_noise, fast_time=fast_time, quiet_time=quiet_time, main_roads_avoided=main_roads_avoided, k_label=k_label)
            st.session_state.last_k_label = k_label
            st.session_state.last_route = route_quiet
    st.write(st.session_state.summary)
    pb.progress(80)



# -------------- Plotting routes using Folium for interactive map ------------------
   
    m = folium.Map(location=[st.session_state.mid_lat, st.session_state.mid_lon], zoom_start=15, tiles="cartodbpositron")
    folium.GeoJson(route_fast_gdf, name="Fast Route", style_function=lambda x: {'color': 'red', 'weight': 4, 'opacity': 0.7}).add_to(m)
    folium.GeoJson(route_quiet_gdf, name="Quiet Route", style_function=lambda x: {'color': 'green', 'weight': 5, 'opacity': 0.9}).add_to(m)
    folium.LayerControl().add_to(m)
    st_folium(m, width=700, height=500, returned_objects=[]) #returned objects means we don't have to process user interactions with the map. 

    pb.progress(100)
    pb.empty()