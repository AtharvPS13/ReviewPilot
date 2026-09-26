# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in ReviewPilot, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email: fake34811289@gmail.com

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

I will respond within 48 hours and work with you on a fix before any public disclosure.

## Sandbox Security Model

ReviewPilot's Docker sandbox uses these security constraints:

- `--network=none`: No internet access from sandbox containers
- `--cap-drop=ALL`: All Linux kernel capabilities dropped
- `--user=1000:1000`: Non-root execution
- `--security-opt=no-new-privileges`: Cannot escalate privileges
- `--memory=1g`: Capped memory usage
- `--pids-limit=128`: Limited process count

These constraints are designed to prevent untrusted code (LLM-generated patches) from escaping the sandbox or accessing sensitive resources.
