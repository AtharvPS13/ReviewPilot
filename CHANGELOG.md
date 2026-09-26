# Changelog

All notable changes to ReviewPilot will be documented here.

## [0.1.0] - 2026-09-22

### Added
- **Diff Parsing**: Custom unified diff parser with dual-counter line tracking for both old/new files
- **File Filtering**: Glob-based filtering for lockfiles, binaries, generated code, vendor directories
- **AST Analysis (Python)**: Tree-sitter parser extracting functions, classes, decorators, docstrings, imports
- **AST Analysis (JS/TS)**: Tree-sitter parser for JavaScript and TypeScript with arrow function and export support
- **Context Builder**: Token-efficient LLM prompts using full source for modified functions, signatures for dependencies
- **Deterministic Checks**: Zero-cost pattern checks for hardcoded secrets, eval(), SQL injection, bare except, TODOs
- **Pydantic Models**: Structured schemas for LLM input/output with severity, category, and line ranges
- **LLM Reviewer**: LiteLLM + instructor integration for provider-agnostic structured reviews
- **Prompt Templates**: Static system prompt (cache-eligible) with few-shot examples and self-repair prompts
- **Sandbox Patch Applier**: Line-range code replacement for testing suggestions
- **Docker Runner**: Isolated container execution with strict security (no network, no root, capped memory)
- **Verification Pipeline**: Apply -> test -> self-repair -> retry -> downgrade orchestration
- **GitHub API Client**: Fetch PR diffs, post batched review comments, handle 422 fallbacks
- **Comment Formatter**: Markdown formatting with severity badges, verification status, suggestion blocks
- **Comment Lifecycle**: Deduplication via hidden markers, resolved issue tracking, summary updates
- **Model Cascading**: Route files to different LLM tiers based on complexity and security sensitivity
- **Token Optimizer**: Context window budgeting, file prioritization, chunking for large PRs
- **CLI**: Local usage with review, check (deterministic-only), parse (debug), and analyze (debug) commands
- **GitHub Action**: Docker container action with configurable inputs for LLM, sandbox, and review settings
- **CI Pipeline**: GitHub Actions workflow with tests, linting, and type checking on Python 3.11/3.12
- **Configuration**: YAML-based config with Pydantic validation and sensible defaults
