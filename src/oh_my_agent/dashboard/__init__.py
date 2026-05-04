"""Local read-only monitoring dashboard for oh-my-agent.

Standalone process (entry point ``oma-dashboard``) that aggregates SQLite,
log, and YAML data into a single HTML page. Loopback-only by deployment
convention; no auth.

See ``docs/EN/monitoring.md`` for operator-facing setup notes.
"""
