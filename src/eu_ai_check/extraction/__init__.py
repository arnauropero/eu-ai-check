"""Phase 2 extraction stage (SYSTEM_SPEC.md §3).

    free-text use case -> Claude (structured outputs, CaseFacts schema)
        -> validated CaseFacts -> conclusion-leakage check -> Case

Extraction captures facts only. Legal conclusions (risk tier, prohibited
practice, actor roles, substantial modification) are made downstream, in a
GroundedDetermination stage that retrieves the provision before concluding.
"""
