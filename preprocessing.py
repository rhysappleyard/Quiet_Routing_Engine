""" THIS SCRIPT JOINS THE NOISE DATA TO THE OSM EDGES AND PROCESSES IT TO BE USED IN THE ROUTING ENGINE.
THE OUTPUTS ARE THE MAP OF BARCELONA, AND A PARQUET FILE WITH THE PROCESSED EDGES AND NOISE DATA, WHICH CAN BE QUICKLY LOADED 
IN THE ROUTING ENGINE WITHOUT HAVING TO REPEAT THE JOINING AND PROCESSING STEPS. """


import osmnx as ox
import networkx as nx
import geopandas as gpd
import pandas as pd

# Define constants
CENTER = (41.3874, 2.1686) # Central Barcelona
DIST = 4000               # 5km radius to cover the whole city
BCN_CRS = "EPSG:25831"    # Meters for noise join
MAP_CRS = "EPSG:4326"     # Degrees for the App/Geocoder

# 1. Download the new, larger graph
print("Downloading graph...")
G = ox.graph_from_point(CENTER, dist=DIST, network_type='walk')

# 2. Project to meters to join noise data accurately
G_proj = ox.project_graph(G, to_crs=BCN_CRS)

edges = ox.graph_to_gdfs(G_proj, nodes=False)
gpkg = gpd.read_file('data/noise_data_2017.gpkg', layer='2017_Tramer_Mapa_Estrategic_Soroll_BCN')  #Currently not got more recent noise data available, so using most recent.
gpkg = gpkg.to_crs(BCN_CRS)
edges = edges.to_crs(BCN_CRS)


joined = gpd.sjoin_nearest(
    edges,
    gpkg,
    how="left",
    distance_col="dist"
)

joined = joined[~joined.index.duplicated(keep='first')]

day_values = pd.to_numeric(joined['TOTAL_D'].str.extract(r"- (\d+)")[0], errors='coerce').fillna(75)
eve_values = pd.to_numeric(joined['TOTAL_E'].str.extract(r"- (\d+)")[0], errors='coerce').fillna(75)
night_values = pd.to_numeric(joined['TOTAL_N'].str.extract(r"- (\d+)")[0], errors='coerce').fillna(75)


edges["TOTAL_D"] = day_values
edges["TOTAL_E"] = eve_values
edges["TOTAL_N"] = night_values

nx.set_edge_attributes(G_proj, edges['TOTAL_D'].to_dict(), 'TOTAL_D')
nx.set_edge_attributes(G_proj, edges['TOTAL_E'].to_dict(), 'TOTAL_E')
nx.set_edge_attributes(G_proj, edges['TOTAL_N'].to_dict(), 'TOTAL_N')

edges['osmid'] = edges['osmid'].astype(str)
for col in edges.select_dtypes(include='object').columns:
    edges[col] = edges[col].astype(str)
edges.to_parquet("edges_preprocessed_v2.parquet")

graph_final = ox.project_graph(G_proj, to_crs=MAP_CRS)
ox.save_graphml(graph_final, "data/barcelona_walk_v2.graphml")
