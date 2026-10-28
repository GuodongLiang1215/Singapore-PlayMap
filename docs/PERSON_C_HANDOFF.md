# Person C — GIS & Constraint-Aware Planning Handoff

## Delivery status

Person C implementation is feature-complete for the agreed GIS/planning scope. The branch keeps user confirmation authoritative: recommendations and alternatives never silently modify an itinerary.

## Implemented capabilities

1. **Spatial nearby recommendation**
   - `POST /api/stage2b/recommend-nearby`
   - Candidate generation: Stage1C catalogue radius/category/keyword query.
   - Verification/ranking: real OneMap route duration for `walk`, `drive`, or `cycle`.
   - Optional `max_travel_minutes` filters by provider route time, not straight-line distance.
   - Returns recommendations only; `itinerary_modified=false`, `confirmation_required=true`.

2. **Maximum walking-time constraint**
   - `PlanRequest.max_walk_minutes`.
   - Checks each real walking leg and reports the violating leg(s) and excess minutes.

3. **Total-time feasibility**
   - Travel + visit stay + buffer.
   - Supports duration budget and explicit departure/finish window.
   - Distinguishes all-fit, upper-range-only exceedance, reference exceedance, and all-estimates exceedance.
   - `itinerary_feasible` combines reference total time, requested walking limits,
     and every visit's opening-hours result. `feasibility_check` explains the
     checks, known failures, unresolved evidence and reference-estimate basis.
   - A known failure returns `false`, even if another check is unknown. With no
     failure, unresolved evidence returns `null`; all applicable checks passing
     returns `true`. Unrequested budget/walking constraints are neutral.
   - A reference estimate can fit while the upper duration estimate exceeds the
     budget. The existing range warning and stricter chat budget verification
     remain separate; `true` is not a guarantee about the duration range or live conditions.

4. **Constraint-aware alternative POIs**
   - `POST /api/stage2b/alternatives`.
   - Starts from a reported walking violation, generates nearby same-category candidates,
     and recalculates the complete itinerary with the same feasibility checks.
   - Excludes known closing-time, stay-overrun, budget and walking conflicts,
     including conflicts at unchanged stops. Checked candidates rank before
     unresolved candidates. Unresolved suggestions retain `feasible=null` and
     an explicit review label; they are not counted in `feasible_count`.
   - Never adopts a replacement without user confirmation.

5. **OneMap route recalculation**
   - Real provider routes for walk / drive / cycle.
   - Session-isolated short-lived route cache; force refresh is supported.
   - No synthetic straight-line fallback is presented as a route.

6. **Opening-hours feasibility**
   - Uses catalogue metadata when a rule is safely parseable and a departure time exists.
   - Reports `feasible`, `closed_at_arrival`, `insufficient_time`, or `unknown`.
   - Evaluates complete daily or supported weekly rules, including split hours,
     `24:00` closing and overnight intervals, in Singapore time. Raw catalogue
     schedules are checked before English presentation is applied.
   - Only a bounded subset of the [OSM opening-hours grammar](https://wiki.openstreetmap.org/wiki/Key:opening_hours/specification)
     is supported. Ambiguous overrides, holiday/seasonal exceptions, unsupported
     annotations and malformed rules remain unknown rather than being reduced
     to their first time interval.
   - Complex schedules, temporary closures, and last-admission edge cases remain explicitly unverified.

7. **Entrance-aware routing V1**
   - Uses reviewed `entrance_points` as **entrance candidates**, not field-verified entrances.
   - One candidate: route to that candidate.
   - Multiple candidates: compare real provider routes from the previous routing point and choose the fastest reachable candidate.
   - If all candidates fail, fall back to the place representative point with an explicit basis.
   - Departure reuses the selected arrival candidate. The system does **not** invent internal traversal between different gates.

## Integration contracts for Person D

- Keep the selected POI identity separate from its routing point:
  - `visit.point` = the attraction/place the user chose.
  - `visit.routing_point` = actual point used for route calculation.
  - `visit.selected_entrance` = selected entrance candidate, when available.
  - `visit.entrance_selection_basis` explains why it was selected/fell back.
- Show entrance candidates as suggestions, never as “verified entrances”.
- Show opening-hours warnings from `visit.opening_status` and `opening_hours_display`.
- Nearby recommendations should expose route duration and route distance; straight-line distance is candidate-generation context only.
- Alternatives and recommendations require explicit user confirmation before changing the itinerary.

## Confirmed real integration checks

- ArtScience Museum after closing time: catalogue hours -> arrival time -> `closed_at_arrival` -> Stage2B UI warning.
- Singapore Botanic Gardens from Royal Palm Mansions: multiple candidates evaluated -> Tanglin Gate selected.
- Same destination from Beauty World Centre: multiple candidates evaluated -> Bukit Timah Gate selected.
- Beauty World Centre nearby nature search: 20 candidates route-evaluated; recommendations filtered to the requested maximum walking time and ranked by real route duration.
- Walk, drive, and cycle routes have each been exercised against OneMap.

## Explicit non-goals / boundaries

- Public transport routing is not implemented.
- Automatic visit-order optimisation is not implemented.
- Entrance candidates are not field-verified.
- Different entry/exit gates with internal park traversal are not implemented because internal connectivity/travel time is not available.
- Complex/exceptional opening calendars and live closures are not guessed.
- Live/future traffic prediction is not claimed.
- Recommendations do not automatically alter the itinerary.
- General-purpose comparative operators such as arbitrary `closer_than` / `farther_than` are not a separate planning constraint contract.

## Regression coverage

The final local regression run before cleanup reported **37 passed**. After cleanup/status/documentation changes, rerun the full suite before handoff. The existing suite covers alternatives, API behavior, entrance routing, OneMap catalogue matching/auth refresh, opening hours, spatial recommendations, walking constraints, and time budgets.
