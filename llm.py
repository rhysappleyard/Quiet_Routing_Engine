import anthropic

client = anthropic.Anthropic() 

def clean_location_input(raw_text):
    response = client.messages.create(
            model="claude-sonnet-4-6",
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



def generate_route_summary(fast_time, quiet_time, fast_noise, quiet_noise, main_roads_avoided, k_label):
    user_prompt = f"""Fast route time: {fast_time} mins. Quiet route time: {quiet_time} mins.
                Average noise on fast route: {fast_noise} dB.
                Average noise on quiet route: {quiet_noise} dB.
                Main roads avoided: {', '.join(main_roads_avoided[:3])}.""" #Limiting to 3 main roads avoided for brevity. 
            
    response = client.messages.create(
    model="claude-sonnet-4-6",
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
    ])
    summary = response.content[0].text.strip()
    return summary
