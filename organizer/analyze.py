"""5つの分析（孤立ノート・リンク候補・リンク切れ・タグの表記ゆれ・目次ノート案）"""
from __future__ import annotations

import difflib
import posixpath
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .config import Config
from .textutil import has_kanji, kakasi_available, reading_key, surface_key
from .vault import (HEADING_RE, HTML_TAG_RE, INLINE_TAG_RE, MDLINK_RE, URL_RE, WIKILINK_RE,
                    Vault, make_mask, merge_spans)

ASCII_WORD = re.compile(r"[A-Za-z0-9]")
ONLY_SYMBOLS = re.compile(r"^[\d\s\W_]+$")


# ------------------------------------------------------------
#  結果の入れ物
# ------------------------------------------------------------
@dataclass
class Orphan:
    rel: str
    outgoing: int
    chars: int


@dataclass
class LinkCandidate:
    source: str
    target: str          # 行き先ノート（相対パス）
    matched: str         # 本文中で見つかった文字
    via_alias: bool
    line: int
    context: tuple[str, str, str]  # (前, 一致した文字, 後)
    count: int           # そのノートの中で見つかった回数


@dataclass
class BrokenLink:
    source: str
    target: str
    line: int
    kind: str
    embed: bool
    fix: str | None = None              # 確実に直せるときの正しいノート名
    similar: list[str] = field(default_factory=list)


@dataclass
class TagGroup:
    level: str                    # "A"（表記の違い）または "B"（読み・似ている：要確認）
    target: str
    variants: list[tuple[str, int]]  # (タグ, 使っているノート数)
    files: list[str]


@dataclass
class TocProposal:
    kind: str           # "tag" または "folder"
    key: str
    path: str           # 作成するノートのパス
    notes: list[str]
    exists: bool


@dataclass
class Analysis:
    orphans: list[Orphan]
    candidates: list[LinkCandidate]
    broken: list[BrokenLink]
    tag_groups: list[TagGroup]
    tocs: list[TocProposal]
    tag_counts: Counter


# ------------------------------------------------------------
#  1. 孤立ノート
# ------------------------------------------------------------
def find_orphans(vault: Vault, cfg: Config) -> list[Orphan]:
    incoming: Counter = Counter()
    for note in vault.notes.values():
        for lk in note.links:
            if lk.resolved and lk.resolved != note.rel:
                incoming[lk.resolved] += 1
    toc_prefix = cfg.toc_folder.replace("\\", "/").rstrip("/") + "/"
    out = []
    for rel, note in sorted(vault.notes.items()):
        if incoming[rel] or rel.startswith(toc_prefix):
            continue
        outgoing = len({lk.resolved for lk in note.links if lk.resolved and lk.resolved != rel})
        out.append(Orphan(rel, outgoing, len(note.text[note.frontmatter_end:].strip())))
    return out


# ------------------------------------------------------------
#  2. リンク候補
# ------------------------------------------------------------
def _lower_same_length(text: str) -> str:
    out = []
    for ch in text:
        lo = ch.lower()
        out.append(lo if len(lo) == 1 else ch)
    return "".join(out)


class NameMatcher:
    """ノート名（と別名）が本文のどこに出てくるかを探す道具"""

    def __init__(self, vault: Vault, cfg: Config):
        self.vault = vault
        exclude = {n.lower() for n in cfg.link_exclude_names}
        entries: dict[str, tuple[str, bool]] = {}  # 小文字の名前 → (ノート, 別名か)
        alias_entries: dict[str, tuple[str, bool]] = {}

        def usable(name: str) -> bool:
            return (len(name) >= cfg.link_min_length and name.lower() not in exclude
                    and not ONLY_SYMBOLS.match(name))

        for lower, rels in vault.md_names().items():
            notes = [r for r in rels if r in vault.notes]
            if len(rels) != 1 or not notes:
                continue  # 同じ名前のノートが複数ある、または調べる対象外
            name = vault.notes[notes[0]].name
            if usable(name):
                entries[name.lower()] = (notes[0], False)
        for rel, note in vault.notes.items():
            for alias in note.aliases:
                if usable(alias) and alias.lower() not in entries:
                    if alias.lower() in alias_entries and alias_entries[alias.lower()][0] != rel:
                        alias_entries[alias.lower()] = ("", True)  # 複数のノートが同じ別名を持つ
                    else:
                        alias_entries[alias.lower()] = (rel, True)
        for k, v in alias_entries.items():
            if v[0]:
                entries[k] = v
        self.entries = entries
        self.by_first: dict[str, list[str]] = defaultdict(list)
        for name in entries:
            self.by_first[name[0]].append(name)
        for lst in self.by_first.values():
            lst.sort(key=len, reverse=True)

    @staticmethod
    def candidate_mask(note_text: str, protected: list[tuple[int, int]]) -> bytearray:
        spans = list(protected)
        for regex in (WIKILINK_RE, MDLINK_RE, URL_RE, HEADING_RE, HTML_TAG_RE):
            spans.extend((m.start(), m.end()) for m in regex.finditer(note_text))
        spans.extend((m.start(), m.end()) for m in INLINE_TAG_RE.finditer(note_text))
        return make_mask(len(note_text), merge_spans(spans))

    def scan(self, text: str, mask: bytearray, self_rel: str):
        """(開始, 終了, 行き先ノート, 別名か) を、前から順に返す（長い名前を優先）"""
        lowered = _lower_same_length(text)
        n = len(text)
        i = 0
        by_first = self.by_first
        while i < n:
            names = by_first.get(lowered[i])
            if names and not mask[i]:
                hit = None
                for name in names:
                    L = len(name)
                    if not lowered.startswith(name, i):
                        continue
                    if mask.find(b"\x01", i, i + L) != -1:
                        continue
                    if ASCII_WORD.match(name[0]) and i > 0 and ASCII_WORD.match(text[i - 1]):
                        continue
                    if ASCII_WORD.match(name[-1]) and i + L < n and ASCII_WORD.match(text[i + L]):
                        continue
                    hit = name
                    break
                if hit:
                    rel, via_alias = self.entries[hit]
                    L = len(hit)
                    if rel != self_rel:
                        yield i, i + L, rel, via_alias
                    i += L
                    continue
            i += 1


def _context(text: str, start: int, end: int, width: int = 18) -> tuple[str, str, str]:
    before = text[max(0, start - width):start]
    after = text[end:end + width]
    before = before.split("\n")[-1]
    after = after.split("\n")[0]
    return before.strip("\r"), text[start:end], after.strip("\r")


def find_link_candidates(vault: Vault, cfg: Config, matcher: NameMatcher) -> list[LinkCandidate]:
    out: list[LinkCandidate] = []
    for rel, note in sorted(vault.notes.items()):
        already = {lk.resolved for lk in note.links if lk.resolved}
        mask = matcher.candidate_mask(note.text, note.protected)
        found: dict[str, LinkCandidate] = {}
        for s, e, target, via_alias in matcher.scan(note.text, mask, rel):
            if target in already:
                continue
            if target in found:
                found[target].count += 1
                continue
            found[target] = LinkCandidate(rel, target, note.text[s:e], via_alias, note.line_of(s),
                                          _context(note.text, s, e), 1)
        out.extend(sorted(found.values(), key=lambda c: c.line))
    return out


# ------------------------------------------------------------
#  3. リンク切れ
# ------------------------------------------------------------
def find_broken_links(vault: Vault) -> list[BrokenLink]:
    by_key: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for rel in vault.all_md_rels():
        name = posixpath.basename(rel)[:-3]
        by_key[surface_key(name)].add(name)
        names[name] = rel
    name_list = sorted(names)

    out: list[BrokenLink] = []
    for rel, note in sorted(vault.notes.items()):
        for lk in note.links:
            if lk.resolved:
                continue
            bl = BrokenLink(rel, lk.raw_target, lk.line, lk.kind, lk.embed)
            base = posixpath.basename(lk.raw_target)
            looks_like_note = not re.search(r"\.[A-Za-z0-9]{1,5}$", base) or base.lower().endswith(".md")
            if looks_like_note:
                base = base[:-3] if base.lower().endswith(".md") else base
                matches = by_key.get(surface_key(base), set())
                if len(matches) == 1 and lk.kind == "wiki":
                    bl.fix = next(iter(matches))
                bl.similar = [n for n in difflib.get_close_matches(base, name_list, n=3, cutoff=0.6)
                              if n != bl.fix]
            out.append(bl)
    return out


# ------------------------------------------------------------
#  4. タグの表記ゆれ
# ------------------------------------------------------------
class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _choose_target(tags: list[str], counts: Counter) -> str:
    # 使われている数が多いもの → 漢字を含むもの → 半角カナや全角英数字でないもの
    # → カタカナを含むもの → 短いもの の順で選ぶ
    def key(t: str):
        stable = unicodedata.normalize("NFKC", t) == t
        katakana = any("\u30a1" <= ch <= "\u30fa" for ch in t)
        return (-counts[t], not has_kanji(t), not stable, not katakana, len(t), t)
    return sorted(tags, key=key)[0]


def find_tag_variants(vault: Vault) -> tuple[list[TagGroup], Counter]:
    counts: Counter = Counter()
    files_of: dict[str, set[str]] = defaultdict(set)
    for rel, note in vault.notes.items():
        for t in note.tags:
            counts[t] += 1
            files_of[t].add(rel)

    # レベルA：全角半角・大文字小文字・記号・ひらがなカタカナ の違いだけ
    groups_a: dict[str, list[str]] = defaultdict(list)
    for t in counts:
        groups_a[surface_key(t)].append(t)

    result: list[TagGroup] = []
    rep_of_key: dict[str, str] = {}
    for key, tags in groups_a.items():
        target = _choose_target(tags, counts)
        rep_of_key[key] = target
        if len(tags) > 1:
            files = sorted(set().union(*(files_of[t] for t in tags)))
            variants = sorted(((t, counts[t]) for t in tags), key=lambda x: (-x[1], x[0]))
            result.append(TagGroup("A", target, variants, files))

    # レベルB：読みが同じ（漢字とかな）・長音の有無・つづりがよく似ている
    uf = _UnionFind()
    keys = sorted(groups_a)
    by_reading: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        uf.find(k)
        by_reading[reading_key(k)].append(k)
    for ks in by_reading.values():
        for k in ks[1:]:
            uf.union(ks[0], k)
    for i, a in enumerate(keys):
        if len(a) < 4:
            continue
        for b in keys[i + 1:]:
            if len(b) < 4 or abs(len(a) - len(b)) > 2:
                continue
            sm = difflib.SequenceMatcher(None, a, b)
            if sm.real_quick_ratio() >= 0.85 and sm.quick_ratio() >= 0.85 and sm.ratio() >= 0.85:
                uf.union(a, b)
    clusters: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        clusters[uf.find(k)].append(k)
    for ks in clusters.values():
        if len(ks) < 2:
            continue
        tags = [t for k in ks for t in groups_a[k]]
        reps = [rep_of_key[k] for k in ks]
        rep_counts = Counter({rep_of_key[k]: sum(counts[t] for t in groups_a[k]) for k in ks})
        target = _choose_target(reps, rep_counts)
        files = sorted(set().union(*(files_of[t] for t in tags)))
        variants = sorted(((t, counts[t]) for t in tags), key=lambda x: (-x[1], x[0]))
        result.append(TagGroup("B", target, variants, files))

    result.sort(key=lambda g: (g.level, g.target))
    return result, counts


# ------------------------------------------------------------
#  5. 目次ノート案
# ------------------------------------------------------------
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _safe_name(s: str) -> str:
    return _BAD_FILENAME.sub("-", s).strip(" .") or "名前なし"


def find_toc_proposals(vault: Vault, cfg: Config, tag_groups: list[TagGroup]) -> list[TocProposal]:
    toc_folder = cfg.toc_folder.replace("\\", "/").strip("/")
    toc_prefix = toc_folder + "/"
    variant_tags = {t for g in tag_groups if g.level == "A" for t, _ in g.variants if t != g.target}

    notes_by_tag: dict[str, list[str]] = defaultdict(list)
    notes_by_folder: dict[str, list[str]] = defaultdict(list)
    for rel, note in vault.notes.items():
        if rel.startswith(toc_prefix):
            continue
        for t in note.tags:
            notes_by_tag[t].append(rel)
        if note.folder:
            notes_by_folder[note.folder].append(rel)

    out: list[TocProposal] = []
    existing = {r.lower() for r in vault.all_md_rels()}
    for tag, rels in sorted(notes_by_tag.items()):
        if len(rels) < cfg.toc_min_notes or tag in variant_tags:
            continue
        path = f"{toc_folder}/目次 - タグ - {_safe_name(tag.replace('/', '-'))}.md"
        out.append(TocProposal("tag", tag, path, sorted(rels, key=_sort_key), path.lower() in existing))
    for folder, rels in sorted(notes_by_folder.items()):
        if len(rels) < cfg.toc_min_notes:
            continue
        path = f"{toc_folder}/目次 - フォルダ - {_safe_name(folder.replace('/', '-'))}.md"
        out.append(TocProposal("folder", folder, path, sorted(rels, key=_sort_key), path.lower() in existing))
    return out


def _sort_key(rel: str):
    return (posixpath.basename(rel).lower(), rel)


def build_toc_content(vault: Vault, prop: TocProposal) -> str:
    if prop.kind == "tag":
        title = f"目次：#{prop.key}"
        lead = f"タグ `#{prop.key}` が付いているノートの一覧です（{len(prop.notes)}件）。"
    else:
        title = f"目次：{prop.key} フォルダ"
        lead = f"フォルダ「{prop.key}」に入っているノートの一覧です（{len(prop.notes)}件）。"
    lines = [f"# {title}", "", lead, "",
             "> このノートは「Obsidian ノート整理ツール」が作成しました。自由に書き換えてかまいません。", ""]
    if prop.kind == "tag":
        by_folder: dict[str, list[str]] = defaultdict(list)
        for rel in prop.notes:
            by_folder[posixpath.dirname(rel)].append(rel)
        for folder in sorted(by_folder):
            if len(by_folder) > 1:
                lines.append(f"## {folder or '（保管庫の一番上）'}")
                lines.append("")
            for rel in by_folder[folder]:
                lines.append(f"- [[{vault.link_text_for(rel)}]]")
            lines.append("")
    else:
        for rel in prop.notes:
            lines.append(f"- [[{vault.link_text_for(rel)}]]")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------
#  まとめて実行
# ------------------------------------------------------------
def analyze(vault: Vault, cfg: Config) -> Analysis:
    matcher = NameMatcher(vault, cfg)
    tag_groups, tag_counts = find_tag_variants(vault)
    return Analysis(
        orphans=find_orphans(vault, cfg),
        candidates=find_link_candidates(vault, cfg, matcher),
        broken=find_broken_links(vault),
        tag_groups=tag_groups,
        tocs=find_toc_proposals(vault, cfg, tag_groups),
        tag_counts=tag_counts,
    )


__all__ = ["analyze", "Analysis", "NameMatcher", "build_toc_content", "kakasi_available"]
