"""Physical Domain: household physical state management for HomeOS.

Manages inventory, storage nodes, procurement, consumption, and expiry via an
event-sourced model. Replaces the former Nox-Chef system.

Core principle: do not optimize behavior; prevent invalid future states.
"""
