"""適用モード：レポートでチェックを入れた提案だけを、バックアップ後にノートへ反映する"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .analyze import NameMatcher
from .backup import backup_vault
from .config import Config
from .report import code, data_path_for, md_escape
from .vault import (INLINE_TAG_RE, Vault, file_sha1, find_frontmatter, is_valid_tag,
                    iter_wikilinks, make_mask, parse_wikilink_inner, protected_spans,
                    read_note_text, write_note_text)
from .textutil import nfc

CHECK_RE = re.compile(r"^\s*[-*+]\s+\[(.)\]\s+`([LBTM]-\d{4,})`")
TARGET_RE = re.compile(r"統一先[:：]\s*(`+)\s*#?(.+?)\s*\1")

TYPE_ORDER = {"broken_fix": 0, "tag": 1, "link": 2}
TYPE_LABEL = {"link": "リンクの追加", "broken_fix": "リンク切れの修正", "tag": "タグの統一", "toc": "目次ノートの作成"}


class ApplyError(Exception):
    pass


# ------------------------------------------------------------
#  レポートを読む
# ------------------------------------------------------------
def read_checked(report_path: Path) -> tuple[list[str], dict[str, str]]:
    """チェックの入った提案の番号と、書き換えられた統一先を返す"""
    checked: list[str] = []
    targets: dict[str, str] = {}
    text = report_path.read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        m = CHECK_RE.match(line)
        if not m:
            continue
        mark, item_id = m.group(1), m.group(2)
        if mark.lower() == "x":
            checked.append(item_id)
            if item_id.startswith("T-"):
                t = TARGET_RE.search(line)
                if t:
                    targets[item_id] = nfc(t.group(2).strip().lstrip("#"))
    return checked, targets


def load_plan(report_path: Path) -> dict:
    dp = data_path_for(report_path)
    if not dp.exists():
        raise ApplyError(f"レポートに対応するデータが見つかりません：{dp}\nもう一度「レポート作成.bat」を実行してください。")
    return json.loads(dp.read_text(encoding="utf-8"))


# ------------------------------------------------------------
#  1件ずつの書き換え
# ------------------------------------------------------------
def apply_broken_fix(text: str, item: dict) -> tuple[str, int]:
    fm_end, _ = find_frontmatter(text)
    mask = make_mask(len(text), protected_spans(text, fm_end))
    old = item["old"]
    out, last, n = [], 0, 0
    for m in iter_wikilinks(text, mask):
        target, sub, display = parse_wikilink_inner(m.group(2))
        if nfc(target) != old:
            continue
        inner = item["new"] + sub + ("|" + display if display else "")
        out.append(text[last:m.start()])
        out.append(f"{m.group(1)}[[{inner}]]")
        last = m.end()
        n += 1
    out.append(text[last:])
    return "".join(out), n


def _replace_tag_token(value: str, old_set: set[str], new: str) -> tuple[str, int]:
    """フロントマターの tags の値（1行分）の中のタグを置き換える"""
    count = 0

    def repl(m):
        nonlocal count
        if nfc(m.group(3)) in old_set and nfc(m.group(3)) != new:
            count += 1
            return f"{m.group(1)}{m.group(2)}{new}{m.group(4)}"
        return m.group(0)

    pattern = re.compile(r"(^|[\[,\s])([\"']?#?)([^\s,\[\]\"'#]+)([\"']?)(?=$|[\s,\]])")
    return pattern.sub(repl, value), count


def apply_tag(text: str, old_tags: list[str], new: str) -> tuple[str, int]:
    old_set = {nfc(t) for t in old_tags}
    lower_set = {t.lower() for t in old_set}
    total = 0

    # 1) フロントマターの tags
    fm_end, _ = find_frontmatter(text)
    if fm_end:
        fm_lines = text[:fm_end].splitlines(keepends=True)
        in_tags = False
        for i, line in enumerate(fm_lines):
            body = line.rstrip("\r\n")
            ending = line[len(body):]
            m = re.match(r"^(tags?|Tags|TAGS)(\s*:\s*)(.*)$", body)
            if m:
                in_tags = True
                if m.group(3).strip():
                    new_val, c = _replace_tag_token(m.group(3), old_set, new)
                    total += c
                    fm_lines[i] = m.group(1) + m.group(2) + new_val + ending
                    in_tags = False
                continue
            if in_tags:
                lm = re.match(r"^(\s*-\s+)(.*)$", body)
                if lm:
                    new_val, c = _replace_tag_token(lm.group(2), old_set, new)
                    total += c
                    fm_lines[i] = lm.group(1) + new_val + ending
                    continue
                if body.strip() == "":
                    continue
                in_tags = False
        text = "".join(fm_lines) + text[fm_end:]

    # 2) 本文の #タグ
    fm_end, _ = find_frontmatter(text)
    mask = make_mask(len(text), protected_spans(text, fm_end))
    out, last = [], 0
    for m in INLINE_TAG_RE.finditer(text):
        if mask.find(b"\x01", m.start(), m.end()) != -1:
            continue
        tag = m.group(1)
        stripped = tag.rstrip("/")
        if nfc(stripped) in old_set or nfc(stripped).lower() in lower_set:
            if nfc(stripped) == new:
                continue
            out.append(text[last:m.start()])
            out.append("#" + new + tag[len(stripped):])
            last = m.end()
            total += 1
    out.append(text[last:])
    text = "".join(out)
    if total:
        text = _dedupe_tag(text, new)
    return text, total


def _tag_core(token: str) -> str:
    return nfc(token.strip().strip("\"'").lstrip("#")).lower()


def _dedupe_tag(text: str, tag: str) -> str:
    """統一した結果、同じタグが2回並んでしまった場合に1つにまとめる"""
    want = nfc(tag).lower()
    fm_end, _ = find_frontmatter(text)
    head, body = text[:fm_end], text[fm_end:]

    # フロントマター
    if head:
        lines = head.splitlines(keepends=True)
        out_lines, in_tags, seen = [], False, False
        for line in lines:
            content = line.rstrip("\r\n")
            ending = line[len(content):]
            m = re.match(r"^(tags?|Tags|TAGS)(\s*:\s*)(.*)$", content)
            if m:
                in_tags, seen = True, False
                value = m.group(3)
                if value.strip():
                    in_tags = False
                    bracket = value.strip().startswith("[") and value.strip().endswith("]")
                    inner = value.strip()[1:-1] if bracket else value
                    sep = "," if "," in inner or bracket else " "
                    tokens = [t for t in (inner.split(sep)) if t.strip()]
                    kept, found = [], False
                    for tok in tokens:
                        if _tag_core(tok) == want:
                            if found:
                                continue
                            found = True
                        kept.append(tok.strip())
                    if len(kept) != len(tokens):
                        joined = (", " if sep == "," else " ").join(kept)
                        value = f"[{joined}]" if bracket else joined
                    out_lines.append(m.group(1) + m.group(2) + value + ending)
                    continue
                out_lines.append(line)
                continue
            if in_tags:
                lm = re.match(r"^\s*-\s+(.*)$", content)
                if lm:
                    if _tag_core(lm.group(1)) == want:
                        if seen:
                            continue
                        seen = True
                    out_lines.append(line)
                    continue
                if content.strip():
                    in_tags = False
            out_lines.append(line)
        head = "".join(out_lines)

    # 本文：タグだけが並んでいる行の中の重複
    def fix_line(m):
        indent, tags_part, ending = m.group(1), m.group(2), m.group(3)
        tokens = tags_part.split()
        kept, found = [], False
        for tok in tokens:
            if tok[1:].lower() == want:
                if found:
                    continue
                found = True
            kept.append(tok)
        if len(kept) == len(tokens):
            return m.group(0)
        return indent + " ".join(kept) + ending

    body = re.sub(r"(?m)^([ \t]*)((?:#[^\s#]+[ \t\u3000]*)+?)[ \t\u3000]*(\r?)$", fix_line, body)
    return head + body


def apply_link(text: str, item: dict, matcher: NameMatcher, rel: str) -> tuple[str, int]:
    fm_end, _ = find_frontmatter(text)
    mask = matcher.candidate_mask(text, protected_spans(text, fm_end))
    for s, e, target, _ in matcher.scan(text, mask, rel):
        if target == item["target"] and text[s:e] == item["matched"]:
            return text[:s] + item["new_text"] + text[e:], 1
    return text, 0


# ------------------------------------------------------------
#  全体の流れ
# ------------------------------------------------------------
def _ask_yes(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "はい", "ｙ")


def _obsidian_running() -> bool:
    import subprocess
    import sys
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe"], capture_output=True,
                             text=True, timeout=10, errors="ignore").stdout
        return "obsidian.exe" in out.lower()
    except Exception:
        return False


def _apply_items(cfg: Config, plan: dict, items: dict, selected: list[str], edited_targets: dict[str, str]):
    print("提案を適用しています…")
    vault = Vault(cfg.vault, cfg.exclude_folders).load()
    matcher = NameMatcher(vault, cfg)
    hashes: dict[str, str] = plan["file_hashes"]
    log: list[str] = []
    results = defaultdict(lambda: [0, 0])  # 種類 → [成功, スキップ]

    per_file: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    tocs: list[tuple[str, dict]] = []
    for item_id in selected:
        item = dict(items[item_id])
        if item["type"] == "toc":
            tocs.append((item_id, item))
        elif item["type"] == "tag":
            new = edited_targets.get(item_id, item["to"])
            if not is_valid_tag(new):
                log.append(f"- {code(item_id)} スキップ：統一先 {code('#' + new)} はタグとして使えない文字を含んでいます")
                results["tag"][1] += 1
                continue
            item["to"] = new
            for rel in item["files"]:
                per_file[rel].append((item_id, item))
        else:
            per_file[item["source"]].append((item_id, item))

    changed_files: list[str] = []
    tag_done: set[str] = set()
    for rel, ops in sorted(per_file.items()):
        path = cfg.vault / rel
        ops.sort(key=lambda x: (TYPE_ORDER[x[1]["type"]], x[0]))
        if not path.exists():
            for item_id, item in ops:
                log.append(f"- {code(item_id)} スキップ：{md_escape(rel)} が見つかりません")
                results[item["type"]][1] += 1
            continue
        if file_sha1(path) != hashes.get(rel):
            for item_id, item in ops:
                log.append(f"- {code(item_id)} スキップ：{md_escape(rel)} はレポート作成後に変更されています")
                results[item["type"]][1] += 1
            continue
        text, bom, _ = read_note_text(path)
        original = text
        for item_id, item in ops:
            t = item["type"]
            if t == "broken_fix":
                text, n = apply_broken_fix(text, item)
                msg = f"{code('[[' + item['old'] + ']]')} → {code('[[' + item['new'] + ']]')}（{n} か所）"
            elif t == "tag":
                text, n = apply_tag(text, item["from"], item["to"])
                msg = f"タグを {code('#' + item['to'])} に統一（{n} か所）"
            else:
                text, n = apply_link(text, item, matcher, rel)
                msg = f"{code(item['matched'])} → {code(item['new_text'])}"
            if n:
                if t == "tag":
                    tag_done.add(item_id)
                results[t][0] += 1
                log.append(f"- {code(item_id)} {md_escape(rel)}：{msg}")
            elif t == "tag":
                pass  # このノートでは書き換える必要がなかった
            else:
                results[t][1] += 1
                log.append(f"- {code(item_id)} スキップ：{md_escape(rel)} で書き換える箇所が見つかりませんでした")
        if text != original:
            write_note_text(path, text, bom)
            changed_files.append(rel)

    for item_id, item in tocs:
        path = cfg.vault / item["path"]
        if path.exists():
            results["toc"][1] += 1
            log.append(f"- {code(item_id)} スキップ：{md_escape(item['path'])} はすでにあります")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")
        results["toc"][0] += 1
        changed_files.append(item["path"])
        log.append(f"- {code(item_id)} 目次ノートを作成：{md_escape(item['path'])}")

    # タグは「グループ単位」で数え直す
    tag_ids = {i for i in selected if items[i]["type"] == "tag"}
    results["tag"][0] = len(tag_done)
    results["tag"][1] = len(tag_ids - tag_done)
    return tag_done, results, log, changed_files


def run_apply(cfg: Config, report_path: Path, assume_yes: bool = False) -> Path | None:
    plan = load_plan(report_path)
    if Path(plan["vault"]).resolve() != cfg.vault.resolve():
        raise ApplyError(
            "このレポートは別の保管庫について作られたものです。\n"
            f"  レポートの保管庫：{plan['vault']}\n  今の設定の保管庫：{cfg.vault}\n"
            "もう一度「レポート作成.bat」を実行してください。")

    checked, edited_targets = read_checked(report_path)
    items = plan["items"]
    selected = [i for i in checked if i in items]
    unknown = [i for i in checked if i not in items]

    print(f"使うレポート：{report_path.name}")
    print(f"レポートの作成日時：{plan['created'].replace('T', ' ')}")
    print()
    if plan.get("applied"):
        print(f"※ このレポートは {plan['applied'].replace('T', ' ')} にすでに一度適用されています。")
        print("   すでに反映済みの提案や、その後に編集されたノートは自動でスキップされます。")
        print()
    if not selected:
        print("チェックの入った提案がありません。何も変更せずに終了します。")
        print("（レポートで、適用したい提案にチェックを入れて保存してから、もう一度実行してください）")
        return None

    counts = defaultdict(int)
    for i in selected:
        counts[items[i]["type"]] += 1
    print("適用する提案：")
    for t in ("link", "broken_fix", "tag", "toc"):
        if counts[t]:
            print(f"  ・{TYPE_LABEL[t]}：{counts[t]} 件")
    print()
    print(f"保管庫：{cfg.vault}")
    print(f"バックアップの保存先：{cfg.backup_dir}")
    print()
    if _obsidian_running():
        print("【ご注意】Obsidian が起動しています。念のため、Obsidian を閉じてから実行することをおすすめします。")
        print()
    if not assume_yes:
        if not _ask_yes("保管庫全体のバックアップを取ってから、上の提案をノートに反映します。よろしいですか？ (y = はい / n = いいえ)："):
            print()
            print("中止しました。ノートは一切変更していません。")
            return None
    print()

    # ---- バックアップ ----
    print("バックアップを作成しています…（ノートが多いと時間がかかります）")
    zip_path = backup_vault(cfg.vault, cfg.backup_dir)
    print(f"バックアップを作成しました：{zip_path}")
    print()

    # ---- 適用 ----
    try:
        tag_done, results, log, changed_files = _apply_items(cfg, plan, items, selected, edited_targets)
    except Exception:
        print()
        print("【適用の途中でエラーが起きました】")
        print("一部のノートだけが変更された可能性があります。元に戻したいときは、")
        print(f"次のバックアップを展開して、保管庫を置き換えてください：\n  {zip_path}")
        print()
        raise

    # ---- 記録 ----
    now = datetime.now()
    plan["applied"] = now.isoformat(timespec="seconds")
    data_path_for(report_path).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    log_path = report_path.parent / f"適用結果_{now.strftime('%Y%m%d_%H%M%S')}.md"
    summary = []
    for t in ("link", "broken_fix", "tag", "toc"):
        if counts[t]:
            ok, skip = results[t]
            summary.append(f"| {TYPE_LABEL[t]} | {ok} 件 | {skip} 件 |")
    lines = [
        "# 適用結果", "",
        f"- 適用日時：{now.strftime('%Y年%m月%d日 %H:%M:%S')}",
        f"- 使ったレポート：{md_escape(report_path.name)}",
        f"- バックアップ：{code(str(zip_path))}",
        f"- 変更・作成したノート：{len(changed_files)} 件", "",
        "| 種類 | 適用 | スキップ |", "| --- | ---: | ---: |", *summary, "",
        "> [!tip] 元に戻したいとき",
        "> 上のバックアップ（ZIPファイル）を展開すると、適用する前の保管庫がそのまま入っています。", "",
        "## くわしい記録", "", *log, "",
    ]
    if unknown:
        lines += ["## 見つからなかった番号", "", *[f"- {code(i)}" for i in unknown], ""]
    log_path.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("適用が終わりました。")
    for t in ("link", "broken_fix", "tag", "toc"):
        if counts[t]:
            ok, skip = results[t]
            print(f"  ・{TYPE_LABEL[t]}：適用 {ok} 件 / スキップ {skip} 件")
    print(f"  ・変更・作成したノート：{len(changed_files)} 件")
    print()
    print(f"くわしい結果：{log_path}")
    print(f"バックアップ：{zip_path}")
    if any(results[t][1] for t in results):
        print()
        print("※ スキップした提案があります。理由は「くわしい結果」のファイルで確認できます。")
    return log_path

