# Separate dialogue semantics from executable commands

Status: accepted

B will have Gemini return `semantic_segments` together with the existing operation-scoped `commands`. The segments preserve the user’s intent, scope, hardness, status, source, quote and turn index for evaluation and conflict diagnosis; commands remain the only input that can produce a proposal or change a Draft. Active structured constraints will round-trip through `Preferences.active_constraints`, while `Draft` remains the adopted itinerary. This split preserves the existing C/D contract, makes silent constraint loss detectable, and allows the system to mark opening-time requirements as unresolved until C has deterministic validation. A segment/command mismatch fails closed and leaves the current state unchanged.
