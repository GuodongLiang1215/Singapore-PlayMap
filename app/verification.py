"""Process-local evidence from completed calculator calls, never client claims."""
from copy import deepcopy
import hashlib
import json
import secrets
import threading
import time


SNAPSHOT_FIELDS = ('origin','visits','finish_policy','finish','mode','time','max_walk_minutes')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def snapshot_fingerprint(value):
    data = value.model_dump(mode='json') if hasattr(value,'model_dump') else value
    return fingerprint({key:data.get(key) for key in SNAPSHOT_FIELDS})


def condition_fingerprint(name, value, scope):
    return fingerprint({'name':name,'value':value,'scope':scope})


class VerificationStore:
    def __init__(self,ttl=600,capacity=128,clock=time.monotonic):
        self.ttl,self.capacity,self.clock = ttl,capacity,clock
        self.values,self.lock = {},threading.Lock()

    def record(self,request,result,*,catalogue_build_id):
        data = request.model_dump(mode='json')
        checks = {}
        complete = result.get('complete_plan_created') is True and result.get('all_legs_accounted_for') is True
        if complete and not result.get('synthetic_fallback_used'):
            walk = result.get('walking_constraint_check') or {}
            if data['mode']=='walk' and data.get('max_walk_minutes') is not None and walk.get('status') in {'ok','violation'}:
                checks[condition_fingerprint('max_walk_minutes',data['max_walk_minutes'],'global')] = {
                    'status':'satisfied' if walk['status']=='ok' else 'conflicting','source':'route',
                    'basis':'per_leg_provider_duration'}
            budget = result.get('budget_check') or {}
            minutes = data['time'].get('budget_minutes')
            if minutes is not None:
                status = ('satisfied' if budget.get('range_within_budget') is True else
                          'conflicting' if budget.get('reference_within_budget') is False else 'unresolved')
                checks[condition_fingerprint('budget_minutes',minutes,'global')] = {
                    'status':status,'source':'route','basis':'reference_route_and_declared_stay_range'}
        item = {'session_id':data['session_id'],'revision':data['revision'],
            'snapshot_fingerprint':snapshot_fingerprint(request),'catalogue_build_id':catalogue_build_id,
            'checks':checks}
        with self.lock:
            now=self.clock()
            self.values={key:row for key,row in self.values.items() if row[0]+self.ttl>now}
            if len(self.values)>=self.capacity:
                self.values.pop(next(iter(self.values)))
            token=secrets.token_hex(16)
            self.values[token]=(now,deepcopy(item))
        return token

    def query(self,token,session_id,revision,draft,*,catalogue_build_id):
        with self.lock:
            stored=self.values.get(token)
            if stored is None or stored[0]+self.ttl<=self.clock():
                self.values.pop(token,None)
                return None,'missing_or_expired'
            item=stored[1]
            if item['catalogue_build_id'] is None or catalogue_build_id is None:
                return None,'catalogue_version_unavailable'
            if item['catalogue_build_id']!=catalogue_build_id:
                return None,'catalogue_changed'
            if item['session_id']!=session_id or item['revision']!=revision or item['snapshot_fingerprint']!=snapshot_fingerprint(draft):
                return None,'snapshot_mismatch'
            return deepcopy(item),'matched'

    def clear(self,session_id):
        with self.lock:
            self.values={key:row for key,row in self.values.items() if row[1]['session_id']!=session_id}


VERIFICATIONS = VerificationStore()
