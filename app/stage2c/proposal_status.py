"""Execution/adoption status is authored by application state, not model prose."""
from __future__ import annotations
LABELS = {'nature':'parks and nature','food':'dining','heritage':'heritage sites',
          'monument':'monuments','tourism':'attractions'}


def slot_label(slot):
    categories=slot.get('categories') or []
    if categories:
        return '/'.join(LABELS.get(c,c) for c in categories)
    return 'location'


def acknowledgement(parsed, slots, preview, user_message):
    """Do not show 'added/booked/done' from an unadopted model proposal.

    Keep model-generated intent in the internal interpretation, but all visible
    adoption and execution claims come from the actual local stage.
    """
    empty=[(i+1,s) for i,s in enumerate(slots) if not s['options']]
    if empty:
        spatial=[(i,s) for i,s in empty
                 if (s.get('spatial_requirement') or {}).get('status') in {'unresolved','conflicting'}]
        if spatial:
            labels=', '.join(f'item {i} ({slot_label(s)})' for i,s in spatial)
            messages=['The proposal is incomplete.',
                      f'Nearby search for {labels} needs a specific reference location.']
            if any(s['spatial_requirement']['status']=='conflicting' for _,s in spatial):
                messages.append('Clarify any conflicting reference locations or distance requirements.')
            if any(s.get('operation')=='set_origin' and s['options'] for s in slots):
                messages.append('Reply in chat with the exact station or exit name from the starting-point candidates.')
            else:
                messages.append('Reply in chat with the exact reference place name, including the station or exit if applicable.')
            spatial_items={i for i,_ in spatial}
            other=[(i,s) for i,s in empty if i not in spatial_items]
            if other:
                labels=', '.join(f'item {i} ({slot_label(s)})' for i,s in other)
                messages.append(f'No matching candidates were found for {labels}.')
            messages.extend(['Resolve all requested places before confirming.', 'Your itinerary is unchanged.'])
            return ' '.join(messages)
        labels=', '.join(f'item {i} ({slot_label(s)})' for i,s in empty)
        return f'The proposal is incomplete. No matching candidates were found for {labels}. Resolve all requested places before confirming. Your itinerary is unchanged.'
    if slots:
        return 'Place candidates are ready for review. Confirm the proposal to update your itinerary and calculate routes and estimated times.'
    if parsed.actions or parsed.semantic_segments or any(getattr(parsed,k) for k in ('preferred_add','preferred_remove','excluded_add','excluded_remove','notes_add','notes_remove')):
        return 'Proposed changes are ready for review. Your itinerary and requirements remain unchanged until you confirm.'
    return parsed.acknowledgement
