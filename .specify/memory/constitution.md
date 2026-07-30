# ElevenMetric Constitution

## Core Principles

### I. Honesty by Construction (NON-NEGOTIABLE)
The system must never quietly overstate what it knows. Missing inputs produce
`null`, never an estimate. Every report carries `data_completeness` and
`confidence`, and every recommendation is scaled by both. Where a model is
knowingly unrealistic, the docs say so and the test asserts a ceiling rather
than pretending the behaviour is correct. Any feature that would let the product
present a guess as a measurement violates this constitution and must be redesigned.

### II. Nothing Imputed Across Data Tiers
Capability scales with the data supplied, and no value is imputed across tiers.
A report built from event data alone reports tracking-only fields (e.g.
`time_possession_pct`) as `null` rather than guessing them. A feature added at
one tier must not silently backfill fields that require a higher tier of input.

### III. Tenant Isolation Is Structural, Not Incidental
This is a multi-tenant platform with a shared database and row-level tenant
discrimination. Every business table carries `tenant_id`, and routes never build
queries directly — they go through `TenantScope`, the only sanctioned way to
construct a statement for a scoped model. An object belonging to another tenant
returns **404, not 403**: existence is itself private. New scoped models and
routes MUST use `TenantScope` and MUST be covered by isolation tests.

### IV. Provenance Is Tracked and Visible
Models ship as bootstrap priors fitted on a documented generative process, not
on real matches, and say so via `provenance` in the registry. Simulated output
is labelled `engine="simulated"` from the job record through to the report
summary and the UI banner; a generated opponent is reported as generated. Every
recommendation carries the numbers behind it under `evidence`. No output may
obscure where it came from.

### V. Tests Assert the Real Contract
Tests encode the guarantees above, not just happy paths. Tenant isolation,
`null`-on-missing-input, and knowingly-unrealistic ceilings are all asserted in
the suite. A change that alters one of these behaviours must update the asserting
test deliberately and explain why — a silently deleted assertion is a regression
in the contract, not a passing build.

## Additional Constraints

- **Backend**: Python / FastAPI / SQLAlchemy / Pydantic. Business logic lives in
  `services/`; routes stay thin and go through `TenantScope`.
- **Frontend**: vanilla HTML/CSS/JS — no build step and no runtime dependencies.
  Keep it that way unless a change is justified against this constraint.
- **The data contract is data.** The input contract is served from
  `GET /api/v1/meta/data-requirements` and documented in `docs/DATA_MODEL.md`.
  Changes to what the product needs update that single source, not scattered copies.
- **Optional dependencies degrade honestly.** Without the CV extras the API still
  runs and reports `engine="simulated"`; optional features must fail visible, not silent.

## Development Workflow

- Specs, plans, and tasks are managed with **GitHub Spec Kit** (`.specify/`, the
  `/speckit-*` skills). Non-trivial features start from a spec, not from code.
- The codebase can be explored as a knowledge graph via **graphify**
  (`graphify-out/`, the `/graphify` skill) — use it to trace relationships before
  changing cross-cutting code.
- The test suite (`cd backend && python -m pytest`) is the gate. A change that
  touches a constitutional behaviour must show the relevant tests still assert it.

## Governance

This constitution supersedes ad-hoc practice. Any PR that relaxes a Core
Principle must call it out explicitly and justify it; complexity and any loss of
honesty must be justified, never assumed. Amendments are made by editing this
file with a version bump and a dated entry below, and by updating any spec,
plan, or checklist templates that reference the changed principle.

**Version**: 1.0.0 | **Ratified**: 2026-07-30 | **Last Amended**: 2026-07-30
