"""Execution/adoption status is authored by application state, not model prose."""
from __future__ import annotations
import re

LABELS = {'nature':'公园/自然','food':'餐饮','heritage':'历史地点','monument':'古迹','tourism':'景点'}


def slot_label(slot, chinese=True):
    categories=slot.get('categories') or []
    if categories:
        return '/'.join(LABELS.get(c,c) if chinese else c for c in categories)
    return ('地点' if chinese else 'location')


def acknowledgement(parsed, slots, preview, user_message):
    """Do not show 'added/booked/done' from an unadopted model proposal.

    Keep model-generated intent in the internal interpretation, but all visible
    adoption and execution claims come from the actual local stage.
    """
    zh=bool(re.search(r'[\u3400-\u9fff]',user_message))
    empty=[(i+1,s) for i,s in enumerate(slots) if not s['options']]
    if empty:
        labels='、'.join(f'第{i}项（{slot_label(s)}）' for i,s in empty) if zh else ', '.join(f'item {i} ({slot_label(s,False)})' for i,s in empty)
        if zh:
            return f'草案还未完整：{labels}暂无可选候选。已经找到的候选也尚未加入实际行程；原行程未修改。补齐地点后才能一起采用，不会悄悄省略任何一项。'
        return f'The proposal is incomplete: {labels} has no matching candidate. No edits have been applied. Resolve all requested places before adopting; none will be silently omitted.'
    if slots:
        return ('已生成地点候选草案，尚未加入实际行程。请核对并确认采用，之后再计算路线与参考总时间。' if zh else
                'Place candidates are ready for review, but no itinerary edits have been applied. Confirm the proposal before routes and reference times are calculated.')
    if parsed.actions or any(getattr(parsed,k) for k in ('preferred_add','preferred_remove','excluded_add','excluded_remove','notes_add','notes_remove')):
        return ('已生成条件修改草案，尚未应用。请核对后采用；原行程和已采用条件目前保持不变。' if zh else
                'Constraint changes are ready for review, not yet applied. Your adopted itinerary and requirements are unchanged until confirmation.')
    return parsed.acknowledgement
