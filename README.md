# ReviewPilot

**AI code reviewer that tests every suggestion before posting it.**

ReviewPilot is a GitHub Action that reviews your Pull Requests using LLMs. But unlike Copilot, CodeRabbit, or PR-Agent, it doesn't blindly trust the AI's output. Every code suggestion gets applied to a copy of your codebase, tested inside a Docker sandbox, and only posted if the tests actually pass.

## The Problem

AI code review tools hallucinate. A lot. Studies show up to 50% of LLM-generated code suggestions have issues: methods that don't exist, code that doesn't compile, or "fixes" that break your tests. Developers start ignoring all bot comments after seeing a few bad ones.

## How ReviewPilot Solves This

```
PR opened/updated
     |
     v
[1] Parse diff, filter out lockfiles/binaries/vendor
     |
     v
[2] Tree-sitter AST analysis: find which functions changed
     |
     v
[3] Deterministic checks: secrets, eval(), SQL injection (free, no LLM)
     |
     v
[4] LLM review with structured JSON output (Pydantic validated)
     |
     v
[5] For each suggestion with code:
     |-- Apply patch to temp copy of repo
     |-- Spin up Docker container (no network, no root, capped memory)
     |-- Run your test suite
     |-- Pass? --> Post with "Verified" badge
     |-- Fail? --> Feed error back to LLM, retry once
     |               |-- Still fails? --> Post as "Advisory" instead
     |
     v
[6] Post batched inline comments on the PR
```

## Quick Start

Add this to `.github/workflows/review.yml` in your repo:

```yaml
name: AI Code Review
on:
  pull_request:
    types: [opened, synchronize]

permissions:
  pull-requests: write
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: AtharvPS13/reviewpilot@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          llm-api-key: ${{ secrets.OPENAI_API_KEY }}
          test-command: "pip install -r requirements.txt && pytest"
```

## Configuration

Create a `.reviewpilot.yml` in your repo root:

```yaml
llm:
  provider: "openai"          # openai | anthropic | ollama | gemini
  model: "gpt-4o-mini"
  api_key_env: "OPENAI_API_KEY"

sandbox:
  enabled: true
  docker_image: "python:3.11-slim"
  test_command: "pip install -r requirements.txt && pytest"
  timeout_seconds: 120
  memory_limit: "1g"

review:
  max_comments: 15
  severity_threshold: "minor"  # critical | major | minor | nitpick

ignore:
  - "*.lock"
  - "package-lock.json"
  - "node_modules/**"
  - "dist/**"
```

## What It Catches

**Without any LLM (deterministic, zero cost):**
- Hardcoded API keys, secrets, passwords
- `eval()` / `exec()` usage
- SQL injection patterns (string interpolation in queries)
- Bare `except:` blocks, empty catch blocks
- TODO/FIXME left in new code

**With LLM (structured review):**
- Logic bugs, off-by-one errors
- Security vulnerabilities (auth bypass, injection)
- Performance issues (N+1 queries, blocking I/O in async)
- Missing error handling, resource leaks
- Architectural concerns

## Comment Format

Verified suggestions show up like this on your PR:

```
[Verified] Security | Critical

JWT decoded without signature verification. Any client can forge tokens.

    payload = jwt.decode(token, key=SECRET_KEY, algorithms=["HS256"])

> Tests pass with this change
```

If a suggestion fails sandbox testing, it gets downgraded:

```
[Advisory] Performance | Major

Database query inside a for-loop will cause N+1 queries.
Consider using a JOIN or batch query.

> Could not verify this suggestion automatically
```

## How Sandbox Verification Works

1. The LLM generates a code suggestion with exact line replacements
2. ReviewPilot copies your repo to a temp directory
3. It applies the replacement (simple line splice, not git patch)
4. It spins up a Docker container with these security constraints:
   - `--network=none` (no internet access)
   - `--cap-drop=ALL` (no kernel capabilities)
   - `--user=1000:1000` (non-root)
   - `--memory=1g` (capped memory)
   - `--pids-limit=128` (process limit)
5. Runs your test command inside the container
6. If tests pass, the suggestion is marked as verified
7. If tests fail, the error output is fed back to the LLM for one repair attempt
8. Container is torn down regardless of outcome

## Tech Stack

| Component | Technology | Purpose |
|:----------|:-----------|:--------|
| Language | Python 3.11+ | Core application |
| AST Parsing | tree-sitter | Extract function/class boundaries from code |
| Structured Output | Pydantic v2 + instructor | Validate LLM JSON responses |
| LLM Access | LiteLLM | Unified API for OpenAI, Anthropic, Ollama, Gemini |
| Sandbox | Docker SDK | Isolated container execution |
| GitHub API | PyGithub + httpx | Fetch diffs, post review comments |
| Config | PyYAML | Load `.reviewpilot.yml` |

## Architecture

```
reviewpilot/
├── src/reviewpilot/
│   ├── main.py                  # Pipeline orchestrator
│   ├── config.py                # YAML config loader
│   ├── ingestion/
│   │   ├── diff_parser.py       # Unified diff parser with line tracking
│   │   └── file_filter.py       # Filter lockfiles, binaries, etc.
│   ├── analysis/
│   │   ├── ast_analyzer.py      # Tree-sitter function/class extraction
│   │   ├── context_builder.py   # Build token-efficient LLM context
│   │   └── deterministic.py     # Regex pattern checks (no LLM)
│   ├── review/
│   │   ├── models.py            # Pydantic schemas for LLM I/O
│   │   ├── prompt_templates.py  # System/user prompts
│   │   └── llm_reviewer.py      # LiteLLM + instructor integration
│   ├── sandbox/
│   │   ├── patch_applier.py     # Line-range code replacement
│   │   ├── docker_runner.py     # Container lifecycle management
│   │   └── verifier.py          # Verify -> repair -> downgrade pipeline
│   └── github/
│       ├── api_client.py        # GitHub REST API wrapper
│       └── comment_formatter.py # Markdown formatting with badges
├── tests/
├── action.yml                   # GitHub Action definition
├── Dockerfile                   # Action container image
└── pyproject.toml
```

## Token Optimization

ReviewPilot doesn't just dump the entire diff into the LLM prompt. It uses AST-aware context building:

- **Modified functions** get their full source code with line numbers
- **Unmodified functions** in the same file get only their signatures (name, params, return type), cutting token usage by ~70%
- Lockfiles, binaries, and generated code are filtered out before any LLM call
- Deterministic checks run first, so obvious issues never hit the LLM

## Supported Languages

- Python (full AST support)
- JavaScript / TypeScript (planned)
- Go (planned)
- Java (planned)

## Development

```bash
# Clone the repo
git clone https://github.com/AtharvPS13/reviewpilot.git
cd reviewpilot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linter
ruff check src/ tests/
```

## License

MIT
