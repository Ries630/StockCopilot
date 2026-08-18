"""ウォッチリスト (保有検討中の銘柄) のテンプレート。

このファイルをコピーして `config/watchlist.py` を作る。

    cp config/watchlist.example.py config/watchlist.py

**`config/watchlist.py` はコミットしてはならない** (`.gitignore` 済み・CI で検査)。
このリポジトリは public であり、規範が守っているのは銘柄名の秘匿ではなく
資産と売買意図をリポジトリに残さないこと。ウォッチリストは購入意図そのものなので、
保有一覧と同じ扱いにする (AGENTS.md「public リポジトリ前提の規範」)。

ファイルが無い環境 (CI・クリーンクローン) では空リストとして扱われるので、
作らなくても screen.py は動く。
"""

# 日本株: 4 桁コードで書く (screen.py が .T を付けて正規化する)
WATCHLIST_JP: list[str] = []

# 米国株・ETF
WATCHLIST_US: list[str] = []
