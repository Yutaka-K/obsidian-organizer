@echo off
setlocal
cd /d "%~dp0"
title 初回セットアップ - Obsidian ノート整理ツール
echo ============================================================
echo   Obsidian ノート整理ツール　初回セットアップ
echo ============================================================
echo.
echo このツールを動かすための準備をします。
echo インターネットから必要なライブラリをダウンロードします。
echo.

rem ---- Python を探す ----
set "PYCMD="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto :found_python
python --version >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto :found_python
goto :no_python

:found_python
echo Python が見つかりました：
%PYCMD% --version
echo.

echo [1/3] このツール専用の作業場所「.venv」を作っています...
if exist ".venv\Scripts\python.exe" goto :venv_ok
%PYCMD% -m venv .venv
if errorlevel 1 goto :error
:venv_ok
echo       OK
echo.

echo [2/3] pip（ライブラリをインストールする道具）を更新しています...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check
echo.

echo [3/3] 必要なライブラリをインストールしています...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :error
echo.

echo ============================================================
echo   セットアップが完了しました！
echo ============================================================
echo.
echo 次にすること：
echo   1. 「設定.ini」をメモ帳で開き、保管庫フォルダの場所を確認します。
echo   2. 「レポート作成.bat」をダブルクリックします。
echo.
goto :end

:no_python
echo ------------------------------------------------------------
echo 【エラー】Python が見つかりませんでした。
echo ------------------------------------------------------------
echo.
echo 次の手順で Python をインストールしてから、もう一度このファイルを
echo ダブルクリックしてください。
echo.
echo   1. https://www.python.org/downloads/ を開く
echo   2. 「Download Python」ボタンからインストーラーをダウンロードする
echo   3. インストーラーの最初の画面で
echo      「Add python.exe to PATH」に必ずチェックを入れてから
echo      「Install Now」を押す
echo.
goto :end

:error
echo.
echo ------------------------------------------------------------
echo 【エラー】セットアップの途中で問題が起きました。
echo ------------------------------------------------------------
echo 上に表示されているメッセージを確認してください。
echo よくある原因：
echo   ・インターネットにつながっていない
echo   ・会社や学校のネットワークで、ダウンロードが制限されている
echo 解決しないときは、このフォルダの中の「.venv」フォルダを削除してから、
echo もう一度このファイルを実行してみてください。
echo.

:end
echo このウィンドウは、何かキーを押すと閉じます。
pause >nul
endlocal
