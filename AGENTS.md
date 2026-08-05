# Codex compatibility

The project Codex configuration treats `CLAUDE.md` files as scoped instruction
files, so their guidance applies when working in those directories. `AGENTS.md`
remains preferred wherever both files exist.

## Claude Code compatibility

- References to Claude mean the active coding agent and therefore apply to Codex too.
- Claude slash commands are not directly executable in Codex. Read the corresponding
  command file under `.claude/commands/` and follow its instructions instead.
- Repository skills are shared with Codex through `.agents/skills`. Use the matching
  skill whenever a repository instruction file requires one.
- Claude-specific UI, hook, subagent, or permission instructions should be translated
  to the closest available Codex behavior while preserving their intent and safety.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->
