"""保管庫の読み込みと、ノート本文の解析（リンク・タグ・保護すべき範囲）"""
from __future__ import annotations

import bisect
import hashlib
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .textutil import nfc

try:
    import yaml
except Exception:  # PyYAML が無い場合でも最低限動くようにする
    yaml = None


# ------------------------------------------------------------
#  正規表現
# ------------------------------------------------------------
FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$\n?", re.S | re.M)
FRONTMATTER_EMPTY_RE = re.compile(r"\A---[ \t]*\r?\n---[ \t]*\r?$\n?", re.M)
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
COMMENT_RE = re.compile(r"%%.*?%%|<!--.*?-->", re.S)
MATH_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.S)
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\r\n]+?)\]\]")
MDLINK_RE = re.compile(r"(!?)\[([^\]\r\n]*)\]\(([^)\r\n]+)\)")
URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s)>\]]+")
HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t].*$", re.M)
HTML_TAG_RE = re.compile(r"<[^>\r\n]+>")

# タグに使える文字（Obsidian のルールに合わせて、記号や日本語の句読点は含めない）
TAG_CHAR = (
    r"[^\s!-,\.:-@\[-\^`\{-~"
    r"、。，．・：；？！「」『』（）［］【】〈〉《》｛｝〔〕…‥“”‘’＃]"
)
INLINE_TAG_RE = re.compile(r"(?<!\S)#(" + TAG_CHAR + r"+)")
VALID_TAG_RE = re.compile(r"^" + TAG_CHAR + r"+$")


def is_valid_tag(tag: str) -> bool:
    return bool(VALID_TAG_RE.match(tag)) and not tag.isdigit() and not tag.replace("/", "").isdigit()


# ------------------------------------------------------------
#  データの入れ物
# ------------------------------------------------------------
@dataclass
class Link:
    raw_target: str      # リンクに書かれていた行き先（見出し部分は除く）
    line: int            # 何行目か（1から数える）
    start: int           # 本文中の位置
    end: int
    kind: str            # "wiki" か "md"
    embed: bool
    resolved: str | None = None  # 行き先のファイル（保管庫からの相対パス）


@dataclass
class Note:
    rel: str             # 保管庫からの相対パス（区切りは / ）
    path: Path
    name: str            # ファイル名（拡張子なし）
    folder: str          # 入っているフォルダ（ルートは ""）
    text: str
    bom: bool
    sha1: str
    frontmatter_end: int = 0
    frontmatter: dict = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    fm_tags: list[str] = field(default_factory=list)
    inline_tags: list[tuple[str, int]] = field(default_factory=list)  # (タグ, 行)
    links: list[Link] = field(default_factory=list)
    protected: list[tuple[int, int]] = field(default_factory=list)
    line_starts: list[int] = field(default_factory=list)

    @property
    def tags(self) -> set[str]:
        return set(self.fm_tags) | {t for t, _ in self.inline_tags}

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.line_starts, pos)


# ------------------------------------------------------------
#  ファイルの読み書き
# ------------------------------------------------------------
def read_note_text(path: Path) -> tuple[str, bool, str]:
    """(本文, BOMの有無, ハッシュ) を返す。改行コードはそのまま保つ。"""
    data = path.read_bytes()
    sha1 = hashlib.sha1(data).hexdigest()
    bom = data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    return text, bom, sha1


def write_note_text(path: Path, text: str, bom: bool) -> None:
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    tmp = path.with_name(path.name + ".tmp_organizer")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def file_sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# ------------------------------------------------------------
#  本文の解析
# ------------------------------------------------------------
def compute_line_starts(text: str) -> list[int]:
    starts = [0]
    for m in re.finditer(r"\n", text):
        starts.append(m.end())
    return starts


def find_frontmatter(text: str) -> tuple[int, str]:
    """(フロントマターの終わりの位置, フロントマターの中身) を返す"""
    m = FRONTMATTER_EMPTY_RE.match(text)
    if m:
        return m.end(), ""
    m = FRONTMATTER_RE.match(text)
    if m:
        return m.end(), m.group(1)
    return 0, ""


def protected_spans(text: str, fm_end: int) -> list[tuple[int, int]]:
    """リンクやタグを探してはいけない範囲（フロントマター・コード・コメント・数式）"""
    spans: list[tuple[int, int]] = []
    if fm_end:
        spans.append((0, fm_end))

    # コードブロック（``` や ~~~ で囲まれた部分）
    pos = 0
    in_fence = None
    fence_start = 0
    for line in text.splitlines(keepends=True):
        if pos >= fm_end:
            m = FENCE_RE.match(line)
            if in_fence is None and m:
                in_fence = m.group(1)[0] * 3
                fence_start = pos
            elif in_fence is not None and m and m.group(1).startswith(in_fence):
                spans.append((fence_start, pos + len(line)))
                in_fence = None
        pos += len(line)
    if in_fence is not None:
        spans.append((fence_start, len(text)))

    for regex in (COMMENT_RE, MATH_BLOCK_RE, INLINE_CODE_RE):
        for m in regex.finditer(text):
            spans.append((m.start(), m.end()))
    return merge_spans(spans)


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans = sorted(s for s in spans if s[1] > s[0])
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def make_mask(length: int, spans: list[tuple[int, int]]) -> bytearray:
    mask = bytearray(length)
    for s, e in spans:
        mask[s:e] = b"\x01" * (e - s)
    return mask


def in_mask(mask: bytearray, start: int, end: int) -> bool:
    return mask.find(b"\x01", start, end) != -1


def parse_wikilink_inner(inner: str) -> tuple[str, str, str]:
    """[[ ]] の中身を (行き先, 見出しなど, 表示名) に分ける"""
    display = ""
    if "|" in inner:
        inner, display = inner.split("|", 1)
        if inner.endswith("\\"):  # 表の中の [[a\|b]]
            inner = inner[:-1]
    target, sub = inner, ""
    for sep in ("#", "^"):
        if sep in target:
            idx = target.index(sep)
            target, sub = target[:idx], target[idx:] + sub
            break
    return target.strip(), sub, display


def iter_wikilinks(text: str, mask: bytearray):
    for m in WIKILINK_RE.finditer(text):
        if in_mask(mask, m.start(), m.end()):
            continue
        yield m


def _clean_md_url(url: str) -> str | None:
    url = url.strip()
    if url.startswith("<") and ">" in url:
        url = url[1:url.index(">")]
    else:
        url = re.split(r"\s+[\"']", url)[0]
    if not url or url.startswith("#") or "://" in url or url.lower().startswith(("mailto:", "obsidian:", "data:")):
        return None
    url = url.split("#")[0]
    return unquote(url).strip() or None


def _tags_from_frontmatter(fm: dict) -> list[str]:
    tags: list[str] = []
    for key in ("tags", "tag", "Tags", "TAGS"):
        value = fm.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            items = re.split(r"[,\s]+", value)
        elif isinstance(value, (list, tuple)):
            items = [str(v) for v in value if v is not None]
        else:
            items = [str(value)]
        for t in items:
            t = t.strip().lstrip("#").strip()
            if t:
                tags.append(nfc(t))
    return tags


def _aliases_from_frontmatter(fm: dict) -> list[str]:
    out: list[str] = []
    for key in ("aliases", "alias", "Aliases"):
        value = fm.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            items = [v for v in value.split(",")]
        elif isinstance(value, (list, tuple)):
            items = [str(v) for v in value if v is not None]
        else:
            items = [str(value)]
        out.extend(nfc(v.strip()) for v in items if v and v.strip())
    return out


def _load_yaml(fm_text: str) -> dict:
    if not fm_text.strip():
        return {}
    if yaml is not None:
        try:
            data = yaml.safe_load(fm_text)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    # 簡易的な読み取り（YAML として読めないとき用）
    data: dict = {}
    key = None
    for line in fm_text.splitlines():
        m = re.match(r"^([^\s:#][^:]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                data[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
            elif val:
                data[key] = val.strip("\"'")
            else:
                data[key] = []
        elif key and re.match(r"^\s*-\s+", line) and isinstance(data.get(key), list):
            data[key].append(re.sub(r"^\s*-\s+", "", line).strip().strip("\"'"))
    return data


def parse_note(note: Note) -> None:
    text = note.text
    note.line_starts = compute_line_starts(text)
    fm_end, fm_text = find_frontmatter(text)
    note.frontmatter_end = fm_end
    note.frontmatter = _load_yaml(fm_text)
    note.fm_tags = _tags_from_frontmatter(note.frontmatter)
    note.aliases = _aliases_from_frontmatter(note.frontmatter)
    note.protected = protected_spans(text, fm_end)
    mask = make_mask(len(text), note.protected)

    note.links = []
    for m in iter_wikilinks(text, mask):
        target, _, _ = parse_wikilink_inner(m.group(2))
        if not target:
            continue  # [[#見出し]] のような同じノート内へのリンク
        note.links.append(Link(nfc(target), note.line_of(m.start()), m.start(), m.end(), "wiki", bool(m.group(1))))
    for m in MDLINK_RE.finditer(text):
        if in_mask(mask, m.start(), m.end()):
            continue
        target = _clean_md_url(m.group(3))
        if not target:
            continue
        note.links.append(Link(nfc(target), note.line_of(m.start()), m.start(), m.end(), "md", bool(m.group(1))))

    note.inline_tags = []
    link_spans = merge_spans([(lk.start, lk.end) for lk in note.links] +
                             [(m.start(), m.end()) for m in URL_RE.finditer(text)])
    tag_mask = make_mask(len(text), note.protected + link_spans)
    for m in INLINE_TAG_RE.finditer(text):
        if in_mask(tag_mask, m.start(), m.end()):
            continue
        tag = nfc(m.group(1).rstrip("/"))
        if tag and is_valid_tag(tag):
            note.inline_tags.append((tag, note.line_of(m.start())))


# ------------------------------------------------------------
#  保管庫全体
# ------------------------------------------------------------
class Vault:
    def __init__(self, root: Path, exclude_folders: list[str]):
        self.root = Path(root).resolve()
        self.exclude = [nfc(e.strip().strip("/\\").replace("\\", "/")) for e in exclude_folders if e.strip()]
        self.notes: dict[str, Note] = {}          # 調べる対象のノート
        self.unreadable: list[str] = []
        self._md_by_name: dict[str, list[str]] = {}   # 存在するすべての .md（除外フォルダ含む）
        self._md_by_path: dict[str, str] = {}
        self._file_by_name: dict[str, list[str]] = {}
        self._file_by_path: dict[str, str] = {}

    # --- 読み込み ---
    def _is_excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        for ex in self.exclude:
            ex_parts = ex.split("/")
            if parts[:len(ex_parts)] == ex_parts:
                return True
        return False

    def load(self) -> "Vault":
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = Path(dirpath).relative_to(self.root).as_posix()
            rel_dir = "" if rel_dir == "." else nfc(rel_dir)
            # .obsidian や .git などのシステム用フォルダは中を見ない
            dirnames[:] = sorted(d for d in dirnames if d not in (".obsidian", ".git", ".trash"))
            for fn in sorted(filenames):
                rel = nfc(f"{rel_dir}/{fn}" if rel_dir else fn)
                lower = rel.lower()
                self._file_by_path[lower] = rel
                self._file_by_name.setdefault(posixpath.basename(lower), []).append(rel)
                if not fn.lower().endswith(".md"):
                    continue
                stem_rel = lower[:-3]
                self._md_by_path[stem_rel] = rel
                self._md_by_name.setdefault(posixpath.basename(stem_rel), []).append(rel)
                if self._is_excluded(rel):
                    continue
                path = self.root / rel
                try:
                    text, bom, sha1 = read_note_text(path)
                except (UnicodeDecodeError, OSError):
                    self.unreadable.append(rel)
                    continue
                name = nfc(fn[:-3])
                note = Note(rel=rel, path=path, name=name, folder=rel_dir, text=text, bom=bom, sha1=sha1)
                parse_note(note)
                self.notes[rel] = note
        for note in self.notes.values():
            for lk in note.links:
                lk.resolved = self.resolve(lk.raw_target, note, lk.kind)
        return self

    # --- リンクの行き先を探す ---
    def resolve(self, target: str, source: Note | None = None, kind: str = "wiki") -> str | None:
        t = nfc(target.strip().replace("\\", "/"))
        if not t:
            return None
        candidates = []
        if kind == "md" and source is not None and not t.startswith("/"):
            joined = posixpath.normpath(posixpath.join(source.folder, t)) if source.folder else posixpath.normpath(t)
            candidates.append(joined)
        candidates.append(t.lstrip("/"))
        for c in candidates:
            lower = c.lower()
            stem = lower[:-3] if lower.endswith(".md") else lower
            # 1. パスがそのまま一致
            if stem in self._md_by_path:
                return self._md_by_path[stem]
            if lower in self._file_by_path:
                return self._file_by_path[lower]
            # 2. ファイル名だけで探す
            if "/" not in stem:
                if stem in self._md_by_name:
                    return self._pick(self._md_by_name[stem], source)
                if lower in self._file_by_name:
                    return self._pick(self._file_by_name[lower], source)
            else:
                # 3. パスの後ろの部分が一致
                suffix = "/" + stem
                for key, rel in self._md_by_path.items():
                    if key.endswith(suffix):
                        return rel
                suffix = "/" + lower
                for key, rel in self._file_by_path.items():
                    if key.endswith(suffix):
                        return rel
        return None

    @staticmethod
    def _pick(rels: list[str], source: Note | None) -> str:
        if len(rels) == 1 or source is None:
            return sorted(rels, key=lambda r: (r.count("/"), r))[0]
        same = [r for r in rels if posixpath.dirname(r) == source.folder]
        return same[0] if same else sorted(rels, key=lambda r: (r.count("/"), r))[0]

    def md_names(self) -> dict[str, list[str]]:
        """ファイル名（小文字）→ 同じ名前の .md の一覧"""
        return self._md_by_name

    def is_ambiguous_name(self, name: str) -> bool:
        return len(self._md_by_name.get(name.lower(), [])) > 1

    def link_text_for(self, rel: str) -> str:
        """[[ ]] の中に書く名前。同じ名前のノートが複数あるときはパスで書く"""
        name = posixpath.basename(rel)[:-3]
        if self.is_ambiguous_name(name):
            return rel[:-3]
        return name

    def all_md_rels(self) -> list[str]:
        return sorted(self._md_by_path.values())
