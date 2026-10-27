# Separate dialogue semantics from executable commands

Status: accepted

B will have Gemini return `semantic_segments` together with the existing operation-scoped `commands`. The segments preserve the user’s intent, scope, hardness, status, source, quote and turn index for evaluation and conflict diagnosis; commands remain the only input that can produce a proposal or change a Draft. Active structured constraints will round-trip through `Preferences.active_constraints`, while `Draft` remains the adopted itinerary. This split preserves the existing C/D contract, makes silent constraint loss detectable, and allows the system to mark opening-time requirements as unresolved until C has deterministic validation. A segment/command mismatch fails closed and leaves the current state unchanged.

The local proposal reducer binds ordinal visit scopes to stable visit IDs before adoption. Newly proposed visits reserve their IDs before reduction. Reordering therefore cannot silently retarget an active constraint; removing a visit removes its scoped constraints. Snapshot ordinals remain the model-facing edit syntax. Explicit null plus a matching cancellation segment clears the executable walking limit; an omitted field preserves it. Hard conflicts prevent adoption even when the model has returned a clarification question.
