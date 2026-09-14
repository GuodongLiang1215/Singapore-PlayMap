"""LLM output is a bounded proposal, never executable code or authoritative coordinates."""
from __future__ import annotations
from typing import Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.stage2b.models import PlacePoint, Visit, TimeSettings, MAX_VISITS

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

Topic = Literal['park','food','museum','shopping','heritage','architecture','art','nature','photography']
Category = Literal['nature','food','heritage','monument','tourism']

class Preferences(Strict):
    preferred: list[Topic] = Field(default_factory=list, max_length=10)
    excluded: list[Topic] = Field(default_factory=list, max_length=10)
    notes: list[str] = Field(default_factory=list, max_length=12)

class Draft(Strict):
    origin: PlacePoint | None = None
    visits: list[Visit] = Field(default_factory=list, max_length=MAX_VISITS)
    finish_policy: Literal['last_stop','return_to_start','custom'] = 'last_stop'
    finish: PlacePoint | None = None
    mode: Literal['walk','drive','cycle'] = 'walk'
    time: TimeSettings = Field(default_factory=TimeSettings)

    @model_validator(mode='after')
    def ids(self):
        ids=[v.visit_id for v in self.visits]
        if len(ids)!=len(set(ids)): raise ValueError('Duplicate visit identifiers')
        return self

class PlaceSpec(Strict):
    # query is a name explicitly given by the user (English translation allowed),
    # not a model-invented candidate. Generic requests use categories and keywords.
    query: str = Field(default='', max_length=160)
    categories: list[Category] = Field(default_factory=list, max_length=5)
    keywords: list[str] = Field(default_factory=list, max_length=6)

class ActionBase(Strict):
    quote: str = Field(min_length=1,max_length=240,description='Exact supporting substring of latest user message or a pending request; never invent a quotation.')

class LocationAction(ActionBase):
    op: Literal['set_origin','set_finish','add_visit','replace_visit']
    place: PlaceSpec
    target_id: str | None = Field(default=None,max_length=80)
    @model_validator(mode='after')
    def required_target(self):
        if self.op=='replace_visit' and not self.target_id: raise ValueError('Replacement target required')
        if self.op!='replace_visit' and self.target_id is not None: raise ValueError('Unexpected target')
        if self.op in ('set_origin','set_finish') and not self.place.query.strip(): raise ValueError('An explicit endpoint name is required')
        return self

class RemoveAction(ActionBase):
    op: Literal['remove_visit']
    target_id: str = Field(min_length=1,max_length=80)
class MoveAction(ActionBase):
    op: Literal['move_visit']
    target_id: str = Field(min_length=1,max_length=80)
    position: int = Field(ge=1,le=MAX_VISITS,strict=True)
class StayAction(ActionBase):
    op: Literal['set_stay']
    target_id: str = Field(min_length=1,max_length=80)
    minutes: int | None = Field(ge=0,le=1440,strict=True)
class FinishAction(ActionBase):
    op: Literal['set_finish_policy']
    value: Literal['last_stop','return_to_start']
class ModeAction(ActionBase):
    op: Literal['set_mode']
    value: Literal['walk','drive','cycle','public_transport']
class TimeAction(ActionBase):
    op: Literal['set_time']
    mode: Literal['estimate','budget','window'] | None = None
    budget_minutes: int | None = Field(default=None,gt=0,le=10080,strict=True)
    departure_at: str | None = Field(default=None,max_length=40)
    finish_by: str | None = Field(default=None,max_length=40)
    clear_departure: bool = False
    buffer_minutes: int | None = Field(default=None,ge=0,le=1440,strict=True)
class ClearAction(ActionBase):
    op: Literal['clear_visits']

Action = Union[LocationAction,RemoveAction,MoveAction,StayAction,FinishAction,ModeAction,TimeAction,ClearAction]
class Interpretation(Strict):
    acknowledgement: str = Field(max_length=400)
    preferred_add: list[Topic] = Field(default_factory=list,max_length=10)
    preferred_remove: list[Topic] = Field(default_factory=list,max_length=10)
    excluded_add: list[Topic] = Field(default_factory=list,max_length=10)
    excluded_remove: list[Topic] = Field(default_factory=list,max_length=10)
    notes_add: list[str] = Field(default_factory=list,max_length=8)
    notes_remove: list[str] = Field(default_factory=list,max_length=8)
    questions: list[str] = Field(default_factory=list,max_length=3)
    actions: list[Action] = Field(default_factory=list,max_length=12)

class Pending(Strict):
    user_message: str = Field(max_length=3000)
    interpretation: Interpretation

class ChatRequest(Strict):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(ge=0,le=2**31-1,strict=True)
    message: str = Field(min_length=1,max_length=3000)
    draft: Draft
    preferences: Preferences = Field(default_factory=Preferences)
    pending: Pending | None = None
    consent: bool
    @field_validator('message')
    @classmethod
    def text(cls,v):
        v=v.strip()
        if not v or any(ord(c)<32 and c not in '\n\t' for c in v):raise ValueError('Invalid message')
        return v

class Pick(Strict):
    slot_id: str = Field(min_length=1,max_length=32)
    candidate_key: str = Field(min_length=1,max_length=64)
    reason: str = Field(max_length=240)
class Selection(Strict):
    picks: list[Pick] = Field(max_length=8)
class ResolveRequest(Strict):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(ge=0,le=2**31-1,strict=True)
    ticket: str = Field(pattern=r'^[a-f0-9]{32}$')
    choices: dict[str,str] = Field(default_factory=dict,max_length=8)
class ForgetRequest(Strict):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')
