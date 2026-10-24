"""Look up entrance coordinates for the core attraction review sheet with OneMap.

Run on your own computer after ONEMAP_EMAIL / ONEMAP_PASSWORD are set in .env:

    .\\.venv\\Scripts\\python.exe -m pip install openpyxl
    .\\.venv\\Scripts\\python.exe scripts\\geocode_entrances.py

What it does
- Reads review/core_attractions_review.xlsx, sheet 入口 (entrances).
- For each entrance without coordinates, searches OneMap with the entrance name
  (and the attraction name), keeps results within 2 km of the attraction, and picks
  the one whose name matches best (ties: nearest).
- Writes the coordinates back with status "OneMap 自动匹配" and the OneMap label in
  the source column, so a person can see what was matched.
- Saves every candidate to review/entrance_candidates.json for checking.
- Keeps a backup: review/core_attractions_review.backup.xlsx.

Rows that already have coordinates are not touched. Nothing is guessed: if OneMap
returns nothing near the attraction, the row stays empty with a note.
"""
from __future__ import annotations

import json
from collections import Counter
import math
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from openpyxl import load_workbook
except ImportError:
    sys.exit('Please run: .\\.venv\\Scripts\\python.exe -m pip install openpyxl')

from app.stage2a.provider import OneMapClient  # noqa: E402
from app.stage2a.transport import ProviderError  # noqa: E402

BOOK = ROOT / 'review' / 'core_attractions_review.xlsx'
BACKUP = ROOT / 'review' / 'core_attractions_review.backup.xlsx'
CANDIDATES = ROOT / 'review' / 'entrance_candidates.json'
MAX_DISTANCE_M = 2000
# Better OneMap search words for entrances whose names are descriptions (tried first)
SEARCH_TERMS = {
    'Bayfront 地铁站方向入口': ['BAYFRONT MRT STATION'],
    'Gardens by the Bay 地铁站（TEL）入口': ['GARDENS BY THE BAY MRT STATION'],
    'Satay by the Bay / 东侧入口': ['SATAY BY THE BAY'],
    'Hotel Tower 3 一楼大堂（经 Tower 3 车道），上 56 楼': ['MARINA BAY SANDS HOTEL TOWER 3', 'MARINA BAY SANDS HOTEL', '10 BAYFRONT AVENUE'],
    'Front Entrance': ['ASIAN CIVILISATIONS MUSEUM'],
    'River Entrance（两处都有坡道）': ['ASIAN CIVILISATIONS MUSEUM'],
    '8 Sentosa Gateway（Resorts World Sentosa）': ['UNIVERSAL STUDIOS SINGAPORE'],
    'Mount Faber 站': ['FABER PEAK SINGAPORE', 'MOUNT FABER CABLE CAR STATION'],
    'HarbourFront 站': ['HARBOURFRONT TOWER TWO', 'HARBOURFRONT CABLE CAR STATION'],
    'Sentosa 站': ['SENTOSA CABLE CAR STATION', 'IMBIAH LOOKOUT'],
    'Singapore Zoo 主入口（Mandai 入口广场）': ['SINGAPORE ZOO'],
    'Night Safari 入口（Mandai 入口广场）': ['NIGHT SAFARI'],
    'River Wonders 入口（Mandai 入口广场）': ['RIVER WONDERS'],
    'Bird Paradise 入口（20 Mandai Lake Rd）': ['BIRD PARADISE', '20 MANDAI LAKE ROAD'],
    'Bukit Timah Gate（近 Botanic Gardens 地铁站）': ['BOTANIC GARDENS MRT STATION', 'BUKIT TIMAH GATE'],
    'Wetland Centre': ['SUNGEI BULOH WETLAND CENTRE', 'SUNGEI BULOH WETLAND RESERVE'],
    'Changi Point Ferry Terminal（乘小船上岛）': ['CHANGI POINT FERRY TERMINAL'],
    '78 Airport Boulevard': ['JEWEL CHANGI AIRPORT'],
    'Merlion Park': ['MERLION PARK'],
    'ArtScience Museum 建筑位置（非精确门口）': ['ARTSCIENCE MUSEUM'],
    'Singapore Flyer 建筑位置（非精确门口）': ['SINGAPORE FLYER'],
    'Esplanade 建筑位置（非精确门口）': ['ESPLANADE THEATRES ON THE BAY', 'ESPLANADE'],
    'Esplanade 地铁站': ['ESPLANADE MRT STATION'],
    'Marina Barrage 建筑位置（非精确门口）': ['MARINA BARRAGE'],
    'National Gallery 建筑位置（非精确门口）': ['NATIONAL GALLERY SINGAPORE'],
    'National Museum 建筑位置（非精确门口）': ['NATIONAL MUSEUM OF SINGAPORE'],
    'Fort Canning Park': ['FORT CANNING PARK'],
    'Fort Canning 地铁站': ['FORT CANNING MRT STATION'],
    'Peranakan Museum 建筑位置（非精确门口）': ['PERANAKAN MUSEUM'],
    'Raffles Hotel（外观）': ['RAFFLES HOTEL'],
    'Clarke Quay': ['CLARKE QUAY'],
    'Boat Quay': ['BOAT QUAY'],
    'Chinatown 地铁站': ['CHINATOWN MRT STATION'],
    'Chinatown Complex': ['CHINATOWN COMPLEX'],
    'Sri Mariamman Temple 建筑位置（非精确门口）': ['SRI MARIAMMAN TEMPLE'],
    'Little India 地铁站': ['LITTLE INDIA MRT STATION'],
    'Tekka Centre': ['TEKKA CENTRE'],
    'Masjid Sultan 建筑位置（非精确门口）': ['MASJID SULTAN', 'SULTAN MOSQUE'],
    'Thian Hock Keng 建筑位置（非精确门口）': ['THIAN HOCK KENG'],
    'Sri Veeramakaliamman Temple 建筑位置（非精确门口）': ['SRI VEERAMAKALIAMMAN TEMPLE', 'VEERAMAKALIAMMAN', '141 SERANGOON ROAD'],
    'Malay Heritage Centre 建筑位置（非精确门口）': ['MALAY HERITAGE CENTRE'],
    'Koon Seng Road（彩色店屋）': ['KOON SENG ROAD'],
    'Joo Chiat Complex': ['JOO CHIAT COMPLEX'],
    'Henderson Waves': ['HENDERSON WAVES'],
    'Fort Siloso': ['FORT SILOSO', 'SENTOSA FORT SILOSO'],
    'Haw Par Villa（地铁站旁）': ['HAW PAR VILLA'],
    'Orchard 地铁站': ['ORCHARD MRT STATION'],
    'Somerset 地铁站': ['SOMERSET MRT STATION'],
    'MacRitchie Reservoir Park 主入口': ['MACRITCHIE RESERVOIR PARK', 'MACRITCHIE RESERVOIR'],
    'TreeTop Walk': ['TREETOP WALK', 'HSBC TREETOP WALK'],
    'Marine Cove': ['MARINE COVE', 'MARINE COVE EAST COAST PARK'],
    'East Coast Lagoon Food Village': ['EAST COAST LAGOON FOOD VILLAGE'],
    'Jurong Lake Gardens': ['JURONG LAKE GARDENS', 'LAKESIDE GARDEN'],
    'Lakeside 地铁站': ['LAKESIDE MRT STATION'],
    'Chinese Garden 地铁站': ['CHINESE GARDEN MRT STATION'],
    'HortPark': ['HORTPARK'],
    'Science Centre Singapore 建筑位置（非精确门口）': ['SCIENCE CENTRE SINGAPORE'],
}
# Entrances that are legitimately far from the attraction's representative point
MAX_DISTANCE_OVERRIDE = {
    'Changi Point Ferry Terminal（乘小船上岛）': 5000,
    'Fort Siloso': 5000,          # NHB representative point is on the mainland, ~3.5 km away
    'TreeTop Walk': 5000,         # large park; representative point is at the south end
    'Marine Cove': 6000,          # East Coast Park is ~15 km long
    'East Coast Lagoon Food Village': 6000,
}
STOP = {'the', 'of', 'and', 'singapore', 'entrance', 'main', 'station'}


def english(text: str) -> str:
    """Keep the Latin part of a mixed Chinese/English label."""
    text = re.sub(r'[（(][^）)]*[）)]', ' ', text or '')
    text = re.sub(r'[　-鿿＀-￯→/／;；]+', ' ', text)
    return ' '.join(text.split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP and len(w) > 1}


def metres(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def clean(q: str) -> str:
    """OneMap may reject queries with symbols such as & or →; keep letters, digits and spaces."""
    return ' '.join(re.sub(r'[^A-Za-z0-9 ]+', ' ', q).split())


def queries(entrance: str, attraction: str) -> list[str]:
    e, a = english(entrance), english(attraction)
    out = list(SEARCH_TERMS.get(entrance.strip(), []))
    if e and a and not words(a) & words(e):
        out.append(f'{a} {e}')
    if e:
        out.append(e)
    return list(dict.fromkeys(clean(q) for q in out if len(clean(q)) >= 3))


def best(results, entrance, attraction, lat, lon, max_distance=MAX_DISTANCE_M):
    want = words(english(entrance)) or words(english(attraction))
    scored = []
    for r in results:
        d = metres(lat, lon, r['latitude'], r['longitude'])
        if d > max_distance:
            continue
        # Match on the place name; use the address only when searching by address (e.g. "18 Raffles Quay"),
        # so a condo on "Marine Parade Road" does not match "Marine Cove".
        by_address = entrance.strip()[:1].isdigit()
        have = words(r.get('label', '') + (' ' + r.get('address', '') if by_address else ''))
        overlap = len(want & have) / max(1, len(want))
        if overlap < 0.5:
            continue
        scored.append((overlap, -r.get('_query_index', 0), -d, r, d))
    if not scored:
        return None, []
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    scored = [(s[0], s[3], s[4]) for s in scored]
    top = scored[0]
    return (top if top[0] > 0 else None), scored


def main():
    if not BOOK.is_file():
        sys.exit(f'Not found: {BOOK}')
    shutil.copy2(BOOK, BACKUP)
    wb = load_workbook(BOOK)
    main_ws, ent = wb['核心景点核验'], wb['入口']
    header = [c.value for c in main_ws[1]]
    i_id, i_lat, i_lon, i_name = header.index('地点 ID'), header.index('纬度'), header.index('经度'), header.index('景点')
    places = {r[i_id]: (r[i_name], r[i_lat], r[i_lon]) for r in main_ws.iter_rows(min_row=2, values_only=True) if r[i_id]}
    eh = [c.value for c in ent[1]]
    col = {name: eh.index(name) + 1 for name in ('地点 ID', '景点', '入口名称', '纬度', '经度', '来源', '核验状态', '备注')}

    client = OneMapClient(ROOT)
    try:  # one test search first, so a login or network problem shows clearly
        test = client.search('Singapore Zoo')
        print(f'OneMap test search OK: {len(test["results"])} results for "Singapore Zoo"')
    except ProviderError as exc:
        sys.exit(f'OneMap test search failed [{exc.code}]: {exc.message}')
    log, errors = [], Counter()
    try:
        process_rows(ent, col, places, client, log, errors)
    finally:
        wb.save(BOOK)
        CANDIDATES.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding='utf-8')
    matched = sum(1 for x in log if x['picked'])
    missed = sum(1 for x in log if not x['picked'])
    print(f'\nMatched {matched}, not found {missed}. Saved {BOOK.name}; candidates in {CANDIDATES.name}.')
    if errors:
        print('Search errors:', dict(errors))
    print('Close the file in Excel/WPS before running, or the save will fail.')


def process_rows(ent, col, places, client, log, errors):
    streak, stop = 0, False
    for row in range(2, ent.max_row + 1):
        eid = ent.cell(row, col['地点 ID']).value
        name = ent.cell(row, col['入口名称']).value
        if not eid or not name or ent.cell(row, col['纬度']).value not in (None, ''):
            continue
        attraction, lat, lon = places.get(eid, (ent.cell(row, col['景点']).value, None, None))
        if lat is None:
            continue
        results, tried, failed = [], [], []
        for qi, q in enumerate(queries(name, attraction)):
            tried.append(q)
            try:
                results += [dict(r, _query_index=qi) for r in client.search(q)['results']]
            except ProviderError as exc:
                print(f'  search failed for "{q}": {exc.code} (skipped)')
                failed.append(exc.code); errors[exc.code] += 1
                streak += 1
                if streak >= 5:
                    stop = True
                    break
            else:
                streak = 0
        if stop:
            print('Stopped after 5 failed searches in a row; results so far are saved.')
            break
        pick, scored = best(results, (SEARCH_TERMS.get(name.strip()) or [name])[0], attraction, lat, lon,
                            MAX_DISTANCE_OVERRIDE.get(name.strip(), MAX_DISTANCE_M))
        log.append({'row': row, 'attraction': attraction, 'entrance': name, 'queries': tried, 'errors': failed,
                    'raw_result_count': len(results),
                    'picked': pick[1] if pick else None,
                    'candidates': [dict(s[1], distance_m=round(s[2])) for s in scored[:8]]})
        if pick:
            r, d = pick[1], pick[2]
            ent.cell(row, col['纬度']).value = round(r['latitude'], 7)
            ent.cell(row, col['经度']).value = round(r['longitude'], 7)
            ent.cell(row, col['来源']).value = f"OneMap：{r['label']}｜{r.get('address', '')}"
            ent.cell(row, col['核验状态']).value = 'OneMap 自动匹配'
            note = f'距景点代表点 {round(d)} m；名称匹配度 {pick[0]:.0%}'
            if '建筑位置' in name:
                note += '；OneMap 给的是建筑位置，不是精确门口'
            ent.cell(row, col['备注']).value = note
            print(f'OK   {attraction} | {name} -> {r["label"]} ({round(d)} m)')
        else:
            ent.cell(row, col['备注']).value = 'OneMap 在景点 2 km 内没有找到名称相符的结果，需手动取点'
            print(f'MISS {attraction} | {name}')


if __name__ == '__main__':
    main()
