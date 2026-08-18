@AGENTS.md

## Claude Code 固有

指示の正は [`AGENTS.md`](AGENTS.md) にある。Claude Code は `AGENTS.md` を読まないので、
このファイルの冒頭 1 行でインポートしている。ここに書くのは Claude Code でしか意味を
持たないことだけで、規範そのものは書かない
→ [ADR-0019](docs/adr/0019-agent-agnostic-instructions.md)

- ADR の作成・更新は `/adr` skill を使う
- 遡り作成の一次資料になった設計メモは
  `~/.claude/plans/tradingcopilot-trading-copilot-morning-b-toasty-duckling.md`
- スキルは `.claude/skills/` から読み込まれるが、そこにあるのは symlink で、実体は
  `.agents/skills/` にある。**編集は実体側に対して行う**
