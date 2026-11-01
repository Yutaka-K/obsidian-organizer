"""保管庫全体のバックアップ（ZIPファイル）"""
from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path


class BackupError(Exception):
    pass


def backup_vault(vault: Path, backup_dir: Path) -> Path:
    """保管庫の中身をすべて ZIP にまとめ、正しく読めるか確認してからパスを返す"""
    vault = Path(vault).resolve()
    backup_dir = Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = backup_dir / f"保管庫バックアップ_{vault.name}_{stamp}.zip"
    i = 2
    while zip_path.exists():
        zip_path = backup_dir / f"保管庫バックアップ_{vault.name}_{stamp}_{i}.zip"
        i += 1

    count = 0
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(vault):
                dirnames.sort()
                for fn in sorted(filenames):
                    full = Path(dirpath) / fn
                    arc = Path(vault.name) / full.relative_to(vault)
                    zf.write(full, arc.as_posix())
                    count += 1
                if not filenames and not dirnames:  # 空のフォルダも残す
                    arc = (Path(vault.name) / Path(dirpath).relative_to(vault)).as_posix() + "/"
                    zf.writestr(arc, "")
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise BackupError(f"バックアップの中のファイルが壊れています：{bad}")
            stored = sum(1 for n in zf.namelist() if not n.endswith("/"))
            if stored != count:
                raise BackupError("バックアップに入ったファイルの数が合いません。")
    except BackupError:
        raise
    except Exception as e:
        raise BackupError(f"バックアップの作成に失敗しました：{e}") from e
    return zip_path
