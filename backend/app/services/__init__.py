"""Future shared identity and independent audit services.

Identity services must share the caller's Session. Audit services must open their
own Session and explicitly report/handle failures. Neither is implemented yet.
"""
