# CLAUDE.md

Role: Claude = architect/reviewer; Codex = coder. Quota saving is top priority.

## Hard rules
- Commit only as the configured git user (Sohan Mandal). NEVER add Co-Authored-By trailers, "Generated with" lines, or any AI/bot attribution.
- Never create .github/dependabot.yml or any bot/app config. Codex never commits or pushes; Claude reviews, commits and pushes.
- Specs live in docs/PLAN.md and docs/TASKS.md. Work only on the task you are given; keep diffs small.
- Save tokens: no unnecessary file dumps, no long explanations. Report: files changed + how to verify.

## Project
NetCoop AI: multi-agent network fault diagnosis (Python 3.14, pydantic v2, FastAPI, networkx, optional Gemini).
Architecture: docs/PLAN.md. Task list + acceptance checks + input formats: docs/TASKS.md. Protocol: docs/PROTOCOL.md.
Must always run with `--no-llm`. Tests: `pytest -q`. Update the task's Status in docs/TASKS.md when done.
