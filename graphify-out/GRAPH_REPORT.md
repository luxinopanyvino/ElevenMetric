# Graph Report - .  (2026-07-30)

## Corpus Check
- 88 files · ~79,726 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1220 nodes · 3341 edges · 59 communities (52 shown, 7 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 448 edges (avg confidence: 0.53)
- Token cost: 61,523 input · 0 output

## Community Hubs (Navigation)
- Academy API & Pipeline
- Simulation, Meta & Market API
- Analysis & Video Jobs API
- Auth & Tenant Dependencies
- Frontend App Shell
- Match Engine Tests
- API & Tenant-Isolation Tests
- Matches & Ingest Routes
- Data Readiness & Model Registry
- xG / xT Value Models
- Academy Projection Engine
- Match Simulation Engine
- Features & Lineup Optimizer
- Transfer Market Recommender
- Fatigue & Substitutions
- CSV Ingestion & Rollups
- Formation Detection
- Heatmaps
- Possession & Territory
- Pitch Geometry
- CV Player/Ball Detection
- Kit-Colour Team Assignment
- Best XI (Hungarian Assignment)
- Tactical Profile
- CSV Parsing & Validation
- Homography Calibration
- Pitch Normalisation & Sim Fixtures
- CV Tracking (ByteTrack)
- Ingest Commit
- Multitenancy Request Path
- Squad-Tier Model Concepts
- CV Pipeline Orchestration
- Homography Concepts
- CV Degradation Tiers
- Match-Sim & Value Concepts
- Import Preview
- Simulation Setup Route
- CSV Templates
- Honesty & ML Priors
- Data-Tier Ladder
- Dataset Catalogue
- Age Curve
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 55
- Community 56
- Community 57
- Community 58

## God Nodes (most connected - your core abstractions)
1. `Position` - 113 edges
2. `Pitch` - 88 edges
3. `Foot` - 47 edges
4. `TenantScoped` - 44 edges
5. `Base` - 42 edges
6. `AgeGroup` - 40 edges
7. `InputSource` - 40 edges
8. `Pathway` - 39 edges
9. `UUIDPk` - 38 edges
10. `Timestamped` - 38 edges

## Surprising Connections (you probably didn't know these)
- `Cumulative tiers, nothing imputed` --semantically_similar_to--> `Honesty by construction`  [INFERRED] [semantically similar]
  docs/DATA_MODEL.md → README.md
- `Core runtime dependencies` --conceptually_related_to--> `ElevenMetric platform`  [INFERRED]
  backend/requirements.txt → README.md
- `ElevenMetric platform` --cites--> `Multitenancy`  [EXTRACTED]
  README.md → docs/ARCHITECTURE.md
- `Honesty by construction` --conceptually_related_to--> `Bootstrap priors, not magic numbers`  [INFERRED]
  README.md → docs/ARCHITECTURE.md
- `Ingestion (csv_ingest pure functions)` --conceptually_related_to--> `CSV import routes`  [INFERRED]
  docs/ARCHITECTURE.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Cumulative data tier ladder** — docs_data_model_tier1_squad, docs_data_model_tier2_events, docs_data_model_tier3_tracking, docs_data_model_tier4_video, docs_data_model_tier5_context [EXTRACTED 1.00]
- **CV pipeline stages** — docs_cv_detector, docs_cv_tracker, docs_cv_homography, docs_cv_team_id, docs_cv_foot_point [EXTRACTED 1.00]
- **Substitution scoring inputs** — docs_models_impact_ridge, docs_models_fatigue_hazard, docs_models_effective_level, docs_models_substitution_scoring [INFERRED 0.85]

## Communities (59 total, 7 thin omitted)

### Community 0 - "Academy API & Pipeline"
Cohesion: 0.07
Nodes (94): add_assessment(), create_academy_player(), delete_academy_player(), get_academy_player(), list_academy_players(), list_assessments(), _out(), pipeline() (+86 more)

### Community 1 - "Simulation, Meta & Market API"
Cohesion: 0.06
Nodes (84): Metadata: the input contract, model registry and reference data., _persist(), Match simulation: pick two sides, play the fixture, watch it back., Store the fixture as an ordinary match, so the analysis pipeline can consume it…, Build a side from a real squad, using the optimiser to pick the XI., _setup_from_team(), _sim_player(), delete_market_player() (+76 more)

### Community 2 - "Analysis & Video Jobs API"
Cohesion: 0.06
Nodes (65): analyse_match(), analyse_video(), _gather(), get_job(), get_job_report(), get_report(), list_jobs(), list_reports() (+57 more)

### Community 3 - "Auth & Tenant Dependencies"
Cohesion: 0.06
Nodes (74): alias, get_current_principal(), get_scope(), get_tenant(), CurrentPrincipal, Depends, Session, FastAPI dependencies: authentication, tenant resolution, authorisation. (+66 more)

### Community 4 - "Frontend App Shell"
Cohesion: 0.11
Nodes (57): api, ApiError, auth, app, boot(), currentTheme(), loadPlayers(), loginScreen() (+49 more)

### Community 5 - "Match Engine Tests"
Cohesion: 0.04
Nodes (42): match(), fixture, The match engine: does it produce a football match, and the same one twice? The…, The loose companion to the test above: whatever the variance, twelve fixtures…, xG per shot sits near 0.10 in real football. It read 0.33 while the carry model…, The centre-forward took every shot in the match until the shape learned to send…, A ceiling, not a target — and a regression guard, not a claim of realism. Real…, Outfielders run 9-13 km in a real match; the engine need not hit that exactly,… (+34 more)

### Community 7 - "Matches & Ingest Routes"
Cohesion: 0.08
Nodes (41): create_match(), delete_match(), get_match(), ingest_events(), ingest_tracking(), list_events(), list_matches(), list_tracking() (+33 more)

### Community 8 - "Data Readiness & Model Registry"
Cohesion: 0.07
Nodes (35): data_contract(), data_readiness(), _has_budget(), _has_market(), models(), overview(), get, Scope (+27 more)

### Community 9 - "xG / xT Value Models"
Cohesion: 0.09
Nodes (33): expected_goals(), packing(), progressive(), ndarray, Value models: expected goals and expected threat. Both are deliberately small,…, Threat value of a single location., Threat added by moving the ball from ``start`` to ``end``. Only successful…, How many opponents the action played through. An opponent counts when they sit… (+25 more)

### Community 10 - "Academy Projection Engine"
Cohesion: 0.10
Nodes (34): first_team_bar(), _growth_rate(), _level_multiplier(), _pathway(), project(), date, Academy engine: development tracking and time-to-first-team projection. The…, Birth quartile within the selection year. Q1 players are over-selected. (+26 more)

### Community 11 - "Match Simulation Engine"
Cohesion: 0.10
Nodes (25): Build a stand-in opponent at a chosen level. Most clubs have no opposition…, _synthetic_opponent(), _anchors(), _block_bounds(), _finishing_skill(), _maybe_substitute(), _outfield_band(), _pass_skill() (+17 more)

### Community 12 - "Features & Lineup Optimizer"
Cohesion: 0.10
Nodes (26): attribute(), effective_level(), FatigueState, player_feature_vector(), position_fit(), Feature engineering shared by the ML engines. The two non-obvious pieces here…, Read an attribute, falling back sensibly when it was never supplied. The chain…, 0-1 suitability of ``player`` for ``target``. Combines a declared-position term… (+18 more)

### Community 13 - "Transfer Market Recommender"
Cohesion: 0.12
Nodes (22): Fee + agent fee. Wages are budgeted separately., detect_needs(), positions_for_formations(), _projected_level(), Transfer market recommender. Three stages: 1. **Need detection** — where the…, Find where the squad is weak, thin, or about to age out. ``relevant_positions``…, Level the player would perform at in our shirt. Adjusts the raw rating for…, Score every market player against every open need. (+14 more)

### Community 14 - "Fatigue & Substitutions"
Cohesion: 0.15
Nodes (26): fatigue_state(), Model in-match decline. Shape of the curve: negligible decay for the first ~55…, Rank substitution options. ``starters`` is a list of ``(player, Position,…, Players whose accumulated load warrants rotation, independent of form., recommend_substitutions(), workload_alerts(), _match_state(), player() (+18 more)

### Community 15 - "CSV Ingestion & Rollups"
Cohesion: 0.09
Nodes (7): headline_from_detail(), Derive any missing headline face from the detail beneath it. Clubs that export…, CSV ingestion: header mapping, validation, and the commit contract., The old model scored keepers on outfield faces, which made every goalkeeping…, test_goalkeepers_are_judged_on_goalkeeping_attributes(), test_headline_is_derived_from_detail_when_absent(), test_missing_detail_falls_back_to_its_headline_not_the_overall()

### Community 16 - "Formation Detection"
Cohesion: 0.13
Nodes (20): _cluster_1d(), detect_formation(), formation_from_slots(), FormationResult, _inertia(), _name_formation(), ndarray, Formation detection and shape description. The declared formation and the… (+12 more)

### Community 17 - "Heatmaps"
Cohesion: 0.12
Nodes (20): build_heatmap(), _gaussian_blur(), Heatmap, ndarray, Heatmap generation from event or tracking positions. Two estimators, chosen by…, Per-zone share of presence, own team minus opponent. Values run -1 (zone owned…, Separable Gaussian blur. Hand-rolled so scipy stays an optional dep., Silverman's rule of thumb, floored so a static player still renders. (+12 more)

### Community 18 - "Possession & Territory"
Cohesion: 0.13
Nodes (20): merge(), possession_from_events(), possession_from_tracking(), PossessionResult, Possession and territory metrics. Possession is reported three ways because the…, Time possession straight from tracking frames. Uses the feed's…, Overlay two results, preferring non-null values from ``primary``., Compute possession metrics from an event stream. ``events`` are objects with… (+12 more)

### Community 19 - "Pitch Geometry"
Cohesion: 0.11
Nodes (11): Pitch, Centre of the goal being attacked., Return ``(col, row)`` in the coarse zone grid., Visible goal angle in radians. Zero on the goal line outside the posts., test_thirds_and_box(), A tell-tale of the old carry model: every shot ended up on the goal's centre…, The shape has to react to the phase of play. While it did not, the whole side…, test_attackers_get_nearer_goal_when_the_ball_does() (+3 more)

### Community 20 - "CV Player/Ball Detection"
Cohesion: 0.15
Nodes (12): available_backends(), build_detector(), Detector, HogDetector, Player and ball detection. Three tiers, resolved at import time: 1.…, Return the best available detector, or ``None`` if the extras are missing., ultralytics-backed detector., OpenCV HOG people detector. No ball class, lower recall in crowds. (+4 more)

### Community 21 - "Kit-Colour Team Assignment"
Cohesion: 0.15
Nodes (13): hex_to_lab(), ndarray, Team assignment from kit colour. Crops the torso region of each detection,…, Return the cluster index, or ``None`` for an outlier., Lab distance between the two kits. Below ~18 the kits are too alike to separate…, Map a cluster onto "home"/"away" using the known home kit colour., sRGB (0-255) → CIE Lab (D65). Distances in Lab track perception, so a threshold…, Dominant Lab colour of the torso band of a detection box. Uses the middle 50%… (+5 more)

### Community 22 - "Best XI (Hungarian Assignment)"
Cohesion: 0.15
Nodes (18): best_xi(), compare_formations(), hungarian(), ndarray, Pick the strongest XI for ``formation``. ``locked`` pins ``slot_index ->…, Rank formations by the strength of the XI each one unlocks. This is the honest…, Exact minimum-cost assignment (Jonker-Volgenant style shortest paths). ``cost``…, _squad() (+10 more)

### Community 23 - "Tactical Profile"
Cohesion: 0.24
Nodes (15): analyse_attack(), analyse_defence(), build_profile(), _defensive_style(), DefensiveProfile, _find_strengths(), _find_vulnerabilities(), _index() (+7 more)

### Community 24 - "CSV Parsing & Validation"
Cohesion: 0.12
Nodes (16): _enum_parser(), parse(), Any, Decode and read a CSV, sniffing the delimiter. Semicolon-delimited exports are…, Parse and validate a CSV against a dataset definition., sniff_and_read(), Spanish- and German-locale spreadsheets export semicolons; assuming commas…, test_a_bad_value_fails_only_its_own_row() (+8 more)

### Community 25 - "Homography Calibration"
Cohesion: 0.19
Nodes (11): CalibrationState, estimate_homography(), project(), ndarray, Camera-to-pitch mapping. Detections live in pixels; every metric downstream…, Apply a homography to image points, returning pitch metres., Mean reprojection error in metres. Above ~1.5 m the frame is unusable., Rolling homography with validity gating. (+3 more)

### Community 26 - "Pitch Normalisation & Sim Fixtures"
Cohesion: 0.19
Nodes (12): flip_for_direction(), Pitch geometry, coordinate normalisation and zone grids. Every provider ships a…, Normalise so the analysed team always attacks towards +x. Second-half…, _anchors(), Deterministic match simulator. Two jobs: * **Demo and test fixture.** Every…, Duck-typed to match :class:`~app.models.match.MatchEvent`., Run the simulation and return events plus tracking frames., SimEvent (+4 more)

### Community 27 - "CV Tracking (ByteTrack)"
Cohesion: 0.24
Nodes (6): Detection, Bottom-centre of the box — the point that sits on the pitch plane, and…, ByteTracker, iou(), Multi-object tracking: turn per-frame detections into persistent identities. A…, Track

### Community 28 - "Ingest Commit"
Cohesion: 0.42
Nodes (12): commit(), _commit_academy_players(), _commit_assessments(), _commit_events(), _commit_market(), _commit_players(), _commit_tracking(), get (+4 more)

### Community 29 - "Multitenancy Request Path"
Cohesion: 0.18
Nodes (11): Analytics layer, 404-not-403 for cross-tenant access, CrossTenantAccess, Duck-typed analytics rows, Multitenancy, orchestrator.analyse(), PLAN_LIMITS, Request path (+3 more)

### Community 30 - "Squad-Tier Model Concepts"
Cohesion: 0.20
Nodes (11): Tier 1 · Squad and lineup, age_curve() for projection only, Best XI (Hungarian assignment), effective_level (damped fit penalty), Fatigue and injury hazard curve, Impact model — impact-ridge-1.0, Two-dimensional knapsack bundle, Positional fit (+3 more)

### Community 31 - "CV Pipeline Orchestration"
Cohesion: 0.29
Nodes (9): PipelineResult, Path, Produce clearly-labelled synthetic tracking when CV extras are missing., Process a video into pitch-space tracking frames. ``landmark_fn`` maps a…, run(), _simulated_fallback(), Duck-typed to match :class:`~app.models.match.TrackingFrame`., SimFrame (+1 more)

### Community 32 - "Homography Concepts"
Cohesion: 0.20
Nodes (9): calibration_coverage, CalibrationState.update(), Continuous homography re-estimation, detector.py, DLT with Hartley normalisation, Foot point projection, homography.py, Coordinate frame (metres, bottom-left origin) (+1 more)

### Community 33 - "CV Degradation Tiers"
Cohesion: 0.28
Nodes (9): Core runtime dependencies, Computer-vision extras, CV three-tier degradation, CV pipeline (decode→detect→track→calibrate→project→classify), Computer vision video path, Loud degradation tiers, Tier 4 · Video, services/cv/synthetic.py fixture generator (+1 more)

### Community 34 - "Match-Sim & Value Concepts"
Cohesion: 0.25
Nodes (9): SimEvent/SimFrame duck-type compatibility, Flat integer positional stream, Match simulation, Match engine (simulation/engine.py), Models (provenance + version), Known limitation: shot distribution, Turnover / absorbing failure state, Expected goals — xg-logistic-1.0 (+1 more)

### Community 35 - "Import Preview"
Cohesion: 0.29
Nodes (8): preview(), description, File, Form, post, UploadFile, Parse and validate without writing anything. A bad import is far cheaper to…, _read_upload()

### Community 36 - "Simulation Setup Route"
Cohesion: 0.33
Nodes (7): options(), get, post, Scope, Everything the setup form needs: which teams can play, and how., Play a fixture between two sides. The heavy work is done here in one pass;…, run()

### Community 37 - "CSV Templates"
Cohesion: 0.33
Nodes (6): csv_template(), A ready-to-fill CSV: header row plus one example row., A CSV template: header row plus one example row., template(), The template must itself be a valid file, or it teaches the wrong shape., test_template_round_trips_through_the_parser()

### Community 38 - "Honesty & ML Priors"
Cohesion: 0.40
Nodes (5): Bootstrap priors, not magic numbers, ML layer, Cumulative tiers, nothing imputed, data_completeness / confidence scaling, Honesty by construction

### Community 39 - "Data-Tier Ladder"
Cohesion: 0.40
Nodes (5): Tracking decimation to target_hz, The input contract, Tier 2 · Event data, Tier 3 · Tracking data, Tier 5 · Context and finance

### Community 40 - "Dataset Catalogue"
Cohesion: 0.50
Nodes (4): datasets(), Every dataset that can be imported, with its columns and aliases., catalogue(), Dataset definitions, for the ingest UI.

### Community 41 - "Age Curve"
Cohesion: 0.50
Nodes (4): age_curve(), Relative level at ``age`` compared with the position's peak age. Rises from…, test_age_curve_peaks_and_declines(), test_keepers_age_more_gently_than_wingers()

### Community 42 - "Community 42"
Cohesion: 0.67
Nodes (3): Frontend (vanilla ES modules), Visualisation rules, Frontend app shell (index.html)

### Community 43 - "Community 43"
Cohesion: 0.67
Nodes (3): kit_separation metric, team_id.py kit classifier, Torso-only Lab-space kit sampling

### Community 44 - "Community 44"
Cohesion: 0.67
Nodes (3): Academy — academy-gbr-1.0, Biological age / bio-banding correction, Relative age effect

## Knowledge Gaps
- **29 isolated node(s):** `Field_`, `Tier`, `state`, `app`, `savedTheme` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Pitch` connect `Pitch Geometry` to `Simulation, Meta & Market API`, `Analysis & Video Jobs API`, `Simulation Setup Route`, `Match Engine Tests`, `Matches & Ingest Routes`, `xG / xT Value Models`, `Match Simulation Engine`, `Formation Detection`, `Heatmaps`, `Possession & Territory`, `CV Player/Ball Detection`, `Tactical Profile`, `Homography Calibration`, `Pitch Normalisation & Sim Fixtures`, `Ingest Commit`, `CV Pipeline Orchestration`?**
  _High betweenness centrality (0.143) - this node is a cross-community bridge._
- **Why does `Position` connect `Academy API & Pipeline` to `Simulation, Meta & Market API`, `Analysis & Video Jobs API`, `Match Engine Tests`, `Age Curve`, `Academy Projection Engine`, `xG / xT Value Models`, `Features & Lineup Optimizer`, `Transfer Market Recommender`, `Match Simulation Engine`, `Fatigue & Substitutions`?**
  _High betweenness centrality (0.137) - this node is a cross-community bridge._
- **Why does `analyse_video()` connect `Analysis & Video Jobs API` to `Simulation, Meta & Market API`, `CV Player/Ball Detection`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **Are the 71 inferred relationships involving `Position` (e.g. with `AcademyAssessment` and `AcademyPlayer`) actually correct?**
  _`Position` has 71 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `Pitch` (e.g. with `FormationResult` and `Heatmap`) actually correct?**
  _`Pitch` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 38 inferred relationships involving `Foot` (e.g. with `AcademyAssessment` and `AcademyPlayer`) actually correct?**
  _`Foot` has 38 INFERRED edges - model-reasoned connections that need verification._
- **Are the 34 inferred relationships involving `TenantScoped` (e.g. with `CrossTenantAccess` and `TenantContext`) actually correct?**
  _`TenantScoped` has 34 INFERRED edges - model-reasoned connections that need verification._