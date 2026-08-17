# Muti_Agent / DevFlow

Evidence-driven multi-agent software engineering runtime.

This repository is developed incrementally from a written engineering plan. Implementation must follow `docs/DEVELOPMENT_PLAN.md`; architecture changes should be recorded before code changes start.

## Current status

**Planning phase only. No application implementation has started yet.**

Next milestone: **V0.1 — Single Task Evidence Loop**.

See:

- `docs/DEVELOPMENT_PLAN.md` — authoritative implementation plan and milestones.
- `docs/ARCHITECTURE.md` — target architecture and component boundaries.

## Core principle

> Agents propose; evidence decides.

LLM agents may plan, implement, review, and repair, but task completion is decided by repository state, scope enforcement, deterministic verification, and independent review rather than by an agent claiming success.
