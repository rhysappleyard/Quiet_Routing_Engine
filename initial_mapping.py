import osmnx as ox

# 1. Professional Caching (saves time/energy)

ox.settings.use_cache = True

# 2. Extract the 'Walk' network for St Albans

print("Scanning St Albans...")

G = ox.graph_from_place("Saint Albans, England", network_type="walk")

# 3. Unpack the edges (The Roads)

_, edges = ox.graph_to_gdfs(G)

# 4. Pull the 'Noise Profile'

# 5. Counts how many of each road type exist in your city

road_profile = edges['highway'].value_counts()

print("\n--- ST ALBANS ROAD TYPES ---")

print(road_profile)