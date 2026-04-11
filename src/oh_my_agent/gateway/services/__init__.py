# Service-layer modules for platform-agnostic business logic.
#
# Services own task state transitions, HITL resolution, automation
# control, and operator report assembly.  Platform adapters (Discord,
# Slack, ...) only parse input, call services, and render output.
