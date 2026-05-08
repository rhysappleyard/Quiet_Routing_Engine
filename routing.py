import pytz
from datetime import datetime
import geopandas as gpd
import osmnx as ox
import networkx as nx



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

def normalise(series):
    return ((series - series.min()) / (series.max() - series.min())).clip(0, 1)

def format_time(minutes):
    hours = minutes // 60
    mins = minutes % 60
    
    if mins == 0:
        return f"{hours}h"
    else:
        return f"{hours}h{mins}m"



def apply_penalty(edges, k, noise_normalised):
    penalty = ((1 + noise_normalised) ** k) - 1
    weighted_costs = edges['length'] * (1 + penalty) #Applying the noise constraints to the edges, with the selected k value.
    weighted_costs = weighted_costs.fillna(1e-6).clip(lower=1e-6) # Avoiding NaN, zero or negative weights which can mess with Dijkstra's algorithm. 
    return weighted_costs


def find_quiet_route(G, orig, dest, weighted_costs):
    nx.set_edge_attributes(G, weighted_costs.to_dict(), 'weighted_costs')
    quiet_route = ox.shortest_path(G, orig, dest, weight='weighted_costs')
    return quiet_route

