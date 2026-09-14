"""Candidate associations only, computed in metres rather than degree buffers.

A polygon match is NOT ownership, a public entrance, opening confirmation,
route connectivity, safety, accessibility, or national-boundary verification.
Invalid shapes are reported and skipped for joins, never automatically repaired.
"""
from __future__ import annotations

import math
from collections import defaultdict

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import shape, box
from shapely.ops import transform
from shapely.validation import explain_validity

from app.stage1.normalise import normal_name


class ParkIndex:
    def __init__(self, boundaries: list[dict]):
        self.transformer = Transformer.from_crs('EPSG:4326', 'EPSG:3414', always_xy=True)
        self.entries = []
        self.issues = []
        self.names = defaultdict(list)
        for record in boundaries:
            projected, problem = self.project(record)
            if problem or projected.geom_type not in ('Polygon', 'MultiPolygon'):
                self.issues.append({'record_uid': record['record_uid'],
                                    'issue': problem or 'not_polygon', 'action': 'excluded_from_joins_not_deleted'})
                continue
            entry = {'record_uid': record['record_uid'], 'name': record.get('name'),
                     'normalised_name': normal_name(record.get('name')), 'geometry': projected}
            self.entries.append(entry)
            if entry['normalised_name']:
                self.names[entry['normalised_name']].append(len(self.entries)-1)
        self.tree = STRtree([e['geometry'] for e in self.entries])

    def project(self, record):
        if not record.get('map_eligible'):
            return None, 'stage1_geometry_or_crs_not_eligible'
        try:
            g = shape(record['geometry'])
            if g.is_empty:
                return None, 'empty_geometry'
            if not g.is_valid:
                return None, 'invalid_geometry: ' + explain_validity(g)
            def project_xy(x, y, z=None):
                return self.transformer.transform(x, y, errcheck=True)
            projected = transform(project_xy, g)
            if projected.is_empty or not all(math.isfinite(v) for v in projected.bounds):
                return None, 'non_finite_projection'
            if not projected.is_valid:
                return None, 'invalid_projected_geometry: ' + explain_validity(projected)
            return projected, None
        except Exception as exc:
            return None, 'projection_or_geometry_failure: ' + type(exc).__name__

    def nearby(self, point, radius):
        search_box = box(point.x-radius, point.y-radius, point.x+radius, point.y+radius) if radius else point
        indices = [int(i) for i in self.tree.query(search_box)]
        return [i for i in indices if self.entries[i]['geometry'].distance(point) <= radius+1e-8]

    def evidence(self, point, i):
        entry = self.entries[i]
        poly = entry['geometry']
        covered = poly.covers(point)
        boundary_distance = poly.boundary.distance(point)
        relation = ('on_boundary' if boundary_distance <= 1e-7 else 'inside') if covered else 'outside_near'
        return {'boundary_uid': entry['record_uid'], 'boundary_name': entry['name'],
                'point_relation': relation, 'point_covered': bool(covered),
                'distance_to_polygon_metres': round(float(poly.distance(point)), 3),
                'distance_to_boundary_metres': round(float(boundary_distance), 3),
                'distance_is_route_cost': False, 'metric_crs': 'EPSG:3414'}


def associate(parks: list[dict], boundaries: list[dict], access_points: list[dict],
              access_radius_metres: float = 50.0, park_radius_metres: float = 100.0):
    if not all(math.isfinite(v) and 0 <= v <= 1000 for v in (access_radius_metres, park_radius_metres)):
        raise ValueError('Association radii must be between 0 and 1000 metres')
    index = ParkIndex(boundaries)
    park_links, access_links = [], []
    problems = list(index.issues)
    strong_park_candidates = defaultdict(set)
    park_match_counts = {}
    for park in parks:
        pt, problem = index.project(park)
        if problem or pt.geom_type != 'Point':
            problems.append({'record_uid': park['record_uid'], 'issue': problem or 'not_point',
                             'action': 'excluded_from_joins_not_deleted'})
            park_match_counts[park['record_uid']] = 0
            continue
        name = normal_name(park.get('name'))
        # Same-name candidates remain visible even if distant; spatial conflicts
        # cannot be used to propagate access-point candidates.
        indices = set(index.names.get(name, [])) | set(index.nearby(pt, park_radius_metres))
        rows = []
        for i in indices:
            ev = index.evidence(pt, i)
            same = bool(name and name == index.entries[i]['normalised_name'])
            if same and ev['point_covered']:
                basis = 'same_name_and_point_covered'
            elif same and ev['distance_to_polygon_metres'] <= park_radius_metres:
                basis = 'same_name_near_boundary_review'
            elif same:
                basis = 'same_name_spatial_conflict'
            elif ev['point_covered']:
                basis = 'spatial_only_name_differs_review'
            else:
                continue
            rows.append({**ev, 'park_uid': park['record_uid'], 'park_name': park.get('name'),
                         'association_basis': basis, 'identity_verified': False,
                         'access_propagation_candidate': basis == 'same_name_and_point_covered'})
        rows.sort(key=lambda r: (not r['access_propagation_candidate'], r['distance_to_polygon_metres'], r['boundary_uid']))
        strong = [r for r in rows if r['access_propagation_candidate']]
        park_match_counts[park['record_uid']] = len(strong)
        for row in rows:
            row['strong_boundary_candidate_count_for_park'] = len(strong)
            if row['access_propagation_candidate']:
                strong_park_candidates[row['boundary_uid']].add(park['record_uid'])
        park_links.extend(rows)
    matched_access = set()
    for access in access_points:
        pt, problem = index.project(access)
        if problem or pt.geom_type != 'Point':
            problems.append({'record_uid': access['record_uid'], 'issue': problem or 'not_point',
                             'action': 'excluded_from_joins_not_deleted'})
            continue
        rows = []
        for i in index.nearby(pt, access_radius_metres):
            ev = index.evidence(pt, i)
            rows.append({**ev, 'access_uid': access['record_uid'],
                         'access_name': access.get('name'),
                         'association_basis': 'source_access_point_spatial_candidate_only',
                         'park_candidate_uids': sorted(strong_park_candidates[ev['boundary_uid']]),
                         'entrance_verified': False, 'route_reachability_verified': False,
                         'public_access_verified': False})
        rows.sort(key=lambda r: (r['distance_to_polygon_metres'], r['boundary_uid']))
        for row in rows:
            row['boundary_candidate_count_for_access_point'] = len(rows)
        if rows:
            matched_access.add(access['record_uid'])
        access_links.extend(rows)
    stats = {
        'metric_crs': 'EPSG:3414', 'axis_order': 'source_lon_lat_projected_easting_northing',
        'access_radius_metres': access_radius_metres, 'park_radius_metres': park_radius_metres,
        'thresholds_are_unvalidated_project_heuristics': True,
        'boundary_record_count': len(boundaries), 'valid_boundary_count_used_for_joins': len(index.entries),
        'park_point_count': len(parks),
        'parks_with_one_strong_boundary_candidate': sum(n == 1 for n in park_match_counts.values()),
        'parks_with_multiple_strong_boundary_candidates': sum(n > 1 for n in park_match_counts.values()),
        'parks_without_strong_boundary_candidate': sum(n == 0 for n in park_match_counts.values()),
        'park_boundary_link_count': len(park_links),
        'source_access_point_record_count': len(access_points),
        'access_points_with_boundary_candidates': len(matched_access),
        'access_points_without_boundary_candidates': len(access_points)-len(matched_access),
        'access_boundary_link_count': len(access_links),
        'access_points_with_multiple_boundary_candidates': len({r['access_uid'] for r in access_links if r['boundary_candidate_count_for_access_point'] > 1}),
        'verified_entrance_count': 0,
        'geometry_problem_count': len(problems),
        'automatic_geometry_repairs': 0,
        'automatic_park_identity_assignments': 0,
        'route_graph_built': False,
    }
    return park_links, access_links, problems, stats
