"""Public contracts: explicit coordinates, units and endpoint confirmation."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Broad request sanity envelope, NOT a national polygon or a catalogue clipping rule.
SG_REQUEST_ENVELOPE = (103.4, 0.9, 104.7, 1.7)  # west, south, east, north

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Location(Contract):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = Field(default='用户选点', min_length=1, max_length=200)
    source: Literal['onemap_search', 'user_map', 'user_coordinates']
    confirmed: bool

    @field_validator('latitude', 'longitude', mode='before')
    @classmethod
    def real_number(cls, v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError('Coordinates must be numeric JSON values, not text or booleans.')
        return v

    @field_validator('confirmed', mode='before')
    @classmethod
    def explicit_choice(cls, v):
        if v is not True:
            raise ValueError('Explicitly choose this endpoint before requesting a route.')
        return True

    @model_validator(mode='after')
    def singapore_request(self):
        w, s, e, n = SG_REQUEST_ENVELOPE
        if not (w <= self.longitude <= e and s <= self.latitude <= n):
            raise ValueError('Point is outside the broad Singapore service envelope; check latitude/longitude order.')
        return self

class SearchRequest(Contract):
    query: str = Field(min_length=1, max_length=160)
    page: int = Field(default=1, ge=1, le=1000)

    @field_validator('query')
    @classmethod
    def clean_query(cls, v):
        v = v.strip()
        if not v or any(ord(c) < 32 for c in v):
            raise ValueError('Use a non-empty place, road, building name or postal code.')
        return v

class RouteRequest(Contract):
    origin: Location
    destination: Location
    mode: Literal['walk', 'drive', 'cycle'] = 'walk'
    revision: int = Field(default=0, ge=0, le=2**31-1)

    @model_validator(mode='after')
    def different_endpoints(self):
        if distance_m(self.origin.latitude, self.origin.longitude,
                      self.destination.latitude, self.destination.longitude) < 1:
            raise ValueError('Endpoints are less than one metre apart; choose a different destination.')
        return self


def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2-p1, math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 6371008.8 * 2 * math.asin(min(1.0, math.sqrt(max(0.0, a))))
