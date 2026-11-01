@echo off
setlocal
cd /d "%~dp0"
title サンプルで試す - Obsidian ノート整理ツール
echo ============================================================
echo   練習用の「サンプル保管庫」で、提案レポートを作ってみます
echo   ※ あなたの本物の保管庫には、まったく触れません。
echo ============================================================
echo.
if not exist ".venv\Scripts\python.exe" goto :no_setup

".venv\Scripts\python.exe" -m organizer --vault "サンプル保管庫" report
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
echo レポートを作成できませんでした。上のメッセージを確認してください。
echo.

:end
echo このウィンドウは、何かキーを押すと閉じます。
pause >nul
endlocal
