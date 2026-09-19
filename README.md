# ReviewPilot 🔍

> The only AI code reviewer that **proves its suggestions work** before posting them.

ReviewPilot is a GitHub Action that automatically reviews Pull Requests using AI. Unlike existing tools (Copilot, CodeRabbit, PR-Agent), ReviewPilot **sandbox-verifies** every code suggestion — applying patches in isolated Docker containers and running tests — before posting them on your PR.

## Why ReviewPilot?

Every AI code review tool has the same problem: **they trust LLM output blindly.** Studies show up to 50% of AI-generated code suggestions contain hallucinations — methods that don't exist, code that doesn't compile, or "fixes" that break tests.

ReviewPilot solves this by testing every suggestion before posting:

1. 📝 Parses your PR diff and identifies which functions changed (via Tree-sitter AST)
2. 🔍 Runs deterministic checks (hardcoded secrets, SQL injection, etc.) — zero LLM cost  
3. 🤖 Sends structured context to an LLM for deeper semantic review
4. 🧪 **Applies each suggestion in a Docker sandbox and runs your tests**
5. ✅ Only posts suggestions that are **verified to compile and pass tests**

## Status

🚧 **Under active development** — see the roadmap below.

## Tech Stack

- **Python 3.11+** — core language
- **Tree-sitter** — AST parsing for function extraction
- **Pydantic v2** — structured LLM output validation
- **Docker** — sandbox verification engine
- **LiteLLM** — multi-provider LLM support (OpenAI, Anthropic, Ollama, etc.)

## License

MIT
