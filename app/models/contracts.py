"""Stage-0 contracts. These validate structure, not real-world route feasibility."""
from typing import Any, Literal
from uuid import uuid4
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Coordinates(Contract):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Geographical membership must later use country data; no pilot bounding box here.

class Evidence(Contract):
    source_key: str
    source_url: str | None = None
    source_record_id: str | None = None
    verified_at: AwareDatetime | None = None
    status: Literal["source_reported", "unverified", "human_verified", "conflicting"] = "unverified"
    note: str | None = None

class DurationEstimate(Contract):
    minimum_minutes: int = Field(gt=0)
    typical_minutes: int = Field(gt=0)
    maximum_minutes: int = Field(gt=0)
    basis: Literal["operator", "field_observation", "project_assumption", "user"]
    evidence: Evidence | None = None
    @model_validator(mode="after")
    def ordered(self):
        if not self.minimum_minutes <= self.typical_minutes <= self.maximum_minutes:
            raise ValueError("Duration must satisfy minimum <= typical <= maximum")
        return self

class Entrance(Contract):
    entrance_id: str
    coordinates: Coordinates
    verification_status: Literal["unknown", "unverified", "verified"] = "unverified"
    evidence: Evidence

class VisitOption(Contract):
    option_id: str
    place_id: str
    visit_mode: Literal["outside_view", "indoor_visit", "park_visit", "food_stop", "other"]
    entrance_ids: list[str] = Field(default_factory=list)
    suggested_duration: DurationEstimate | None = None
    opening_hours_raw: str | None = None
    opening_verification: Literal["unknown", "unverified", "verified"] = "unknown"
    price_status: Literal["unknown", "free", "paid", "conditional"] = "unknown"
    reservation_status: Literal["unknown", "required", "not_required"] = "unknown"
    # A raw opening-hours string is not an executable opening-hours calendar.

class Place(Contract):
    place_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    display_point: Coordinates | None = None
    coordinate_kind: Literal["source_point", "representative_point", "verified_entrance", "unknown"] = "unknown"
    category_tags: list[str] = Field(default_factory=list)
    access_status: Literal["unknown", "verified_accessible", "closed", "restricted"] = "unknown"
    source_evidence: list[Evidence] = Field(default_factory=list)
    entrances: list[Entrance] = Field(default_factory=list)
    visit_options: list[VisitOption] = Field(default_factory=list)

class TimePolicy(Contract):
    mode: Literal["suggest_duration", "duration_budget", "clock_window"] = "suggest_duration"
    budget_minutes: int | None = Field(default=None, gt=0)
    departure_at: AwareDatetime | None = None
    finish_by: AwareDatetime | None = None
    strictness: Literal["hard", "soft", "unspecified"] = "unspecified"
    timezone: Literal["Asia/Singapore"] = "Asia/Singapore"
    @model_validator(mode="after")
    def check_modes(self):
        if self.mode == "suggest_duration" and (self.budget_minutes is not None or self.finish_by is not None):
            raise ValueError("Suggested-duration mode must not invent a budget or deadline")
        if self.mode == "duration_budget":
            if self.budget_minutes is None or self.finish_by is not None:
                raise ValueError("Duration-budget mode needs a budget and no separate deadline")
        if self.mode == "clock_window":
            if self.departure_at is None or self.finish_by is None or self.budget_minutes is not None:
                raise ValueError("Clock-window mode needs two aware datetimes, without a separate budget")
            if self.finish_by <= self.departure_at:
                raise ValueError("Finish must be after departure; use the next date for overnight plans")
        return self

class ConstraintRecord(Contract):
    key: str
    value: Any
    source: Literal["user", "system_proposal", "inferred_unconfirmed"]
    strength: Literal["hard", "soft", "unspecified"] = "unspecified"
    user_confirmed: bool = False

class PlanningRequest(Contract):
    origin_label: str | None = None
    origin: Coordinates | None = None
    time: TimePolicy = Field(default_factory=TimePolicy)
    transport_modes: list[Literal["walk", "public_transport", "drive", "cycle"]] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    must_visit_ids: list[str] = Field(default_factory=list)
    excluded_place_ids: list[str] = Field(default_factory=list)
    return_to_origin: bool | None = None
    end_location: Coordinates | None = None
    maximum_walking_minutes: int | None = Field(default=None, ge=0)
    constraints: list[ConstraintRecord] = Field(default_factory=list)
    @model_validator(mode="after")
    def no_conflicting_ids(self):
        if set(self.must_visit_ids) & set(self.excluded_place_ids):
            raise ValueError("A place cannot be both mandatory and excluded")
        return self

class TravelLeg(Contract):
    from_id: str
    to_id: str
    mode: Literal["walk", "public_transport", "drive", "cycle"]
    status: Literal["computed", "not_computed", "unreachable", "provider_error"] = "not_computed"
    distance_m: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    route_geojson: dict | None = None
    provider: str | None = None
    queried_at: AwareDatetime | None = None
    departure_at: AwareDatetime | None = None
    @model_validator(mode="after")
    def no_invented_cost(self):
        if self.status == "computed" and (self.distance_m is None or self.duration_seconds is None or not self.provider):
            raise ValueError("Computed travel needs actual costs and provider provenance")
        if self.status != "computed" and (self.distance_m is not None or self.duration_seconds is not None):
            raise ValueError("Unavailable travel must not be assigned zero or invented costs")
        return self

class PlanStop(Contract):
    place_id: str
    option_id: str
    stay_minutes: int = Field(gt=0)
    arrival_at: AwareDatetime | None = None
    departure_at: AwareDatetime | None = None

class ItineraryPlan(Contract):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    state_revision: int = Field(ge=0)
    status: Literal["draft", "needs_clarification", "conditionally_checked", "rejected"] = "draft"
    stops: list[PlanStop] = Field(default_factory=list)
    legs: list[TravelLeg] = Field(default_factory=list)
    total_duration_minutes: float | None = Field(default=None, ge=0)
    assumptions: list[ConstraintRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # A full feasibility validator will be implemented in the planning stage.

class SessionState(Contract):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    revision: int = Field(default=0, ge=0)
    request: PlanningRequest = Field(default_factory=PlanningRequest)
    completed_place_ids: list[str] = Field(default_factory=list)
    locked_place_ids: list[str] = Field(default_factory=list)
    current_location: Coordinates | None = None
    current_plan_id: str | None = None
    # Storage/update semantics are not implemented merely by declaring this schema.

class ChangeOperation(Contract):
    operation: Literal["set", "clear", "add", "remove"]
    field: Literal["origin", "time", "transport_modes", "interests", "excluded_categories", "must_visit_ids", "excluded_place_ids", "return_to_origin", "end_location", "maximum_walking_minutes"]
    value: Any = None

class ChangeSet(Contract):
    session_id: str
    base_revision: int = Field(ge=0)
    changes: list[ChangeOperation] = Field(min_length=1)
