"""設定ファイル（設定.ini）の読み込み"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOL_DIR / "設定.ini"


class ConfigError(Exception):
    """設定に問題があるときのエラー（メッセージは日本語）"""


@dataclass
class Config:
    vault: Path
    report_dir: Path
    backup_dir: Path
    exclude_folders: list[str] = field(default_factory=list)
    link_min_length: int = 2
    link_exclude_names: list[str] = field(default_factory=list)
    link_checked: bool = True
    toc_min_notes: int = 3
    toc_folder: str = "目次"
    toc_checked: bool = False
    obsidian_uri: bool = True
    open_folder: bool = True


def _split_list(value: str) -> list[str]:
    items = []
    for part in value.replace("、", ",").replace("，", ",").split(","):
        part = part.strip()
        if part:
            items.append(part)
    return items


def _to_bool(value: str, key: str) -> bool:
    v = value.strip().lower()
    if v in ("はい", "yes", "true", "1", "on", "する"):
        return True
    if v in ("いいえ", "no", "false", "0", "off", "しない"):
        return False
    raise ConfigError(f"設定「{key}」には「はい」か「いいえ」を書いてください（今の値：{value}）")


def _to_int(value: str, key: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        raise ConfigError(f"設定「{key}」には数字を書いてください（今の値：{value}）") from None


def _resolve(path_text: str, base: Path) -> Path:
    p = Path(path_text.strip().strip('"'))
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ConfigError(f"設定ファイルの文字コードが読めません：{path}\nメモ帳で開き、「UTF-8」で保存し直してください。")


def load_config(config_path: Path | None = None, vault_override: str | None = None) -> Config:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(f"設定ファイルが見つかりません：{path}")
    base = path.resolve().parent

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(_read_text(path))
    except configparser.Error as e:
        raise ConfigError(f"設定ファイルの書き方に誤りがあります：{e}") from None

    def get(section: str, key: str, default: str) -> str:
        if parser.has_option(section, key):
            return parser.get(section, key)
        return default

    vault_text = vault_override or get("基本", "保管庫フォルダ", r"C:\Obsidian")
    vault = _resolve(vault_text, base)

    cfg = Config(
        vault=vault,
        report_dir=_resolve(get("基本", "レポートフォルダ", "レポート"), base),
        backup_dir=_resolve(get("基本", "バックアップフォルダ", "バックアップ"), base),
        exclude_folders=_split_list(get("除外", "除外フォルダ", ".obsidian, .trash")),
        link_min_length=_to_int(get("リンク候補", "最小文字数", "2"), "最小文字数"),
        link_exclude_names=_split_list(get("リンク候補", "除外する名前", "")),
        link_checked=_to_bool(get("リンク候補", "初期状態でチェック", "はい"), "初期状態でチェック"),
        toc_min_notes=_to_int(get("目次", "最小ノート数", "3"), "最小ノート数"),
        toc_folder=get("目次", "作成先フォルダ", "目次").strip().strip("/\\") or "目次",
        toc_checked=_to_bool(get("目次", "初期状態でチェック", "いいえ"), "初期状態でチェック"),
        obsidian_uri=_to_bool(get("レポート", "Obsidianで開くリンクを付ける", "はい"), "Obsidianで開くリンクを付ける"),
        open_folder=_to_bool(get("レポート", "完了後にフォルダを開く", "はい"), "完了後にフォルダを開く"),
    )
    for name in (".obsidian", ".trash", ".git"):
        if name not in cfg.exclude_folders:
            cfg.exclude_folders.append(name)
    return cfg


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_config(cfg: Config) -> None:
    if not cfg.vault.exists():
        raise ConfigError(
            f"保管庫フォルダが見つかりません：{cfg.vault}\n"
            "「設定.ini」の「保管庫フォルダ」を、あなたの Obsidian 保管庫の場所に書き換えてください。"
        )
    if not cfg.vault.is_dir():
        raise ConfigError(f"保管庫フォルダとして指定された場所がフォルダではありません：{cfg.vault}")
    if _is_inside(cfg.report_dir, cfg.vault):
        raise ConfigError(
            f"レポートフォルダが保管庫の中になっています：{cfg.report_dir}\n"
            "レポートは保管庫の外に出す決まりです。「設定.ini」の「レポートフォルダ」を変更してください。"
        )
    if _is_inside(cfg.backup_dir, cfg.vault):
        raise ConfigError(
            f"バックアップフォルダが保管庫の中になっています：{cfg.backup_dir}\n"
            "「設定.ini」の「バックアップフォルダ」を保管庫の外に変更してください。"
        )
    if _is_inside(cfg.vault, cfg.report_dir) or _is_inside(cfg.vault, cfg.backup_dir):
        raise ConfigError("保管庫フォルダが、レポートフォルダかバックアップフォルダの中にあります。設定を見直してください。")
