"""HomeOS Validator: enforcement layer.

Sits between the planning layer (Claude) and the execution layer (OpenCLAW).
No execution without validation. Rejects any plan that fails any of the six
validation steps:

    1. Schema validation
    2. Operation whitelist check
    3. Parameter validation
    4. Risk classification
    5. Approval gating
    6. Execution (handed off to OpenCLAW)

Core rule: if validation fails, do not execute.
"""
