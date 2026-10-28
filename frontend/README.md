# Frontend stages

`stage0_dashboard.html` is the FastAPI local data-status dashboard.
The source-inspection pages are in `stage1`, `stage1b` and `stage1c`.
`stage2a` provides two-point routes, `stage2b` the manual itinerary editor,
and `stage2c` the conversational map served by the shared deployment.
These pages use plain JavaScript and Leaflet; no Node build step is required.
Application interface text is English. Source names remain searchable through
their original aliases, and translated place names are marked as unofficial.
