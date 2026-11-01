"""動作確認用のテスト（サンプル保管庫のコピーで、レポート作成と適用を試す）

実行方法：  python -m unittest discover -s tests
"""
import io
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from organizer.__main__ import main
from organizer.analyze import analyze
from organizer.apply import apply_tag, read_checked
from organizer.config import load_config
from organizer.report import latest_report
from organizer.textutil import kakasi_available
from organizer.vault import Vault

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "サンプル保管庫"


class OrganizerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = self.tmp / "保管庫"
        shutil.copytree(SAMPLE, self.vault)
        # 改行コードが CRLF（Windows 形式）のノートも用意する
        (self.vault / "改行CRLF.md").write_bytes(
            "# 改行CRLF\r\n\r\n伏線について。\r\n\r\n#サクゲキ\r\n".encode("utf-8"))
        self.config = self.tmp / "設定.ini"
        shutil.copy(ROOT / "設定.ini", self.config)
        self.cfg = load_config(self.config, str(self.vault))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--config", str(self.config), "--vault", str(self.vault), *args])
        return code, buf.getvalue()

    def snapshot(self):
        return {p.relative_to(self.vault).as_posix(): p.read_bytes()
                for p in sorted(self.vault.rglob("*")) if p.is_file()}

    # ---------- 分析 ----------
    def test_analysis(self):
        vault = Vault(self.vault, self.cfg.exclude_folders).load()
        an = analyze(vault, self.cfg)
        orphans = {o.rel for o in an.orphans}
        self.assertIn("アイデア/夢日記.md", orphans)
        self.assertNotIn("作劇法/伏線.md", orphans)          # 三幕構成・伏線回収からリンクされている
        self.assertNotIn("テンプレート/ノートのひな形.md", orphans)  # 除外フォルダ

        pairs = {(c.source, c.target) for c in an.candidates}
        self.assertIn(("作劇法/三幕構成.md", "作劇法/起承転結.md"), pairs)
        self.assertIn(("作劇法/伏線.md", "作劇法/伏線回収.md"), pairs)   # 長い名前が優先される
        self.assertNotIn(("作劇法/伏線回収.md", "作劇法/伏線.md"), pairs)  # すでにリンク済み
        reading = [c for c in an.candidates if c.source == "読書メモ.md"]
        self.assertEqual([c.target for c in reading], ["作劇法/構成/セーブ・ザ・キャット.md"])  # コード・見出し・URLは除く

        broken = {b.target: b for b in an.broken}
        self.assertEqual(set(broken), {"未来の自分への手紙", "葛藤の種類", "ミッド・ポイント"})
        self.assertEqual(broken["ミッド・ポイント"].fix, "ミッドポイント")
        self.assertNotIn("人物相関図.png", broken)  # 添付ファイルは存在する

        groups = {(g.level, g.target): {t for t, _ in g.variants} for g in an.tag_groups}
        self.assertEqual(groups[("A", "プロット")], {"プロット", "ぷろっと"})
        self.assertEqual(groups[("A", "キャラクター")], {"キャラクター", "ｷｬﾗｸﾀｰ"})
        if kakasi_available():  # 漢字の読みの比較は pykakasi があるときだけ
            self.assertIn("作劇", groups[("B", "作劇")])
            self.assertIn("さくげき", groups[("B", "作劇")])

        toc_keys = {(t.kind, t.key) for t in an.tocs}
        self.assertIn(("tag", "作劇"), toc_keys)
        self.assertIn(("folder", "作劇法"), toc_keys)

    # ---------- レポートを作ってもノートは変わらない ----------
    def test_report_does_not_modify_vault(self):
        before = self.snapshot()
        code, out = self.run_cli("report", "--no-open")
        self.assertEqual(code, 0, out)
        self.assertEqual(before, self.snapshot())
        report = latest_report(self.cfg.report_dir)
        self.assertIsNotNone(report)
        text = report.read_text(encoding="utf-8")
        for heading in ("## 1. 孤立ノート", "## 2. リンク候補", "## 3. リンク切れ", "## 4. タグの表記ゆれ", "## 5. 目次ノート案"):
            self.assertIn(heading, text)
        self.assertFalse(str(report).startswith(str(self.vault)))

    # ---------- 適用モード ----------
    @unittest.skipUnless(kakasi_available(), "pykakasi が必要です")
    def test_apply(self):
        self.run_cli("report", "--no-open")
        report = latest_report(self.cfg.report_dir)
        text = report.read_text(encoding="utf-8")
        lines = []
        for line in text.splitlines():
            if "`M-" in line and "作劇法" in line and line.startswith("- [ ]"):
                line = line.replace("- [ ]", "- [x]", 1)      # 目次ノートを1つ作る
            if "`T-" in line and "統一先：`#作劇`" in line:
                line = line.replace("- [ ]", "- [x]", 1)      # 要確認のタグも1つ適用
            if "`T-" in line and "統一先：`#人物`" in line:
                line = line.replace("- [ ]", "- [x]", 1).replace("`#人物`", "`#登場人物`")  # 統一先を変更
            lines.append(line)
        report.write_text("\n".join(lines), encoding="utf-8")

        # 「いいえ」と答えたら何も変わらない
        before = self.snapshot()
        import builtins
        orig_input = builtins.input
        builtins.input = lambda prompt="": "n"
        try:
            self.run_cli("apply")
        finally:
            builtins.input = orig_input
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.cfg.backup_dir.exists())

        code, out = self.run_cli("apply", "--yes")
        self.assertEqual(code, 0, out)

        # バックアップが取られ、元の内容が入っている
        zips = list(self.cfg.backup_dir.glob("*.zip"))
        self.assertEqual(len(zips), 1)
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
            self.assertIn("保管庫/作劇法/三幕構成.md", names)
            self.assertIn("保管庫/.obsidian/app.json", names)
            self.assertEqual(zf.read("保管庫/作劇法/三幕構成.md"), before["作劇法/三幕構成.md"])

        def read(rel):
            return (self.vault / rel).read_text(encoding="utf-8")

        self.assertIn("[[起承転結]]と比べると、[[葛藤]]の扱い", read("作劇法/三幕構成.md"))
        self.assertIn("[[伏線回収]]とセットで", read("作劇法/伏線.md"))
        self.assertIn("[[ミッドポイント]]で主人公が変わる", read("キャラクター/キャラクター造形.md"))
        self.assertIn("tags: [キャラクター, 登場人物]", read("キャラクター/キャラクター造形.md"))
        self.assertIn("#キャラクター #登場人物", read("キャラクター/敵役.md"))
        self.assertIn("#構成 #プロット", read("作劇法/構成/ミッドポイント.md"))
        self.assertIn("#キャラクター\n", read("キャラクター/主人公の欲求.md"))  # 重複したタグはまとめる
        self.assertIn("#作劇\n", read("作劇法/伏線.md"))
        self.assertNotIn("さくげき", read("作劇法/起承転結.md"))
        # コードブロックの中は変わらない
        self.assertIn("コードブロックの中の 三幕構成 は", read("作劇法/伏線.md"))
        # CRLF のノートは CRLF のまま
        crlf = (self.vault / "改行CRLF.md").read_bytes()
        self.assertIn(b"[[" + "伏線".encode() + b"]]", crlf)
        self.assertNotIn(b"\n", crlf.replace(b"\r\n", b""))
        # 目次ノート
        toc = read("目次/目次 - フォルダ - 作劇法.md")
        self.assertIn("- [[三幕構成]]", toc)
        # 除外フォルダは変わらない
        self.assertEqual((self.vault / "テンプレート/ノートのひな形.md").read_bytes(),
                         before["テンプレート/ノートのひな形.md"])

        # 同じレポートでもう一度適用しても、二重には書き換えない
        after = self.snapshot()
        code, out = self.run_cli("apply", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("すでに一度適用されています", out)
        self.assertEqual(after, self.snapshot())

    def test_skip_when_note_edited_after_report(self):
        self.run_cli("report", "--no-open")
        target = self.vault / "作劇法/三幕構成.md"
        target.write_text(target.read_text(encoding="utf-8") + "\n追記しました\n", encoding="utf-8")
        edited = target.read_bytes()
        code, out = self.run_cli("apply", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(target.read_bytes(), edited)
        self.assertIn("スキップ", out)

    def test_read_checked(self):
        p = self.tmp / "r.md"
        p.write_text("- [x] `L-0001` a\n- [ ] `L-0002` b\n- [X] `T-0003` 統一先：`#新しい` ← x\n", encoding="utf-8")
        checked, targets = read_checked(p)
        self.assertEqual(checked, ["L-0001", "T-0003"])
        self.assertEqual(targets, {"T-0003": "新しい"})

    def test_apply_tag_frontmatter_forms(self):
        t = "---\ntags:\n  - 作劇\n  - さくげき\n---\n#さくげき です\n"
        new, n = apply_tag(t, ["さくげき"], "作劇")
        self.assertEqual(new, "---\ntags:\n  - 作劇\n---\n#作劇 です\n")
        self.assertEqual(n, 2)

    def test_report_dir_inside_vault_is_rejected(self):
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace("レポートフォルダ = レポート",
                                                            f"レポートフォルダ = {self.vault / 'レポート'}"),
            encoding="utf-8")
        code, out = self.run_cli("report", "--no-open")
        self.assertEqual(code, 1)
        self.assertIn("保管庫の中", out)

    def test_missing_vault_message(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--config", str(self.config), "--vault", str(self.tmp / "ない"), "report", "--no-open"])
        self.assertEqual(code, 1)
        self.assertIn("保管庫フォルダが見つかりません", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
