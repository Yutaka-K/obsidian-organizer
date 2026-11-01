"""提案レポート（Markdown）と、適用モード用のデータ（JSON）を作る"""
from __future__ import annotations

import json
import posixpath
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .analyze import Analysis, build_toc_content
from .config import Config
from .textutil import kakasi_available
from .vault import Vault

DATA_DIR_NAME = ".提案データ"
REPORT_PREFIX = "提案レポート_"

_MD_SPECIAL = set("\\`*_[]#<>|~=$%^{}")


def md_escape(s: str) -> str:
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in s)


def code(s: str) -> str:
    """インラインコードとして表示（中にバッククォートがあるときは2重にする）"""
    if "`" in s:
        return f"`` {s} ``"
    return f"`{s}`"


class Writer:
    def __init__(self, vault: Vault, cfg: Config):
        self.vault = vault
        self.cfg = cfg
        self.vault_name = vault.root.name

    def note(self, rel: str) -> str:
        """レポートに書くノート名（クリックで Obsidian が開くリンク付き）"""
        name = posixpath.basename(rel)
        name = name[:-3] if name.lower().endswith(".md") else name
        folder = posixpath.dirname(rel)
        label = md_escape(name)
        if self.cfg.obsidian_uri:
            uri = ("obsidian://open?vault=" + quote(self.vault_name, safe="")
                   + "&file=" + quote(rel[:-3] if rel.lower().endswith(".md") else rel, safe=""))
            label = f"[{label}]({uri})"
        else:
            label = f"**{label}**"
        if folder:
            label += f"（{md_escape(folder)}/）"
        return label


def checkbox(checked: bool) -> str:
    return "- [x]" if checked else "- [ ]"


def build_report(vault: Vault, cfg: Config, an: Analysis, created: datetime) -> tuple[str, dict]:
    w = Writer(vault, cfg)
    items: dict[str, dict] = {}
    touched: set[str] = set()
    L: list[str] = []

    n_link_sources = len({c.source for c in an.candidates})
    tag_a = [g for g in an.tag_groups if g.level == "A"]
    tag_b = [g for g in an.tag_groups if g.level == "B"]
    fixable = [b for b in an.broken if b.fix]

    L += [
        "# Obsidian ノート整理　提案レポート",
        "",
        f"- 作成日時：{created.strftime('%Y年%m月%d日 %H:%M:%S')}",
        f"- 保管庫：{code(str(vault.root))}",
        f"- 調べたノート：{len(vault.notes)} 件",
        "",
        "> [!info] これは「提案」だけです",
        "> このレポートを作っただけでは、あなたのノートは**一切変更されていません**。",
        "> ",
        "> **適用したい提案にはチェック ☑ を入れ、適用したくない提案はチェックを外してください。**",
        "> チェックを付け終えたらこのファイルを保存し、「提案を適用.bat」をダブルクリックすると、",
        "> バックアップを取ったうえで、チェックの入った提案だけがノートに反映されます。",
        "> ",
        "> - Obsidian で読んでいる場合：チェックボックスをクリックするだけで切り替わります。",
        "> - メモ帳で開いている場合：`- [x]` がチェックあり、`- [ ]` がチェックなしです。",
        "> - 行の先頭にある `L-0001` のような番号は、適用するときの目印です。消さないでください。",
        "",
        "## まとめ",
        "",
        "| 項目 | 件数 | 適用できる提案 |",
        "| --- | ---: | --- |",
        f"| 1. 孤立ノート | {len(an.orphans)} 件 | （一覧のみ） |",
        f"| 2. リンク候補 | {len(an.candidates)} 件（{n_link_sources} ノート） | リンクを追加 |",
        f"| 3. リンク切れ | {len(an.broken)} 件 | うち {len(fixable)} 件は修正案あり |",
        f"| 4. タグの表記ゆれ | {len(an.tag_groups)} グループ | タグを統一 |",
        f"| 5. 目次ノート案 | {len(an.tocs)} 件 | 目次ノートを新しく作成 |",
        "",
    ]
    if vault.unreadable:
        L += ["> [!warning] 読み込めなかったファイル",
              "> 文字コードが UTF-8 ではないなどの理由で、次のファイルは調べられませんでした。"]
        L += [f"> - {w.note(r)}" for r in vault.unreadable]
        L.append("")

    # ---------- 1. 孤立ノート ----------
    L += ["---", "", f"## 1. 孤立ノート（{len(an.orphans)} 件）", "",
          "ほかのどのノートからもリンクされていないノートです。",
          "関連するノートからリンクを張るか、目次ノートに入れると見つけやすくなります。", ""]
    if an.orphans:
        no_out = [o for o in an.orphans if o.outgoing == 0]
        with_out = [o for o in an.orphans if o.outgoing > 0]
        if no_out:
            L += [f"### リンクが1本もないノート（{len(no_out)} 件）", "",
                  "ほかのノートへのリンクも持っていない、完全に独立したノートです。", ""]
            L += [f"- {w.note(o.rel)}　（本文 {o.chars} 文字）" for o in no_out]
            L.append("")
        if with_out:
            L += [f"### ほかのノートへのリンクは持っているノート（{len(with_out)} 件）", ""]
            L += [f"- {w.note(o.rel)}　（リンク先 {o.outgoing} 件・本文 {o.chars} 文字）" for o in with_out]
            L.append("")
    else:
        L += ["孤立ノートはありません。", ""]

    # ---------- 2. リンク候補 ----------
    L += ["---", "", f"## 2. リンク候補（{len(an.candidates)} 件）", "",
          "ほかのノートの名前が本文に出てくるのに、まだリンクになっていない箇所です。",
          "適用すると、**そのノートの中で最初に出てくる1か所だけ**を `[[ノート名]]` の形に書き換えます。",
          "（コードブロック・見出し・すでにリンクになっている部分は書き換えません）", ""]
    if an.candidates:
        by_source: dict[str, list] = {}
        for c in an.candidates:
            by_source.setdefault(c.source, []).append(c)
        n = 0
        for source, cands in by_source.items():
            L += [f"### {w.note(source)}（{len(cands)} 件）", ""]
            for c in cands:
                n += 1
                item_id = f"L-{n:04d}"
                link_name = vault.link_text_for(c.target)
                if c.matched == link_name:
                    new_text = f"[[{link_name}]]"
                else:
                    new_text = f"[[{link_name}|{c.matched}]]"
                before, hit, after = c.context
                ctx = f"…{md_escape(before)}**{md_escape(hit)}**{md_escape(after)}…"
                extra = f"、ほか {c.count - 1} か所にも出てきます" if c.count > 1 else ""
                alias = "（別名で一致）" if c.via_alias else ""
                L.append(f"{checkbox(cfg.link_checked)} {code(item_id)} → {w.note(c.target)}{alias}"
                         f"　{c.line}行目：{ctx}　⇒ {code(new_text)}{extra}")
                items[item_id] = {"type": "link", "source": c.source, "target": c.target,
                                  "matched": c.matched, "new_text": new_text}
                touched.add(c.source)
            L.append("")
    else:
        L += ["リンク候補はありません。", ""]

    # ---------- 3. リンク切れ ----------
    L += ["---", "", f"## 3. リンク切れ（{len(an.broken)} 件）", "",
          "存在しないノート（またはファイル）へのリンクです。", ""]
    if an.broken:
        if fixable:
            L += [f"### 修正案があるもの（{len(fixable)} 件）", "",
                  "全角／半角やひらがな／カタカナなどの違いだけで、正しいノートが1つに決まるものです。", ""]
            for i, b in enumerate(fixable, 1):
                item_id = f"B-{i:04d}"
                L.append(f"{checkbox(True)} {code(item_id)} {w.note(b.source)}　{b.line}行目："
                         f"{code('[[' + b.target + ']]')} → {code('[[' + b.fix + ']]')} に修正")
                items[item_id] = {"type": "broken_fix", "source": b.source, "old": b.target, "new": b.fix}
                touched.add(b.source)
            L.append("")
        rest = [b for b in an.broken if not b.fix]
        if rest:
            L += [f"### 修正案がないもの（{len(rest)} 件）", "",
                  "ノートを新しく作るか、リンクを正しい名前に直してください（自動では直しません）。", ""]
            by_target: dict[str, list] = {}
            for b in rest:
                by_target.setdefault(b.target, []).append(b)
            for target, bs in sorted(by_target.items(), key=lambda x: (-len(x[1]), x[0])):
                shown = target if bs[0].kind == "md" else f"[[{target}]]"
                kind = "（埋め込み）" if bs[0].embed else ""
                L.append(f"- {code(shown)}{kind}　{len(bs)} か所")
                for b in bs:
                    L.append(f"    - {w.note(b.source)}　{b.line}行目")
                sim = bs[0].similar
                if sim:
                    L.append("    - 似た名前のノート：" + "、".join(md_escape(s) for s in sim))
            L.append("")
    else:
        L += ["リンク切れはありません。", ""]

    # ---------- 4. タグの表記ゆれ ----------
    L += ["---", "", f"## 4. タグの表記ゆれ（{len(an.tag_groups)} グループ）", "",
          "書き方が違うだけで、同じ意味と思われるタグのグループです。",
          "適用すると、グループ内のタグをすべて「統一先」のタグに書き換えます（本文の `#タグ` とフロントマターの tags の両方）。",
          "統一先を変えたいときは、`統一先：` のあとの `#タグ名` を書き換えてから保存してください。", ""]
    n = 0

    def tag_lines(groups, checked: bool):
        nonlocal n
        for g in groups:
            n += 1
            item_id = f"T-{n:04d}"
            variants = "、".join(f"{code('#' + t)}（{c}ノート）" for t, c in g.variants if t != g.target)
            target_count = dict(g.variants).get(g.target, 0)
            L.append(f"{checkbox(checked)} {code(item_id)} 統一先：{code('#' + g.target)}"
                     f"（{target_count}ノート）　← 書き換えるタグ：{variants}")
            items[item_id] = {"type": "tag", "from": [t for t, _ in g.variants], "to": g.target,
                              "files": g.files}
            touched.update(g.files)

    if tag_a:
        L += [f"### 表記の違い（{len(tag_a)} グループ）", "",
              "全角／半角・大文字／小文字・ひらがな／カタカナ・記号（- _ ・ など）だけが違うタグです。", ""]
        tag_lines(tag_a, True)
        L.append("")
    if tag_b:
        L += [f"### 読みが同じ・つづりが似ている（要確認：{len(tag_b)} グループ）", "",
              "漢字とかなで読みが同じもの、のばす音「ー」の有無、つづりがよく似ているものです。",
              "**意味が違うタグがまざっている可能性がある**ため、最初はチェックを外してあります。",
              "内容を確認して、統一してよいものだけチェックを入れてください。",
              "（上の「表記の違い」と同じタグを含むグループもあります。両方にチェックを入れると、最終的にこちらの統一先にそろいます）", ""]
        if not kakasi_available():
            L += ["> [!warning] 漢字の読みを調べるライブラリ（pykakasi）が見つからないため、"
                  "漢字とかなの比較はできませんでした。「初回セットアップ.bat」を実行してください。", ""]
        tag_lines(tag_b, False)
        L.append("")
    if not an.tag_groups:
        L += ["タグの表記ゆれは見つかりませんでした。", ""]
    if an.tag_counts:
        L += ["<details><summary>参考：使われているタグの一覧（クリックで開く）</summary>", "",
              "| タグ | ノート数 |", "| --- | ---: |"]
        for t, c in sorted(an.tag_counts.items(), key=lambda x: (-x[1], x[0])):
            L.append(f"| {code('#' + t)} | {c} |")
        L += ["", "</details>", ""]

    # ---------- 5. 目次ノート案 ----------
    L += ["---", "", f"## 5. 目次ノート案（{len(an.tocs)} 件）", "",
          f"{cfg.toc_min_notes} 件以上のノートがあるタグやフォルダについて、目次ノートの案です。",
          f"適用すると、保管庫の「{md_escape(cfg.toc_folder)}」フォルダに**新しいノートを作ります**（既存のノートは書き換えません）。",
          "最初はチェックを外してあるので、作りたい目次にだけチェックを入れてください。", ""]
    if an.tocs:
        for i, t in enumerate(an.tocs, 1):
            item_id = f"M-{i:04d}"
            kind = f"タグ {code('#' + t.key)}" if t.kind == "tag" else f"フォルダ「{md_escape(t.key)}」"
            if t.exists:
                L.append(f"- {code(item_id)} {kind}（{len(t.notes)} ノート）　→ "
                         f"{code(t.path)} はすでにあるため、作成しません")
                continue
            L.append(f"{checkbox(cfg.toc_checked)} {code(item_id)} {kind}（{len(t.notes)} ノート）"
                     f"　→ 作成するノート：{code(t.path)}")
            for rel in t.notes:
                L.append(f"    - {w.note(rel)}")
            items[item_id] = {"type": "toc", "path": t.path, "content": build_toc_content(vault, t)}
        L.append("")
    else:
        L += ["目次ノート案はありません。", ""]

    L += ["---", "", "このレポートは「Obsidian ノート整理ツール」が作成しました。", ""]

    data = {
        "format": 1,
        "created": created.isoformat(timespec="seconds"),
        "vault": str(vault.root),
        "applied": None,
        "file_hashes": {rel: vault.notes[rel].sha1 for rel in sorted(touched) if rel in vault.notes},
        "items": items,
    }
    return "\n".join(L), data


def write_report(vault: Vault, cfg: Config, an: Analysis) -> Path:
    created = datetime.now()
    md, data = build_report(vault, cfg, an, created)
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    data_dir = cfg.report_dir / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = created.strftime("%Y%m%d_%H%M%S")
    md_path = cfg.report_dir / f"{REPORT_PREFIX}{stamp}.md"
    i = 2
    while md_path.exists():
        md_path = cfg.report_dir / f"{REPORT_PREFIX}{stamp}_{i}.md"
        i += 1
    md_path.write_text(md, encoding="utf-8")
    (data_dir / (md_path.stem + ".json")).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return md_path


def data_path_for(report_path: Path) -> Path:
    return report_path.parent / DATA_DIR_NAME / (report_path.stem + ".json")


def latest_report(report_dir: Path) -> Path | None:
    if not report_dir.exists():
        return None
    reports = [p for p in report_dir.glob(f"{REPORT_PREFIX}*.md") if data_path_for(p).exists()]
    if not reports:
        return None
    return max(reports, key=lambda p: p.stem)
