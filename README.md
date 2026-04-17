# HomeOS

## System Definition

HomeOS is a deterministic household state operating system.

It manages projected state across:

- Financial domain
- Physical domain
- Operational domain

HomeOS prevents invalid future states.

---

## Core Principle

Do not optimize behavior.
Prevent invalid future states.

---

## System Hierarchy

1. HomeOS (constraint authority)
2. Claude (planning layer)
3. Validator (enforcement layer)
4. OpenCLAW (execution layer)
5. Tools (Home Assistant, SSH, APIs)
6. World (real state)

---

## System Loop

Event → Projection → Constraints → Plan → Validate → Execute → Event

---

## Hard Constraints

- All Tier-1 obligations must be met
- No future liquidity breach allowed
- No critical inventory depletion allowed
- No operational conflicts allowed

---

## Failure

- missed obligation
- liquidity breach
- invalid projected state

---

## Design Rule

Constraints override planning and execution.
