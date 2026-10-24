"""Clean display names of official records (STB attractions, NParks parks).

Used by add_overture_to_catalogue.py. Source records are not changed; the old name stays
as an alias so searching by it still works.
"""
from __future__ import annotations

import re

# STB attraction titles: web-page titles with SEO suffixes and broken characters (â„¢, Â®).
STB_TITLES = {
    'Adventure Cove Waterparkâ„¢ Singapore': 'Adventure Cove Waterpark',
    'Armenian Church in Singapore': 'Armenian Church',
    'ArtScience Museum Singapore': 'ArtScience Museum',
    'Asian Civilisations Museum & Singapore Museum': 'Asian Civilisations Museum',
    'Bras Basah.Bugis, Singapore Cultural District': 'Bras Basah.Bugis Cultural District',
    'Buddha Tooth Relic Temple & Museum in Singapore': 'Buddha Tooth Relic Temple and Museum',
    'CHIJMES Singapore': 'CHIJMES',
    'Cable Car Singapore & Mount Faber': 'Singapore Cable Car (Mount Faber)',
    'Capitol Building Singapore': 'Capitol Building',
    'Changi Museum Singapore': 'Changi Chapel and Museum',
    'Chinatown Heritage Centre, Singapore': 'Chinatown Heritage Centre',
    'Chinatown Singapore: History & Culture': 'Chinatown',
    'Chinese Garden Singapore': 'Chinese Garden',
    'Chinese Heritage Centre, Singapore': 'Chinese Heritage Centre',
    'Civic District, Singapore': 'Civic District',
    'Dalhousie Obelisk, a Singapore Landmark': 'Dalhousie Obelisk',
    'East Coast Park Singapore': 'East Coast Park',
    'Eurasian Heritage Centre: Singapore Attraction': 'Eurasian Heritage Gallery',
    'Gillman Barracks – YourSingapore': 'Gillman Barracks',
    'HarbourFront, Singapore': 'HarbourFront',
    'Haw Par Villa: Singapore Theme Park': 'Haw Par Villa',
    'Hong San See Temple Singapore': 'Hong San See Temple',
    'Indian National Army (INA) Monument in Singapore': 'Indian National Army (INA) Monument',
    'Institute of Contemporary Arts Singapore': 'Institute of Contemporary Arts Singapore',
    'Jamae Mosque (Masjid Chulia) Singapore': 'Jamae Mosque (Masjid Chulia)',
    'Joo Chiat/Katong: Singapore Peranakan Neighbourhood': 'Joo Chiat / Katong',
    'Jurong Bird Park, Singapore: Attractions & Things to Do': 'Jurong Bird Park（旧址）',
    'Kampong Glam & Malay Culture in Singapore': 'Kampong Glam',
    'Katong Antique House Singapore: Peranakan Heritage': 'Katong Antique House',
    'Kranji War Memorial Landmark in Singapore': 'Kranji War Memorial',
    'Kusu Island, Singapore': 'Kusu Island',
    'Lau Pa Sat Singapore': 'Lau Pa Sat',
    'Little India Singapore: History & Culture': 'Little India',
    'MINT Museum of Toys Singapore & Toy Museum': 'MINT Museum of Toys',
    'MacRitchie Singapore & Singapore Nature Reserve': 'MacRitchie Nature Trail',
    'Malay Heritage Centre, Singapore': 'Malay Heritage Centre',
    'Marina Bay Sands, Singapore': 'Marina Bay Sands',
    'Marina Bay SandsÂ®': 'Marina Bay Sands (Shoppes)',
    'Marina Bay SandsÂ® SkyPark': 'Marina Bay Sands SkyPark',
    'Marina Bay Singapore: Attractions & Things to do': 'Marina Bay',
    'Merlion Park, Singapore: Attractions & Things to Do': 'Merlion Park',
    'NUS Baba House - Singapore Peranakan Architecture': 'NUS Baba House',
    'NUS University Museum, Singapore': 'NUS Museum',
    'National Design Centre, Singapore': 'National Design Centre',
    'Night Safari, Singapore': 'Night Safari',
    'Old Ford Factory Singapore Museum': 'Former Ford Factory',
    'Old Supreme Court Singapore': 'Old Supreme Court',
    "Orchard Road, Singapore: Asia's Most Famous Shopping Street": 'Orchard Road',
    'Peranakan Museum Singapore: History & Culture': 'Peranakan Museum',
    'Pulau Ubin & Singapore Islands': 'Pulau Ubin',
    'Raffles Singapore': 'Raffles Hotel',
    'Raffles Statue – Sir Stamford Raffles Landing Site': "Raffles' Landing Site",
    'Red Dot Design Museum, Singapore': 'Red Dot Design Museum',
    'Reflections at Bukit Chandu: Singapore War Memorial': 'Reflections at Bukit Chandu',
    'Resorts World Sentosa, Singapore': 'Resorts World Sentosa',
    'River Safari Singapore': 'River Safari（已改名 River Wonders）',
    'SAM at 8Q & Singapore Art Museum': 'SAM at 8Q',
    'SIT Flats Singapore': 'SIT Flats',
    'Sentosa Island, Singapore: Leisure & Attractions': 'Sentosa Island',
    'Singapore Coins and Notes Museum & Museum Singapore': 'Singapore Coins and Notes Museum',
    'Singapore Garden & Singapore Park': 'Singapore Gardens and Parks',
    'Singapore River: History & Culture Tours': 'Singapore River',
    'Singapore Science Centre': 'Science Centre Singapore',
    'Singapore Zoo: Attractions & Things to Do': 'Singapore Zoo',
    'Sri Mariamman Temple: Hindu Temple in Singapore': 'Sri Mariamman Temple',
    'Sri Veeramakaliamman Temple: Hindu Temple in Singapore': 'Sri Veeramakaliamman Temple',
    'St Andrew’s Cathedral- Singapore Architecture Landmark': "St Andrew's Cathedral",
    'Sultan Mosque (Masjid Sultan) Singapore': 'Sultan Mosque (Masjid Sultan)',
    'The Cenotaph - a Singapore War Memorial Landmark': 'The Cenotaph',
    'The Civilian War Memorial park in Singapore': 'Civilian War Memorial',
    'The Substation, Singapore Contemporary Art Centre': 'The Substation',
    'Thian Hock Keng Temple, Singapore': 'Thian Hock Keng Temple',
    'Universal Studios Singaporeâ„¢ Theme Park': 'Universal Studios Singapore',
    'Victoria Theatre Singapore': 'Victoria Theatre',
}

# NParks names: upper case with abbreviations ("MACRITCHIE RESERVOIR PK", "ALMOND CRESCENT PG").
ABBREV = {
    'PG': 'Playground', 'PK': 'Park', 'RD': 'Road', 'JLN': 'Jalan', 'AVE': 'Avenue', 'OS': 'Open Space',
    'DR': 'Drive', 'FC': 'Fitness Corner', 'ST': 'Street', 'LN': 'Lane', 'LOR': 'Lorong', 'GDNS': 'Gardens',
    'GDN': 'Garden', 'PT': 'Point', 'TER': 'Terminal', 'BC': 'Beach', 'UPP': 'Upper', 'EC': 'East Coast',
    'SG': 'Sungei', 'BLK': 'Blk', 'MT': 'Mount', 'CRES': 'Crescent', 'PLG': 'Playground',
}
KEEP_UPPER = {'HDB', 'FCP', 'SBG', 'LC', 'JLG', 'TNC', 'CC', 'BTC', 'NOG', 'PV', 'PRC', 'PRU', 'ARBO', 'PCN',
              'II', 'III', 'PD', 'A3', 'E1', 'SPORTSG', 'MRT', 'PUB', 'NParks'}
SMALL = {'AND': 'and', 'AT': 'at', 'OF': 'of', 'THE': 'the'}
SPECIAL = {'MACRITCHIE': 'MacRitchie', 'MACPHERSON': 'MacPherson', 'MCNAIR': 'McNair', 'SPORTSG': 'SportSG',
           'JLG:': 'Jurong Lake Gardens:'}


def _part(core: str, first: bool) -> str:
    """One word without brackets or separators."""
    if not core:
        return core
    if core in SPECIAL:
        return SPECIAL[core]
    if core in ABBREV:
        return ABBREV[core]
    if core in KEEP_UPPER or re.fullmatch(r'[A-Z]?\d+[A-Z]?', core):
        return core
    if core in SMALL and not first:
        return SMALL[core]
    return core[:1].upper() + core[1:].lower()


def _word(w: str, first: bool) -> str:
    if w in SPECIAL:
        return SPECIAL[w]
    # keep brackets/colons around the word, and treat - / @ joined parts one by one
    pre = re.match(r'^[(\[]*', w).group(0)
    post = re.search(r'[)\],:]*$', w).group(0)
    core = w[len(pre):len(w) - len(post)] if post else w[len(pre):]
    pieces = re.split(r'([-/@])', core)
    out = []
    for i, piece in enumerate(pieces):
        if piece in ('-', '/', '@'):
            out.append(piece)
        else:
            out.append(_part(piece, first and i == 0))
    return pre + ''.join(out) + post


def clean_nparks(name: str) -> str:
    if not name or not name.isupper():
        return name  # already mixed case, e.g. "HortPark"
    words = name.split()
    out = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ''
        if w == 'ST' and "'" in nxt:
            out.append('St')  # ST ANNE'S -> St Anne's (saint), not Street
        else:
            out.append(_word(w, i == 0))
    return ' '.join(out)


def clean_title(source_key: str, title: str) -> str:
    if source_key == 'stb_attractions':
        return STB_TITLES.get(title, title)
    if source_key == 'nparks_parks':
        return clean_nparks(title)
    return title
