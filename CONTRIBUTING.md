# Contributing to ReviewPilot

Thanks for your interest in contributing! Here's how to get started.

## Setup

```bash
# Clone and install
git clone https://github.com/AtharvPS13/reviewpilot.git
cd reviewpilot
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific module
pytest tests/test_diff_parser.py -v

# With coverage
pytest tests/ --cov=src/reviewpilot --cov-report=term-missing
```

## Linting and Type Checking

```bash
# Lint
ruff check src/ tests/

# Auto-fix lint issues
ruff check src/ tests/ --fix

# Type check
mypy src/reviewpilot/ --ignore-missing-imports
```

## Project Structure

```
src/reviewpilot/
    ingestion/       # Diff parsing and file filtering
    analysis/        # AST analysis, context building, deterministic checks
    review/          # LLM integration, models, prompts, token optimization
    sandbox/         # Docker verification engine
    github/          # GitHub API, comment formatting, lifecycle
    main.py          # Pipeline orchestrator (GitHub Action entry)
    cli.py           # CLI interface (local usage)
    config.py        # YAML config loader
```

## Adding a New Language

ReviewPilot uses tree-sitter for AST parsing. To add support for a new language:

1. Install the tree-sitter grammar package (e.g., `tree-sitter-go`)
2. Add it to `pyproject.toml` dependencies
3. Create a new analyzer in `src/reviewpilot/analysis/` (see `js_analyzer.py` as reference)
4. Register the language in `analyze_file()` in `ast_analyzer.py`
5. Add the file extension to `EXTENSION_MAP` in `ast_analyzer.py`
6. Write tests in `tests/`

## Adding a New Deterministic Check

To add a new pattern-based check that runs without any LLM:

1. Open `src/reviewpilot/analysis/deterministic.py`
2. Add a new `PatternRule` to the `RULES` list
3. Set the regex pattern, severity, category, and explanation
4. Optionally set `file_extensions` to limit which file types it applies to
5. Optionally set `negative_pattern` to filter out false positives
6. Add a test in `tests/test_deterministic.py`

## Commit Messages

We use conventional commits:

- `feat:` new features
- `fix:` bug fixes
- `test:` adding or updating tests
- `docs:` documentation changes
- `chore:` maintenance tasks
- `refactor:` code changes that don't fix bugs or add features

Scope is optional but encouraged: `feat(sandbox): add timeout handling`

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests as needed
4. Run the lint and test suite locally
5. Open a PR with a clear description of what you changed and why

## Code Style

- Python 3.11+ features are welcome (type hints, match statements, etc.)
- Use type hints everywhere
- Docstrings on all public functions (Google style)
- Keep functions focused and under ~50 lines when possible
- No print statements in library code (use `logging` instead)
