import osmnx as ox

# Load your new graph
G = ox.load_graphml("data/barcelona_walk_v2.graphml")

# Plot it to see the coverage
fig, ax = ox.plot_graph(G, node_size=0, edge_color='w', bgcolor='k')