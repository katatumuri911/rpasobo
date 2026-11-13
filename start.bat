@echo off
chcp 65001 >nul
setlocal
REM memo予算・構造化（4見出し/1,200B目標）を有効化
set "RPCHAT_MEMO_BUDGET=1"
REM 速度計測（latency / read_file 使用有無）を有効にするときだけコメントを外す
REM set "RPCHAT_PERF_LOG=1"
REM full送信時の重複アンカーを旧挙動に戻すなら 1、全オフなら 0（既定 auto）
REM set "RPCHAT_CORE_ANCHOR=1"
python --version >nul 2>&1
if errorlevel 1 (
  echo Python が見つかりません。python.org から Python 3.10 以上を入れてください。
  echo インストールの最初の画面で「Add python.exe to PATH」にチェックを入れます。
  pause
  exit /b 1
)

REM ComfyUI 出力フォルダの設定（未設定かつ C:\AI\ComfyUI\output が存在する場合は自動設定）
if not defined COMFYUI_OUTPUT_DIR (
  if exist "C:\AI\ComfyUI\output" set "COMFYUI_OUTPUT_DIR=C:\AI\ComfyUI\output"
)

REM Web Push 通知用ライブラリの確認と自動インストール
python -c "import pywebpush" >nul 2>&1
if errorlevel 1 (
  echo Web Push 通知に必要な pywebpush をインストールしています...
  python -m pip install pywebpush
  if errorlevel 1 (
    echo [警告] pywebpush のインストールに失敗しました。
  )
)

REM 長期記憶（RAG）用ライブラリの確認と自動インストール
python -c "import fastembed" >nul 2>&1
if errorlevel 1 (
  echo 長期記憶（RAG）に必要な fastembed をインストールしています...
  python -m pip install fastembed
  if errorlevel 1 (
    echo [警告] fastembed のインストールに失敗しました。
  )
)
python -c "import janome" >nul 2>&1
if errorlevel 1 (
  echo 長期記憶（RAG）に必要な janome をインストールしています...
  python -m pip install janome
  if errorlevel 1 (
    echo [警告] janome のインストールに失敗しました。
  )
)

echo このウィンドウは閉じないでください。
python "%~dp0server.py"
pause
