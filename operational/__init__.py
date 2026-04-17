"""Operational Domain: tasks, schedules, and resource reservations.

Hard constraint: no operational conflicts (resource overbooking, missed
deadlines, tasks with unmet resource requirements).

Event sourced; projection defines truth.
"""
