"""Short-lived, session-isolated RAM reuse. No location or provider response is persisted."""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import threading
import time

class SegmentCache:
    def __init__(self, ttl_seconds=300, max_entries=256, clock=time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._values = OrderedDict()
        self._generations = {}
        self.max_geometry_points = 200000
        self._lock = threading.Lock()

    @staticmethod
    def key(session_id, mode, origin, destination):
        # Exact coordinates and direction; no rounding, reversal, or mode substitution.
        return (session_id, mode, origin.latitude, origin.longitude,
                destination.latitude, destination.longitude)

    def _prune(self):
        now = self.clock()
        for k in list(self._values):
            if now - self._values[k][0] >= self.ttl_seconds:
                self._values.pop(k, None)

    def get(self, key):
        with self._lock:
            self._prune()
            entry = self._values.get(key)
            if entry is None: return None
            self._values.move_to_end(key)
            return deepcopy(entry[1]), max(0.0, self.clock() - entry[0])

    def generation(self, session_id):
        with self._lock: return self._generations.get(session_id, 0)

    def put(self, key, route, expected_generation=None):
        # Keep only route geometry/metrics, never free-text labels or full user requests.
        fields = ('provider','mode','queried_at','geometry','distance_m','duration_s',
                  'endpoint_offsets_m','endpoint_review_required','geometry_source',
                  'endpoint_warning_threshold_m')
        value = {k:deepcopy(route[k]) for k in fields if k in route}
        with self._lock:
            if expected_generation is not None and self._generations.get(key[0], 0) != expected_generation:
                return  # A cleared page cannot be repopulated by an old in-flight response.
            self._prune()
            self._values[key] = (self.clock(), value)
            self._values.move_to_end(key)
            while (len(self._values) > self.max_entries or
                   sum(len(v[1].get('geometry',{}).get('coordinates',[])) for v in self._values.values()) > self.max_geometry_points):
                self._values.popitem(last=False)

    def clear_session(self, session_id):
        with self._lock:
            self._generations[session_id] = self._generations.get(session_id, 0) + 1
            for k in list(self._values):
                if k[0] == session_id: self._values.pop(k, None)
