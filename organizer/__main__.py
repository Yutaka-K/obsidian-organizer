"""コマンドの入り口

  python -m organizer report   … 提案レポートを作る（ノートは変更しない）
  python -m organizer apply    … チェックを入れた提案を、バックアップ後に適用する
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

from .analyze import analyze
from .apply import ApplyError, run_apply
from .backup import BackupError
from .config import ConfigError, load_config, validate_config
from .report import latest_report, write_report
from .textutil import kakasi_available
from .vault import Vault


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def cmd_report(args) -> int:
    cfg = load_config(args.config, args.vault)
    validate_config(cfg)
    print(f"保管庫：{cfg.vault}")
    print("ノートを読み込んでいます…")
    t0 = time.time()
    vault = Vault(cfg.vault, cfg.exclude_folders).load()
    print(f"  {len(vault.notes)} 件のノートを読み込みました。")
    if not vault.notes:
        print("  ※ ノート（.md ファイル）が1つも見つかりませんでした。保管庫フォルダの設定を確認してください。")
    if not kakasi_available():
        print("  ※ 漢字の読みを調べるライブラリが見つかりません。「初回セットアップ.bat」を実行してください。")
    print("分析しています…")
    an = analyze(vault, cfg)
    path = write_report(vault, cfg, an)
    print()
    print("=" * 50)
    print(" 提案レポートができました（ノートは変更していません）")
    print("=" * 50)
    print(f"  1. 孤立ノート　　　：{len(an.orphans)} 件")
    print(f"  2. リンク候補　　　：{len(an.candidates)} 件")
    print(f"  3. リンク切れ　　　：{len(an.broken)} 件")
    print(f"  4. タグの表記ゆれ　：{len(an.tag_groups)} グループ")
    print(f"  5. 目次ノート案　　：{len(an.tocs)} 件")
    print()
    print(f"レポート：{path}")
    print(f"（かかった時間：{time.time() - t0:.1f} 秒）")
    print()
    print("次にすること：")
    print("  レポートを開いて内容を確認し、適用したい提案にチェックを入れて保存してください。")
    print("  そのあと「提案を適用.bat」を実行すると、ノートに反映されます。")
    if cfg.open_folder and not args.no_open and sys.platform == "win32":
        try:
            os.startfile(str(path.parent))  # type: ignore[attr-defined]
        except Exception:
            pass
    return 0


def cmd_apply(args) -> int:
    cfg = load_config(args.config, args.vault)
    validate_config(cfg)
    report = Path(args.report).resolve() if args.report else latest_report(cfg.report_dir)
    if report is None or not report.exists():
        print("提案レポートが見つかりません。")
        print("先に「レポート作成.bat」を実行して、レポートを作ってください。")
        return 1
    run_apply(cfg, report, assume_yes=args.yes)
    return 0


def main(argv=None) -> int:
    _setup_console()
    parser = argparse.ArgumentParser(prog="organizer", description="Obsidian ノート整理ツール")
    parser.add_argument("--config", help="設定ファイルの場所（省略すると 設定.ini）")
    parser.add_argument("--vault", help="保管庫フォルダ（設定ファイルより優先）")
    sub = parser.add_subparsers(dest="command")
    p_report = sub.add_parser("report", help="提案レポートを作る")
    p_report.add_argument("--no-open", action="store_true", help="終了後にフォルダを開かない")
    p_apply = sub.add_parser("apply", help="提案を適用する")
    p_apply.add_argument("--report", help="使うレポートのファイル（省略すると一番新しいもの）")
    p_apply.add_argument("--yes", action="store_true", help="確認せずに実行する（テスト用）")
    args = parser.parse_args(argv)

    try:
        if args.command == "report":
            return cmd_report(args)
        if args.command == "apply":
            return cmd_apply(args)
        parser.print_help()
        return 1
    except (ConfigError, ApplyError, BackupError) as e:
        print()
        print("【エラー】")
        print(e)
        return 1
    except KeyboardInterrupt:
        print()
        print("中断しました。")
        return 1
    except Exception:
        print()
        print("【予期しないエラーが起きました】")
        print("お手数ですが、下の内容をそのままコピーして、作者に伝えてください。")
        print("-" * 50)
        traceback.print_exc()
        print("-" * 50)
        return 2


if __name__ == "__main__":
    sys.exit(main())
