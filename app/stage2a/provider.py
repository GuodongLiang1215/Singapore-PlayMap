"""Normalize documented OneMap search and routing contracts; no synthetic fallback."""
from __future__ import annotations
import math
from datetime import datetime, timezone
from app.stage2a.auth import TokenStore
from app.stage2a.models import SG_REQUEST_ENVELOPE, RouteRequest, distance_m
from app.stage2a.transport import HTTPSJSON, ProviderError, check_payload_error

ENDPOINT_REVIEW_METRES = 50.0  # Project warning threshold; does not verify an entrance.


def utc_now(): return datetime.now(timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool): raise ValueError('Boolean is not a metric')
    value = float(value)
    if not math.isfinite(value): raise ValueError('Nonfinite number')
    return value


def point_in_envelope(lat, lon):
    w, s, e, n = SG_REQUEST_ENVELOPE
    return w <= lon <= e and s <= lat <= n


def decode_polyline(encoded: str):
    """Google-style 1e5 polyline, returned explicitly as GeoJSON [lon,lat]."""
    if not isinstance(encoded, str) or not encoded or len(encoded) > 1500000:
        raise ValueError('Missing/oversized encoded polyline')
    i, latitude, longitude, points = 0, 0, 0, []
    while i < len(encoded):
        deltas = []
        for _ in range(2):
            value, shift = 0, 0
            while True:
                if i >= len(encoded) or shift > 30: raise ValueError('Truncated/invalid polyline')
                b = ord(encoded[i]) - 63; i += 1
                if b < 0 or b > 63: raise ValueError('Illegal polyline character')
                value |= (b & 31) << shift
                shift += 5
                if b < 32: break
            deltas.append(~(value >> 1) if value & 1 else value >> 1)
        latitude += deltas[0]; longitude += deltas[1]
        lat, lon = latitude / 100000, longitude / 100000
        if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise ValueError('Invalid decoded coordinate')
        points.append([lon, lat])
        if len(points) > 100000: raise ValueError('Too many geometry points')
    if len(points) < 2: raise ValueError('LineString requires two points')
    return points


def parse_search(payload, query, requested_page):
    check_payload_error(payload)
    try:
        rows = payload['results']
        if not isinstance(rows, list): raise ValueError()
        found, total, current = int(payload['found']), int(payload['totalNumPages']), int(payload['pageNum'])
        if min(found, total, current) < 0 or current != requested_page or (found > 0 and total < 1): raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ProviderError('SEARCH_SCHEMA_CHANGED', '搜索响应缺少有效分页或结果字段；本版未自动猜测坐标。') from None
    items, rejected = [], 0
    for index, row in enumerate(rows):
        try:
            if not isinstance(row, dict): raise ValueError()
            lat, lon = number(row['LATITUDE']), number(row['LONGITUDE'])
            if not point_in_envelope(lat, lon): raise ValueError()
            def text(k):
                v = row.get(k)
                return str(v)[:500] if isinstance(v, (str, int, float)) and str(v).upper() != 'NIL' else ''
            title = text('BUILDING') or text('SEARCHVAL') or text('ADDRESS') or 'OneMap搜索候选'
            items.append({'candidate_key': f'{current}:{index}', 'label':title,
                'address':text('ADDRESS'), 'postal_code':text('POSTAL'),
                'latitude':lat,'longitude':lon,'source':'onemap_search','entrance_verified':False,
                'confirmation_required':True})
        except (KeyError, ValueError, TypeError): rejected += 1
    return {'provider':'OneMap','query':query,'page':current,'total_pages':total,'provider_found':found,
            'next_page':current+1 if current < total and current < 1000 else None,
            'page_limit_reached':current == 1000 and current < total,
            'results':items,'invalid_rows_on_page':rejected,'selected_automatically':False,
            'queried_at':utc_now(), 'notice':'候选坐标不等于核验入口；请明确选择，不自动使用第一条结果。'}


def parse_route(payload, request: RouteRequest):
    check_payload_error(payload)
    status = payload.get('status')
    if status is not None and str(status) != '0':
        message = str(payload.get('status_message','')).lower()
        if 'token' in message or 'unauthor' in message:
            raise ProviderError('AUTH_REJECTED', 'OneMap拒绝认证，请重新登录。', 503)
        raise ProviderError('PROVIDER_NO_ROUTE', 'OneMap本次未返回可用路线；这不是现实中绝对不可达的证明。', 422)
    try:
        summary = payload['route_summary']
        seconds, metres = number(summary['total_time']), number(summary['total_distance'])
        if seconds <= 0 or metres <= 0: raise ValueError()
        coordinates = decode_polyline(payload['route_geometry'])
        if any(not point_in_envelope(p[1],p[0]) for p in coordinates): raise ValueError()
        origin, destination = request.origin, request.destination
        start_offset = distance_m(origin.latitude, origin.longitude, coordinates[0][1], coordinates[0][0])
        end_offset = distance_m(destination.latitude, destination.longitude, coordinates[-1][1], coordinates[-1][0])
        # Detect reversed geometry/incorrect scaling rather than silently repairing it.
        if max(start_offset, end_offset) > 5000: raise ValueError()
    except (KeyError, ValueError, TypeError, OverflowError):
        raise ProviderError('ROUTE_SCHEMA_OR_GEOMETRY', '路线摘要或几何未通过检查：不绘制直线替代，不猜测缺失的时间或距离。') from None
    warnings = ['结果是所选两点间的服务商通行估计，不是完整游玩时长，也不保证景点营业或允许入内。',
                '手选点及搜索候选未被升级为核验入口；返回路线不等于实时封路、排队或无障碍核验。']
    review_needed = max(start_offset,end_offset) > ENDPOINT_REVIEW_METRES
    if review_needed:
        warnings.append('路径端点与所选点存在明显偏移；请查看路径端点标记并重新选择通行点。未绘制连接线，偏移段时间未知。')
    return {'provider':'OneMap','mode':request.mode,'revision':request.revision,'queried_at':utc_now(),
            'origin':origin.model_dump(),'destination':destination.model_dump(),
            'geometry':{'type':'LineString','coordinates':coordinates},
            'distance_m':metres,'duration_s':seconds,'duration_min':round(seconds/60,2),
            'geometry_source':'OneMap route_geometry, decoded precision=5',
            'units':{'distance':'metres','duration':'seconds','coordinates':'GeoJSON longitude,latitude'},
            'endpoint_offsets_m':{'origin':round(start_offset,2),'destination':round(end_offset,2)},
            'endpoint_review_required':review_needed,'endpoint_warning_threshold_m':ENDPOINT_REVIEW_METRES,
            'endpoint_threshold_is_project_heuristic':True,'snap_gap_traversability_verified':False,
            'entrances_verified':False,'opening_hours_checked':False,'visit_duration_included':False,
            'itinerary_feasible':None,'live_disruption_verified':False,'warnings':warnings,
            'request_sent_to_provider':True,'fake_fallback_used':False}

class OneMapClient:
    def __init__(self, root, transport=None):
        self.tokens = TokenStore(root)
        self.transport = transport or HTTPSJSON()

    def _request_with_token(self, operation, *, params):
        token = self.tokens.get(self.transport)
        try:
            return self.transport.request(operation, token=token, params=params)
        except ProviderError as exc:
            # A server-side deployment can refresh once when OneMap rejects an
            # otherwise cached token. Never loop and never persist the password/token.
            if exc.code != 'AUTH_REJECTED' or not self.tokens.can_refresh():
                raise
            self.tokens.invalidate_runtime()
            token = self.tokens.get(self.transport, force_refresh=True)
            return self.transport.request(operation, token=token, params=params)

    def search(self, query, page=1):
        payload = self._request_with_token('search',
            params={'searchVal':query,'returnGeom':'Y','getAddrDetails':'Y','pageNum':page})
        return parse_search(payload,query,page)

    def route(self, request):
        def ll(point): return f'{point.latitude:.8f},{point.longitude:.8f}'
        payload = self._request_with_token('route',
            params={'start':ll(request.origin),'end':ll(request.destination),'routeType':request.mode})
        return parse_route(payload,request)
