@echo off
REM Pythonテスト等で生成されたゴミチャンネルおよび関連データを削除・掃除するスクリプト
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python tools/cleanup_test_data.py %*
pause

