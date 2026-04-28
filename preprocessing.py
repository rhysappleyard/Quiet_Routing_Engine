""" THIS SCRIPT JOINS THE NOISE DATA TO THE OSM EDGES AND PROCESSES IT TO BE USED IN THE ROUTING ENGINE.
THE OUTPUT IS A PARQUET FILE WITH THE PROCESSED EDGES AND NOISE DATA, WHICH CAN BE QUICKLY LOADED 
IN THE ROUTING ENGINE WITHOUT HAVING TO REPEAT THE JOINING AND PROCESSING STEPS. """



import osmnx as ox
import geopandas as gpd
import pandas as pd


graph = ox.graph_from_place('Barcelona, Spain', network_type='walk')
edges = ox.graph_to_gdfs(graph, nodes=False)
gpkg = gpd.read_file('noise_data_2017.gpkg', layer='2017_Tramer_Mapa_Estrategic_Soroll_BCN')  #No recent noise data available so using most recent.

bcn_crs = "EPSG:25831"
gpkg = gpkg.to_crs(bcn_crs)
edges = edges.to_crs(bcn_crs)


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



edges['osmid'] = edges['osmid'].astype(str)
for col in edges.select_dtypes(include='object').columns:
    edges[col] = edges[col].astype(str)
edges.to_parquet("edges_preprocessed.parquet")


