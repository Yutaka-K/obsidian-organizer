@echo off
setlocal
cd /d "%~dp0"
title レポート作成 - Obsidian ノート整理ツール
echo ============================================================
echo   提案レポートを作成します
echo   ※ ノートは一切書き換えません。安心して実行してください。
echo ============================================================
echo.
if not exist ".venv\Scripts\python.exe" goto :no_setup

".venv\Scripts\python.exe" -m organizer report
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
echo レポートを作成できませんでした。上のメッセージを確認してください。
echo ------------------------------------------------------------
echo.

:end
echo このウィンドウは、何かキーを押すと閉じます。
pause >nul
endlocal
