@echo off
setlocal
cd /d "%~dp0"
title 提案を適用 - Obsidian ノート整理ツール
echo ============================================================
echo   提案を適用します（ノートを書き換えるモード）
echo ============================================================
echo.
echo 一番新しい提案レポートで、チェックを入れた提案だけを、
echo 実際のノートに反映します。
echo.
echo   ・書き換える前に、保管庫全体のバックアップを自動で作ります。
echo   ・実行する前に、もう一度確認のメッセージが出ます。
echo   ・Obsidian は閉じておくことをおすすめします。
echo.
echo まだレポートの内容を確認していない場合は、
echo このウィンドウを右上の × で閉じてください。
echo.
if not exist ".venv\Scripts\python.exe" goto :no_setup

".venv\Scripts\python.exe" -m organizer apply
if errorlevel 1 goto :error
echo.
goto :end

:no_setup
echo 【エラー】まだセットアップが済んでいないようです。
echo 先に「初回セットアップ.bat」をダブルクリックしてください。
echo.
goto :end

:error
echo.
echo ------------------------------------------------------------
echo 適用できませんでした。上のメッセージを確認してください。
echo ------------------------------------------------------------
echo.

:end
echo このウィンドウは、何かキーを押すと閉じます。
pause >nul
endlocal
