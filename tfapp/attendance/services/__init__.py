"""
Attendance domain services: time processing, engine rules, balances, weekly reconciliation.

Implementation modules:
- time_processing: schedule and punch normalization helpers
- attendance_engine: tardy / override / occurrence orchestration
- balance_service: PTO and personal balance application
- weekly_reconciliation: payroll week finalize and revert
- attendance_types: AttendanceResult dataclass (interpretation layer)
"""
