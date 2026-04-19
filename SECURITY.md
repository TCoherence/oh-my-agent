# Security Policy

Oh My Agent is a **single-user, self-hosted** tool. The maintainers take security issues seriously and appreciate responsible disclosure.

## Reporting a vulnerability

Please **do not** file a public GitHub issue for a vulnerability report.

Email security reports to **tcoherence@gmail.com** with:

- A short description of the issue and its impact.
- Steps to reproduce, ideally with a proof-of-concept.
- Affected version (`oh-my-agent --version`).
- Your operating environment (local venv vs Docker, Python version, OS).

You should receive an acknowledgement within **72 hours**. Expect a substantive follow-up (confirmation, patch ETA, or request for more detail) within **7 days**.

## Scope

In-scope:

- The published `oh_my_agent` Python package
- Container images built from the repo's `Dockerfile` and `compose.yaml`
- Skill files under `skills/` and bundled automation examples
- Memory / SQLite persistence and the `AuthFlow` credential storage layer
- Discord gateway, automation scheduler, and runtime service

Out-of-scope:

- Vulnerabilities in upstream dependencies (please report to them directly — you can cc us if the issue is triggerable via Oh My Agent)
- Vulnerabilities that require a compromised host machine or stolen Discord bot token (the threat model assumes the operator protects those)
- Denial-of-service achievable only by exhausting host resources (disk, RAM, CPU)
- Social-engineering attacks against the operator

## Supported versions

We patch the **latest released minor version** on the `main` branch.

| Version | Supported |
|---------|-----------|
| 0.9.x   | ✅ (current RC / contract-freeze) |
| 0.8.x   | ⚠️ security-only, best effort       |
| < 0.8   | ❌ no longer supported              |

Once `1.0` ships, `1.x` becomes the supported line and `0.9` moves to security-only.

## Credential handling

Oh My Agent stores provider credentials (Discord tokens, CLI auth cookies, etc.) on the local filesystem under `~/.oh-my-agent/auth/`. Files are written with `chmod 0600`. The `AuthFlow` and credential store only read / write these files; they are never transmitted to external services except as required by the relevant provider (e.g. Bilibili's login API).

If you discover a path where credentials could leak to logs, messages, attachments, or external services, that is in-scope.

## Safe harbor

Good-faith security research that follows this policy will not be subject to legal action by the maintainers. Please make a best-effort attempt to avoid privacy violations, data destruction, and service disruption during your testing.
