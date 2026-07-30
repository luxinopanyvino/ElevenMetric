# Feature Specification: Player Performance Attribute Vocabulary — 35-attribute alignment

**Feature Branch**: `001-player-attribute-vocabulary`

**Created**: 2026-07-30

**Status**: Draft

**Input**: User description: "Alinear el vocabulario de atributos de rendimiento del jugador con el estándar de 35 (esquema tipo FIFA/EA FC). Faltan dos detalles: Posicionamiento ofensivo (bajo shooting) y Regates (bajo dribbling). Respetar la regla de fallback y la honestidad por construcción. Cuestión abierta: si reubicar Cabezazos (heading) de shooting a defending."

## Context

The product describes a player through a layered attribute vocabulary: six
headline faces (pace, shooting, passing, dribbling, defending, physical), a set
of detail attributes that roll up to those faces, six goalkeeping attributes,
and two work rates. Users coming from the mainstream football-game vocabulary
(FIFA / EA Sports FC) expect **35 performance attributes** — 29 outfield detail
attributes plus 6 goalkeeping. The current vocabulary exposes only **33** (27
outfield + 6 goalkeeping), so two attributes that scouts and analysts routinely
reason about have nowhere to live:

1. **Attacking positioning** — a striker's knack for being in the right place to
   finish. Conceptually belongs to the *shooting* face.
2. **Dribbling (close ball control in motion)** — carrying the ball past
   opponents at speed, distinct from static *ball control*. Belongs to the
   *dribbling* face.

Because these have no home, a club that already grades players on them cannot
record the values, and positional fit ignores two skills that matter most for
exactly the roles (forwards, wide attackers) the product is meant to judge well.

This feature closes that gap while preserving the two behaviours the
constitution makes non-negotiable: **a missing detail falls back to its headline
face, never to the overall rating**, and **nothing is imputed** — an attribute a
club never supplied is reported as absent, not guessed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Record the two missing skills (Priority: P1)

An analyst grading a striker can record how well the player finds finishing
positions, and grade a winger on close-control dribbling — the same two skills
every mainstream scouting vocabulary already has — instead of being forced to
fold them into a coarser face.

**Why this priority**: This is the core of the request. Without it the vocabulary
is simply incomplete for attacking players, and the rest of the feature (import,
published contract, docs) has nothing to describe.

**Independent Test**: Enter a player by hand with values for the two new
attributes; confirm they are stored, returned on read, and change that player's
positional fit for an attacking role relative to a player who scores low on them.

**Acceptance Scenarios**:

1. **Given** the player-by-hand editor, **When** an analyst sets attacking
   positioning and dribbling on a forward, **Then** both values persist and are
   returned unchanged on the next read.
2. **Given** two forwards identical except that one has high attacking
   positioning and the other low, **When** their fit for a striker role is
   computed, **Then** the two fits differ measurably.
3. **Given** a player profile that omits the two new attributes, **When** it is
   read, **Then** those attributes are reported as absent (not zero, not the
   overall rating) and the player remains valid.

### User Story 2 - Import files that already carry these columns (Priority: P2)

A club whose squad export already includes "attacking position" and "dribbling"
columns can import them without hand-mapping, in English or Spanish headers, the
same way every other attribute column is matched today.

**Why this priority**: The by-hand path (P1) makes the attributes usable; bulk
import is how real squads actually arrive. It depends on P1 existing but adds the
volume path.

**Independent Test**: Import a CSV whose header row includes the two new columns
(and a Spanish-locale variant); confirm the values land on the right attributes
and the preview maps them without manual intervention.

**Acceptance Scenarios**:

1. **Given** a CSV with recognised headers for the two new attributes, **When**
   it is previewed, **Then** both columns are mapped to the correct attributes.
2. **Given** a CSV that omits the two columns, **When** it is imported, **Then**
   it still imports cleanly and those attributes are simply absent for those
   players — nothing is invented.
3. **Given** a CSV with a genuinely unknown attribute column, **When** it is
   previewed, **Then** it is still rejected as unknown (the new attributes do not
   loosen validation).

### User Story 3 - The published contract tells the truth (Priority: P3)

Anyone reading what data the product needs — the reference vocabulary served by
the API, the README attribute section, and the data-model doc — sees the updated,
accurate counts and the two new attributes grouped under the right faces.

**Why this priority**: The product's self-description is a first-class promise
here ("this is the question the product answers about itself"), but it follows
the actual capability rather than leading it.

**Independent Test**: Read the published vocabulary and the docs; confirm they
list 35 performance attributes (and the new total) with correct grouping and
that the numbers agree across every place they appear.

**Acceptance Scenarios**:

1. **Given** the published reference vocabulary, **When** it is read, **Then** it
   lists attacking positioning under shooting and dribbling under dribbling.
2. **Given** the README and data-model doc, **When** the attribute counts are
   read, **Then** they state 35 performance attributes and agree with the
   published vocabulary and with each other.

### Edge Cases

- **Absent attribute**: a player without the new attributes must fall back to the
  headline face for fit (attacking positioning → shooting, dribbling → dribbling
  face), never to the overall rating, and must report the raw attribute as absent.
- **Existing stored players**: profiles saved under the old vocabulary must remain
  valid unchanged; the new attributes are optional and are not back-filled.
- **Key collision**: the new close-control attribute shares its everyday name
  ("dribbling") with an existing headline face; the two must remain distinguishable
  so neither validation nor fallback confuses them (see Assumptions).
- **Positional weighting integrity**: after the two attributes enter the
  position-fit weighting, every position's fit must remain well-formed (no
  position silently double-counts or drops a face because a new detail was added).
- **Unknown columns still rejected**: adding two attributes must not weaken the
  rule that an unrecognised attribute key is refused.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The performance attribute vocabulary MUST expose exactly 35
  attributes — 29 outfield detail attributes and 6 goalkeeping — up from 33.
- **FR-002**: The vocabulary MUST include an **attacking positioning** attribute
  grouped under the shooting face.
- **FR-003**: The vocabulary MUST include a **dribbling (close ball control in
  motion)** attribute grouped under the dribbling face, distinct from the existing
  static ball-control attribute.
- **FR-004**: When either new attribute is absent for a player, positional fit
  MUST fall back to the attribute's headline face value, never to the player's
  overall rating — identical to the fallback for every other detail attribute.
- **FR-005**: No new value may be imputed across data tiers. An attribute a club
  never supplied MUST be reported as absent, not estimated.
- **FR-006**: Bulk import MUST recognise the two new attributes via
  case-insensitive header aliases covering at least English and Spanish labels,
  consistent with how existing attribute columns are matched.
- **FR-007**: The published reference vocabulary MUST list the two new attributes
  and their headline grouping.
- **FR-008**: Positional fit weighting MUST incorporate the two new attributes for
  the positions where they are relevant, while every position's weighting remains
  well-formed.
- **FR-009**: Player records stored under the previous vocabulary MUST remain
  valid without modification; the new attributes are optional additions.
- **FR-010**: All self-descriptions of the vocabulary (published reference, README
  attribute section, data-model documentation) MUST state the updated counts and
  agree with one another.
- **FR-011**: Validation MUST continue to reject unrecognised attribute keys and
  out-of-range values; the two new keys are the only additions to the accepted set.
- **FR-012** *(open decision)*: Whether **heading accuracy** is moved from the
  shooting face to the defending face to match the exact standard grouping.
  [NEEDS CLARIFICATION: relocating heading accuracy changes which headline face it
  falls back to and shifts it between two position-weight groups — do we mirror the
  external standard exactly, or keep heading under shooting to avoid changing the
  behaviour of existing profiles?]

### Key Entities *(include if feature involves data)*

- **Player performance attribute vocabulary**: the enumerated set of performance
  attributes, each belonging to exactly one headline face (or to goalkeeping), and
  the fallback each detail uses when absent. This feature grows the outfield detail
  set from 27 to 29 and, consequently, the full attribute-key set.
- **Player profile**: a player's recorded attribute values. Gains two optional
  attributes; existing profiles are unaffected until a value is supplied.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The published vocabulary and both documents report **35 performance
  attributes** (and the corresponding new total key count), with 100% agreement
  across all three sources.
- **SC-002**: A correctly-headed CSV containing the two new columns (English or
  Spanish) maps both to the right attributes with **zero** manual mapping steps.
- **SC-003**: For a player who omits the two new attributes, positional fit is
  **unchanged** from today for positions that do not weight them, and the two
  attributes are reported as absent (no imputed value) in 100% of such reads.
- **SC-004**: For an attacking role, two otherwise-identical players differing only
  in attacking positioning (or only in dribbling) produce **different** fit
  scores — the new attributes demonstrably affect the recommendation.
- **SC-005**: Every player record valid before the change remains valid after it,
  with no data migration required.

## Assumptions

- **Naming**: the attacking-positioning attribute is a new key under the shooting
  face; the new dribbling attribute is a distinct key from the existing `dribbling`
  headline face and from the existing static ball-control detail, so validation and
  fallback never conflate them. Exact internal key strings are an implementation
  concern for `/speckit-plan`; the display labels are "Attacking positioning /
  Posicionamiento ofensivo" and "Dribbling / Regates".
- **Scope of "35"**: the count of 35 refers to *performance* attributes (outfield
  detail + goalkeeping). The six headline faces and two work rates are counted
  separately, so the full attribute-key set grows from 41 to 43 keys (or 43 if
  FR-012 keeps heading in place; heading relocation does not change the total).
- **Fallback parents**: attacking positioning falls back to shooting; the new
  dribbling attribute falls back to the dribbling face — mirroring the standard's
  grouping.
- **No backfill / no migration**: existing seed and stored data are left as-is; the
  new attributes are simply absent until supplied, consistent with honesty by
  construction.
- **Standard of reference**: "the 35-attribute standard" is the mainstream
  football-game outfield+GK attribute layout the user enumerated; it is the target
  the vocabulary is aligned to, not a contractual external dependency.

## Constitutional Alignment

- **Honesty by construction / nothing imputed**: FR-004, FR-005, SC-003 and the
  no-backfill assumption keep absent attributes absent and fallback face-scoped.
- **Data contract is data**: FR-007 and FR-010 keep the published vocabulary and
  the docs as the single, agreeing source of truth (SC-001).
- **Tests assert the real contract**: the acceptance scenarios and success criteria
  are written to become the assertions that guard this behaviour.
