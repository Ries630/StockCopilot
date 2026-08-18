# ADR-0019: 指示ファイルとスキルをエージェント非依存の場所に置き、Claude Code へは橋渡しする

- ステータス: 承認済み
- 日付: 2026-08-18
- 関連: [#52](https://github.com/Ries630/StockCopilot/issues/52)、[ADR-0018](0018-bundle-skills-in-repo.md)

## 背景

プロジェクト指示は `CLAUDE.md`、スキル定義は `.claude/skills/` にあり、どちらも
Claude Code しか読まない。Codex も使いたいという動機から調べたが、目的は特定の
エージェントへの追従ではなく、ベンダ依存を外して共通規格に乗せることに置いた。

調べたところ、指示ファイルとスキルで規格の成熟度が違っていた。

**指示ファイルは共通規格がある。** `AGENTS.md` はリポジトリルートに置くことまで
仕様が定めており、20 以上のツールが対応している。Claude Code だけが読まず、公式
ドキュメントが「Claude Code は `CLAUDE.md` を読みます。`AGENTS.md` ではありません」と
明記したうえで、`CLAUDE.md` から `@AGENTS.md` でインポートするか symlink を張る
回避策を案内している。

**スキルは規格が置き場所を定めていない。** [Agent Skills の仕様](https://agentskills.io/specification)
が規定するのは `SKILL.md` の書式（フォルダ構成、frontmatter のフィールドと制約、
progressive disclosure）までで、探索ディレクトリの規定は一切ない。その穴を埋める
慣習として `.agents/skills` があり、複数ベンダが採用している。

| ツール | プロジェクトの探索パス |
|---|---|
| Codex | `.agents/skills`（公式ドキュメントに載るリポジトリ層はこれのみ） |
| Gemini CLI | `.agents/skills` / `.gemini/skills`。前者を優先し「異なる AI ツール間で相互運用可能なパス」と明記 |
| Cursor | `.agents/skills` / `.cursor/skills`、互換で `.claude/skills` `.codex/skills` |
| OpenCode | `.agents/skills` / `.claude/skills` / `.opencode/skills` |
| Claude Code | `.claude/skills` のみ |

Gemini CLI が自社パスより `.agents/` を優先している点で、これは Codex 固有の流儀では
なく規格の外側で立ち上がった業界慣習だと判断した。そして両方の層で、慣習に乗って
いないのが Claude Code 1 つだけという同じ構図になっている。

現行の 2 つの SKILL.md は、移動前の時点で既に仕様に準拠していた（`name` が親
ディレクトリ名と一致、`description` は 1024 字以内）。移す必要があるのは中身ではなく
配置だけだった。

## 決定

正をエージェント非依存の場所に置き、Claude Code だけに橋を架ける。

- 指示は `AGENTS.md` を正とし、`CLAUDE.md` は `@AGENTS.md` のインポート 1 行だけを持つ
  （Claude 固有の記述が必要になったら、その下に足せる）
- スキルの実体を `.agents/skills/` に移し、`.claude/skills/<name>` をそこへの symlink にする
  （Claude Code はスキルディレクトリのエントリが symlink でも辿ると公式ドキュメントに明記がある）
- 規格準拠を CI で機械的に検証する（`skills-ref validate`。バージョンは固定する）

## 検討した代替

**実体を `.claude/skills/` に置いて `.agents/skills/` を symlink にする。** 逆向きでも
両対応にはなる。落としたのは対応範囲が狭いためで、この向きだと Cursor と OpenCode は
互換パスとして拾えるが、Codex と Gemini CLI は読めない。読まれる範囲が広い側を実体に
した。

**`CLAUDE.md` を `AGENTS.md` への symlink にする。** 公式が案内している選択肢で、
ファイルが 1 つ減る。当初は「Claude 固有の記述を置く場所が無くなる」ことを落とす理由に
したが、その記述として挙げていた `adr` skill と設計メモのパスは、レビューで
「ユーザー環境には依存するがエージェントには依存しない」と判断されて `AGENTS.md` に
移った。結果として `CLAUDE.md` に固有の記述は残っていない。

それでもインポートを選んでいる理由は 1 つだけで、固有の記述が必要になったときに
1 行足すだけで済むこと。切り替えのコストも低いので、必要が生じないまま推移するなら
symlink に寄せてよい。

**両方に実体を置いてコピーする。** 二重管理になり、単一情報源の規範に正面から反する。
写経は必ずどちらかが先に古くなる（ADR-0018 の背景で実際に起きていた）。

**CI での規格検証を入れない。** `skills-ref` は Development Status: Alpha で、README も
「demonstration purposes only」と書いている。それでも入れたのは、規格に乗せることが
この移行の目的そのものであり、準拠を目視に頼ると移行の意味が薄れるため。alpha である
代償はバージョン固定で受け止める。

**`.github/workflows/claude.yml`（Claude Code Action）も移行する。** 今回は対象外にした。
CI 統合であって指示の層とは別で、Codex 側の GitHub 連携は使う予定が立ってから足す。

## 結果

**Windows で clone すると symlink が壊れる。** git は symlink をモード 120000 で記録するが、
Windows では開発者モードか管理者権限が無いと実体化せず、リンク先パスを書いたただの
テキストファイルになる。その環境では Claude Code がスキルを読めない。macOS 単独運用
なので実害なしと判断している。

**スキルの編集先が 2 つ見える。** `.claude/skills/` 経由でも実体に到達できてしまうので、
symlink と知らずに触ると混乱する。ただし symlink 越しの編集は実体に届くため実害は
なく、注意書きは置いていない（自明な記述で指示ファイルを厚くしないため）。

**CI が外部サービスに依存する。** これまで CI の依存は ruff だけで、テストは
ネットワークにアクセスしない設計だった（→ [ADR-0005](0005-completed-bars-only.md) の
安定性の考え方と同根）。`skills-ref` を入れたことで、PyPI が落ちていると lint も
テストも無関係に CI が赤くなる。

**ADR-0018 の配置が古くなる。** 0018 は `.claude/skills/` に同封すると書いており、その
記述は本 ADR で置き換わる。ただし 0018 の決定の本体（汎用化して同封する / 契約を
`docs/` に集約する / 教訓を `journal/lessons.md` に分離する）は変わらないので、廃止には
していない。「どこに同封するか」だけが更新された状態であり、0018 を読むときはこの
ADR とセットで読む必要がある。

**エージェントが増えるたびに橋の要否を調べ直す必要がある。** `.agents/` を実体に
したことで多くのツールは無設定で読めるが、独自パスしか見ないツールが来たら
symlink を足す判断が要る。規格が探索パスを定めていない以上、この作業は無くならない。

## 再評価のサイン

- Claude Code が `.agents/skills` または `AGENTS.md` をネイティブに読むようになった
  → symlink と `CLAUDE.md` を消せる。橋が不要になったか確認する
- Agent Skills の仕様が探索ディレクトリを規定した → 慣習ではなく仕様に合わせ直す
- `skills-ref` が stable にならないまま更新が止まった、または CI を不安定にした
  → 検証を外すか、書式チェックを自前の軽いスクリプトに置き換える
