"""Validated, explicit itinerary inputs. All clock times are interpreted in Singapore."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.stage2a.models import Contract, Location

SGT = timezone(timedelta(hours=8), 'Asia/Singapore')
MAX_VISITS = 20  # Request protection, NOT a geographical or data coverage limit.

class PlacePoint(Location):
    source: Literal['onemap_search', 'user_map', 'user_coordinates', 'catalogue_representative', 'device_location']
    entity_id: str | None = Field(default=None, min_length=1, max_length=512)
    catalogue_build_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('label')
    @classmethod
    def clean_label(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('A nonempty label without control characters is required.')
        return value

    @model_validator(mode='after')
    def source_reference(self):
        if self.source == 'catalogue_representative':
            if not self.entity_id or not self.catalogue_build_id:
                raise ValueError('Catalogue points require an entity ID and source build ID.')
        elif self.entity_id is not None or self.catalogue_build_id is not None:
            raise ValueError('Do not attach a catalogue identity to unverified geocoder or map points.')
        return self

    def route_location(self):
        # A catalogue representative point is routed as a map point; a device
        # reading keeps its own provenance and its reported radius.
        return Location(latitude=self.latitude, longitude=self.longitude, label=self.label,
            source='user_map' if self.source == 'catalogue_representative' else self.source,
            accuracy_m=self.accuracy_m if self.source == 'device_location' else None,
            confirmed=True)

class Visit(Contract):
    visit_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    point: PlacePoint
    # None means a visible project assumption, not a missing value converted to zero.
    stay_minutes: int | None = Field(default=None, ge=0, le=1440, strict=True)
    stay_profile: Literal['quick', 'regular', 'extended'] = 'regular'

class TimeSettings(Contract):
    mode: Literal['estimate', 'budget', 'window'] = 'estimate'
    departure_at: datetime | None = None
    finish_by: datetime | None = None
    budget_minutes: int | None = Field(default=None, gt=0, le=10080, strict=True)
    buffer_minutes: int = Field(default=0, ge=0, le=1440, strict=True)

    @field_validator('departure_at', 'finish_by', mode='before')
    @classmethod
    def explicit_datetime(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError('Use an ISO datetime with explicit offset, not a numeric timestamp.')
        return value

    @field_validator('departure_at', 'finish_by')
    @classmethod
    def timezone_required(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('Include a UTC offset; local browser time must not be assumed.')
            return value.astimezone(SGT)
        return value

    @model_validator(mode='after')
    def consistent_time(self):
        if self.mode == 'estimate' and (self.budget_minutes is not None or self.finish_by is not None):
            raise ValueError('Estimated-duration mode has no hidden budget or finish deadline.')
        if self.mode == 'budget' and (self.budget_minutes is None or self.finish_by is not None):
            raise ValueError('Budget mode needs a duration and no finish deadline.')
        if self.mode == 'window':
            if self.departure_at is None or self.finish_by is None or self.budget_minutes is not None:
                raise ValueError('Time-window mode requires explicit departure and finish dates/times.')
            if self.finish_by <= self.departure_at:
                raise ValueError('Finish must be later; choose the next date explicitly for overnight trips.')
            if (self.finish_by - self.departure_at).total_seconds() > 10080 * 60:
                raise ValueError('This local request supports a window of at most seven days.')
        return self

    def available_seconds(self):
        if self.mode == 'estimate': return None
        if self.mode == 'budget': return self.budget_minutes * 60
        return (self.finish_by - self.departure_at).total_seconds()

class PlanRequest(Contract):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(default=0, ge=0, le=2**31-1, strict=True)
    origin: PlacePoint
    visits: list[Visit] = Field(min_length=1, max_length=MAX_VISITS)
    finish_policy: Literal['last_stop', 'return_to_start', 'custom'] = 'last_stop'
    finish: PlacePoint | None = None
    mode: Literal['walk', 'drive', 'cycle'] = 'walk'
    time: TimeSettings = Field(default_factory=TimeSettings)
    force_refresh: bool = False

    @model_validator(mode='after')
    def distinct_ids_and_finish(self):
        ids = [v.visit_id for v in self.visits]
        if len(ids) != len(set(ids)):
            raise ValueError('Visit IDs must be unique; repeat visits may use separate IDs.')
        if (self.finish_policy == 'custom') != (self.finish is not None):
            raise ValueError('Only custom finish mode takes an extra destination.')
        return self
