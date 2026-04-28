# Quiet_Routing_Engine - An app for finding the quietest feasible route through a city.
Starting with Barcelona - I am using real reported data from OpenData BCN, having initially tried road categories as a proxy for noise data. 
The app completes a spatial join of the noise data available with an OSMNX graph, whose size is determined based on user input. 
It then maps the fastest route and the quietest route available, and the LLM returns a summary of the route based on data generated in the script. 


## Tech Stack 
- Python, osmnx, networkx, geopandas — graph construction and spatial analysis
- Streamlit + Folium — interactive UI and map visualisation
- Anthropic API (Claude) — user input cleaning and natural language route summary 
- OpenData BCN — real noise pollution data (GeoPackage)


## How to run it
Live demo available at quietroutingengine.streamlit.app  
Please note this is currently a work in progress, and there are caching and data joining issues which I'm currently solving. 

## Methodology 
Noise penalties are applied exponentially via a tunable parameter k, reflecting the logarithmic nature of decibel perception.





