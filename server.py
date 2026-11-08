"""
RP Image Chat - server (multi-session)
  - public/ を配信
  - data/sessions/<sid>.json を各チャンネルの状態として管理
  - wait_input.py が入力を受け取ったら、設定・会話・発言をそのまま表示する
    （担当IDEが前回と同じなら、増えた発言と変わった設定だけ。担当が変わったら全部）
  - outbox/*.json（AIの応答）を監視して該当セッションに取り込み
  - 応答に付いた画像プロンプトを ComfyUI に投げ、完成画像をセッションに反映
標準ライブラリのみ。  python server.py
"""
import json, os, threading, time, uuid, random, shutil, urllib.request, mimetypes, sys, copy, socket, sqlite3, datetime, hashlib, re, base64, binascii, collections
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import qr  # スマホで開くアドレスの QR コード
try:
    import ide_poke  # 担当の Antigravity 会話に合図を送る（Windows のみ）
except (ImportError, OSError, AttributeError):
    ide_poke = None
try:
    import pywebpush
    from py_vapid import Vapid
except (ImportError, OSError, AttributeError):
    pywebpush = None
    Vapid = None

import memory_store
memory_regen_state = {}  # sid -> {"running": bool, "current": int, "total": int, "status": str}

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
SESS_DIR = os.path.join(DATA, "sessions")
RAG_DIR = os.path.join(DATA, "rag")
TRASH = os.path.join(DATA, "trash")
APP_SETTINGS = os.path.join(DATA, "settings.json")
VAPID_FILE = os.path.join(DATA, "vapid_keys.json")
PUSH_SUBS = os.path.join(DATA, "push_subscriptions.json")
PRESETS = os.path.join(DATA, "file-presets.json")  # 「読み込むファイル」の選択のプリセット
CHAR_PRESETS = os.path.join(DATA, "char-presets.json")  # キャラ固定タグのプリセット（全チャンネル共通）
WRITING_STYLE = os.path.join(DATA, "writing_style.md")
LEGACY = os.path.join(DATA, "session.json")
SENT = os.path.join(DATA, "sent")  # チャンネルごとに、担当IDEに何を送ったかの記録（同じ担当なら差分だけ送るため）
UPLOADS = os.path.join(DATA, "uploads")  # 発言に添付したファイル（uploads/<sid>/<時刻>_<名前>）
UPLOAD_MAX = 40 * 1024 * 1024  # 1ファイルの上限（base64 で送るので BODY_MAX に収まるように）
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}  # これ以外はページに埋め込まずダウンロードさせる
OUTBOX = os.path.join(ROOT, "outbox")
DONE = os.path.join(OUTBOX, "done")
COMFY = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_OUT = os.path.abspath(os.path.normpath(os.environ.get(
    "COMFYUI_OUTPUT_DIR", os.path.join(ROOT, "..", "ComfyUI", "output"))))
PORT = int(os.environ.get("RPCHAT_PORT", "8200"))
RUNNING = os.path.join(DATA, "server.json")  # 起動中のサーバーの番号（同じ data/ に2台目を立てないため）
HOST = os.environ.get("RPCHAT_HOST", "127.0.0.1")
# Antigravity IDE の会話一覧（読み取り専用で開く。IDE側には何も書き込まない）
IDE_DB = os.environ.get("ANTIGRAVITY_SUMMARIES_DB", os.path.join(
    os.path.expanduser("~"), ".gemini", "antigravity", "conversation_summaries.db"))
ide_waiting = {}  # IDE会話ID -> wait_input.py が最後に問い合わせてきた時刻
POKE_DELAY = 0  # 即時合図を送る（0秒）
poke_event = threading.Event()  # 入力発生時に poke_worker を即時起床させるイベント
ide_auto_event = threading.Event()  # 会話の自動作成ワーカーを即時起床させるイベント
image_event = threading.Event()  # 画像生成キュー追加時に image_worker を即時起床させるイベント
session_revs = {}  # sid -> int(rev) インメモリキャッシュ
session_cache = {}  # sid -> {"mtime": float, "size": int, "choices_mtime": float, "states_mtime": float, "images_mtime": float, "s": dict}
choices_cache = {}  # sid -> {"mtime": float, "size": int, "data": dict}
states_cache = {}  # sid -> {"mtime": float, "size": int, "data": dict}
images_cache = {}  # sid -> {"mtime": float, "size": int, "data": dict}
sent_cache = {}  # sid -> {"mtime": float, "size": int, "data": dict}
session_ide_cache = {}  # sid -> ide_session
msg_to_session = {}  # mid -> sid 高速逆引きキャッシュ
queued_images = collections.deque()  # (sid, mid) 画像生成キュー
queued_memories = collections.deque()  # 非同期メモリ抽出・保存キュー
memory_event = threading.Event()  # 非同期メモリ保存ワーカー起床イベント
summary_cache = {}  # sid -> (rev, summary_dict) チャンネル一覧キャッシュ
CHOICES_DIR = os.path.join(DATA, "choices")  # 選択肢（choices）の分離保存先
STATES_DIR = os.path.join(DATA, "states")  # キャラクターステータス（state）の分離保存先
IMAGES_DIR = os.path.join(DATA, "images")  # 画像詳細メタデータ（image）の分離保存先
ROTATION_TURNS = 30  # 世代交代を起こすセッション内ユーザーターン数（初回）。以降は KEEP 分を残すため通算20ターンごと
ROTATION_KEEP_TURNS = 10  # ローテーション後に残す最新ターン数
# ROTATION_PERIOD は「旧セッションの total_turns 補填」専用に残す（恒常的な再計算には使わない）
ROTATION_PERIOD = ROTATION_TURNS - ROTATION_KEEP_TURNS  # 2回目以降の実質周期

ROT_TICK = 0.3                 # worker の刻み（秒）
ROT_SETTLE_SEC = 15            # 旧会話が落ち着くのを待つ上限
ROT_SPAWN_TRIES = 3            # 新規会話の作成試行回数
ROT_BOOTSTRAP_TRIES = 6        # ブートストラップ送信の試行回数
ROT_BOOTSTRAP_WAIT = 5         # 予備実行用の wait_input.py --wait（30 だと無駄に30秒待つ）
ROT_VERIFY_TEXT = "準備完了"
ROT_VERIFY_ROUNDS = 2          # 疎通テストの総ラウンド数（初回を含む。2 = 初回+やり直し1回）
ROT_VERIFY_POLL = 2.0          # 検証ポーリング間隔
ROT_VERIFY_TIMEOUT = 120       # 1ラウンドの上限
ROT_WATCHDOG_SEC = 600         # cutover 待ちの上限（超えたら中止）
ROT_SKIP_TURNS = 3             # 中止後、再武装しないターン数

ROTATION_ENABLED = os.environ.get("RPCHAT_ROTATION", "1") != "0"  # 切り戻し用（武装を止める）

rotation_event = threading.Event()  # worker 起床（phase 書き込みの後に set する）
rotation_queue = {}                 # sid -> next_at(monotonic)
rotation_guard = threading.Lock()   # rotation_queue の保護（lock とは別）
ARCHIVE_DIR = os.path.join(DATA, "archive")  # スナップショット退避先
MEMO_REMIND_TURNS = 10  # 前回の memo 更新から数えた AI 応答ターン数がこれに達したら memo 全文出力を促す
POKE_AGAIN = 30  # スペースの合図のあと、これだけたっても受け取られなければコマンドを送り直す
POKE_TEXT = os.environ.get("RPCHAT_POKE_TEXT", " ")  # 合図として会話に送る文字
# rpc: 会話IDを指定して Antigravity の言語サーバーに直接送る / window: IDE のウィンドウにキーを打つ（予備）
POKE_MODE = os.environ.get("RPCHAT_POKE_MODE", "rpc")
# チャンネルの作業フォルダ（Antigravity の会話を作る場所・AGENTS.md を置く場所）。
# 全チャンネルの既定はこのフォルダそのもの（チャンネルの設定で、この下の名前か絶対パスにも変えられる）
NEW_IDE_DIR = os.path.abspath(os.environ.get("RPCHAT_NEW_IDE_DIR", os.path.join(ROOT, "session")))
poke_log = {}  # IDE会話ID -> 最後の合図の結果 {"ts", "ok", "msg"}
claimed_at = {}  # 発言ID -> wait_input.py が受け取った時刻
# 発言を書き換えたチャンネルID -> {"ide", "msgs": 書き換える前の発言, "keep": 残す最後の発言の番号, "t"}。
# 画面が続けて /api/ide/revert を呼んで担当の会話を巻き戻す。それまで（最長 REVERT_HOLD 秒）はその会話に合図しない
revert_plan = {}
reverting = set()  # 巻き戻している最中の会話ID
REVERT_HOLD = 60
REVERT_BACKUP = os.path.join(DATA, "trash", "ide_revert")  # 巻き戻す前の会話データ（conversations/<ID>.db）の写し
poke_lock = threading.Lock()  # 合図は同時に1つだけ（window のときは前面のウィンドウを入れ替えるので）
# wait_input.py --ide で一度でも問い合わせてきた会話。まだの会話にはスペースではなく実行するコマンドを送る
IDE_KNOWN = os.path.join(DATA, "ide_known.json")
HISTORY_CHARS = 30000  # 会話を全部送るときの上限（新しい方から。それより前はAIメモで補う）
DEFAULT_APP = {
    # 自分の場所は画面のチャンネル設定の「設定ファイル」→「変更…」で変える（data/settings.json に保存される。全チャンネル共通）
    "system_root": os.path.join(ROOT, "systemfiles"),
    "file_folders": [],  # 「読み込むファイル」を選べるフォルダ（いくつでも。この中のファイルだけ読む）
    "new_channel_ide": True,  # 新しいチャンネルを作ったら Antigravity の新規会話も作って担当にする
}
FOLDER_EXT = (".md", ".txt")
FOLDER_MAX = 3000  # 1つのフォルダで一覧に出すファイルの上限（大きなフォルダを登録しても固まらないように）


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        # デフォルトルートがない場合（オフラインWi-Fiルーター等）のフォールバック
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and not ip.startswith("169.254.") and ip != "0.0.0.0":
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def bind_host():
    return lan_ip() if HOST == "lan" else HOST


# スマホで開くアドレスとその QR コード（LAN で待ち受けたときだけ。アプリ全体の設定に出す）
phone = {"url": None, "qr": None}


def setup_phone(host):
    if host in LOOPBACK:
        return
    if host in ("0.0.0.0", "::"):
        host = lan_ip()
    phone["url"] = f"http://{'[' + host + ']' if ':' in host else host}:{PORT}/"
    try:
        phone["qr"] = qr.svg(phone["url"])
    except ValueError:
        pass


# ---------------------------------------------------------------- フォルダを選ぶウィンドウ
pick_lock = threading.Lock()  # 一度に1つだけ出す


def from_this_pc(ip):
    """要求がこの PC から来たか（スマホから押すと、選ぶウィンドウが PC の画面に出てしまう）"""
    if ip in LOOPBACK or ip.startswith("127."):
        return True
    try:
        own = set(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        own = set()
    return ip in own or ip == lan_ip()


def pick_folder(initial=""):
    """PC の画面に「フォルダーの選択」ウィンドウを出して、選んだ場所を返す（やめたら ""）"""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # ブラウザの後ろに隠れないように
    try:
        opt = {"initialdir": initial} if initial and os.path.isdir(initial) else {}
        path = filedialog.askdirectory(parent=root, title="フォルダを選ぶ", mustexist=True, **opt)
    finally:
        root.destroy()
    return os.path.normpath(path) if path else ""


# ---------------------------------------------------------------- access
# ほかのサイトがブラウザ経由でこのサーバーを操作しないように、Host と Origin を確かめる。
# LAN モードは同じネットワークの機器なら誰でも開けるので、信頼できるネットワークでだけ使う
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
BODY_MAX = 64 * 1024 * 1024  # 書き出したチャンネルの読み込みも通るように大きめ
access = {"hosts": set(LOOPBACK)}


def is_private_ip(ip):
    try:
        parts = [int(p) for p in ip.split(".")]
        if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
            if parts[0] == 10:
                return True
            if parts[0] == 172 and 16 <= parts[1] <= 31:
                return True
            if parts[0] == 192 and parts[1] == 168:
                return True
    except (ValueError, AttributeError):
        pass
    return False


def setup_access(host):
    """受け付ける Host 名（この PC の名前とアドレス、待ち受けるアドレス、RPCHAT_ALLOWED_HOSTS）を決める"""
    hname = socket.gethostname().lower()
    names = {hname, f"{hname}.local"}
    try:
        names.update(a.lower() for a in socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        lip = lan_ip()
        if lip:
            names.add(lip.lower())
    except Exception:
        pass
    extra = {x.strip().lower() for x in os.environ.get("RPCHAT_ALLOWED_HOSTS", "").split(",") if x.strip()}
    access["hosts"] = set(LOOPBACK) | names | {host} | extra


def host_name(v):
    """Host ヘッダー（"name:port" や "[::1]:port"）から名前だけを取り出す"""
    v = (v or "").strip().lower()
    if v.startswith("["):
        return v[1:v.find("]")] if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


lock = threading.RLock()

DEFAULT_SETTING = {
    "title": "新しいチャンネル",
    "summary": "",        # 概要：このチャンネルがどんな話か（ユーザーが書く）
    "instructions": "",   # 指示：AIへの書き方・方針の指示（ユーザーが書く）
    "memo": "",           # AIメモ：進行状況・あらすじ（AIが応答ごとに更新、手で直してもOK）
    "initial_memo": "",
    "elapsed_time": {
        "day": 1,
        "period": "午前"
    },
    "initial_elapsed_time": {
        "day": 1,
        "period": "午前"
    },
    "turns_in_period": 0,
    "period_turns_limit": 10,
    "total_turns": 0,
    "rotation_count": 0,
    "last_rotation_turn": 0,
    "turns_since_rotation": 0,
    "user_name": "あなた",
    "character": {"name": "", "age": "", "profile": "", "speech": ""},
    "scenario": "",
    "first_message": "",
    "rag_collections": [],
    "shared_memories": False,  # キャラクター共通記憶：同名キャラの他セッションの記憶も横断参照
    "project": "",        # サイドバーでの分類
    "system_file": "",    # "folder/file"（System として読む）
    "ref_files": [],      # ["folder/file", ...]（参照ファイルとして読む）
    "folder_files": [],   # 登録フォルダから選んだファイルの実パス（選んだ順に読む）
    "files_extra": "",    # 追加設定：選んだファイルの後に読ませる文
    "image_mode": "always",  # always / auto / off
    # 画像のキャラ固定タグ。AI がキャラの登場時に作り、画像に写るキャラの分だけ前に付ける
    # [{"id", "name", "tags", "locked"(AIに上書きさせない), "source": "ai"|"user"|"preset"}]
    "chars": [],
    "ide_session": "",    # 担当する Antigravity IDE の会話ID（空なら従来どおり誰でも受け取れる）
    "ide_title": "",
    "ide_auto": False,    # 初めての起動で作ったチャンネル1。Antigravity の新規会話をまだ付けていない（付けるまで ide_auto_worker が試す）
    "ide_model": "",     # 合図を送るときのモデル（Antigravity のモデルのキー。空なら会話の最後の入力と同じ）
    "choices_ide_session": "",   # 新規: 選択肢担当の Antigravity 会話ID（空ならメインが順次担当）
    "choices_ide_title": "",     # 新規: 選択肢担当の会話タイトル
    "choices_ide_auto": False,   # 新規: 選択肢担当の自動作成フラグ
    "choices_ide_model": "",     # 新規: 選択肢担当の合図モデル（空なら ide_model と同じ）
    "choices_ide_manual_off": False,  # ユーザーが手動で選択肢担当を「外す」にした場合は自動作成しない
    "ide_revert": True,   # 発言を編集・削除・作り直し・巻き戻ししたら、担当の会話もその前まで巻き戻す
    # 入力の渡し方。diff: 全部のときは履歴を30000字まで・ファイルの設定も中身を出す、続きは増えた分と変わった分
    # light: 全部のときは直近15件を全文（前はAIメモ）・ファイルの設定と長い会話は場所を出して読ませる、続きは今回の発言（と変わった設定）だけ
    "input_mode": "diff",
    "ide_folder": "",     # 作業フォルダ。NEW_IDE_DIR からの名前か絶対パス（空なら NEW_IDE_DIR そのもの）
    "agents_md": False,   # 作業フォルダに AGENTS.md（System ファイルと追加のルール）を書き出す
    "agents_extra": "",   # AGENTS.md に足すルール（画面から書く）
    "rules_every": False, # agents_extra を入力のたびに wait_input.py の表示の最後に付ける（AGENTS.md と違ってチャンネルごとに効く）
    "direct_send": False, # 合図にコンテキストを直接載せる（rpcのみ・高速）
    "rp_state": True,    # キャラの状態（箇条書き）と内心を、AI に応答の state で毎回書かせ、次の入力で毎回見せる
    "suggest_choices": True,  # 返答候補（8択）生成フラグ（デフォルトON）
    "image": {
        "checkpoint": "waiIllustriousSDXL_v170.safetensors",
        "width": 768, "height": 1344, "steps": 8, "cfg": 4,
        "sampler": "euler", "scheduler": "normal",
        "clip_skip": 2,  # アニメ系SDXL推奨（CLIPの最後N層をスキップ）
        "speed_lora": "Hyper-SDXL-8steps-lora.safetensors",  # 高速化LoRA（空なら無効）
        "speed_lora_strength": 0.8,  # 高速化LoRAの強度
        "base_positive": "masterpiece, best quality, ultra-detailed, anime style, glossy skin, motion lines, large breasts, cleavage, ",
        # character_tags は廃止（キャラの固定外見は chars に完全一元化）
        "base_negative": "worst quality, low quality, bad anatomy, bad proportions, missing limbs, extra limbs, bad hands, mutated hands, missing fingers, text, watermark, signature, multiple girls,",
        "ipadapter_image": "",
    },
}
# 別のチャンネルとして始めるときに持ち越さない、担当IDEの割り当て
NO_IDE = {
    "ide_session": "", "ide_title": "", "ide_model": "", "ide_auto": False,
    "choices_ide_session": "", "choices_ide_title": "", "choices_ide_model": "", "choices_ide_auto": False,
    "choices_ide_manual_off": False,
}
NO_FOLDER = {"ide_folder": ""}  # 作業フォルダも持ち越さない（同じ AGENTS.md を2つのチャンネルで取り合わないように）

DEPRECATED_SETTING_KEYS = {"relationship_core", "initial_relationship_core"}
DEPRECATED_IMAGE_KEYS = {"character_tags"}

RESET_PROGRESS = {
    "memo": "",
    "initial_memo": "",
    "turns_in_period": 0,
    "period_turns_limit": 10,
    "total_turns": 0,
    "rotation_count": 0,
    "last_rotation_turn": 0,
    "turns_since_rotation": 0,
}
RESET_STORY = {
    "summary": "",
    "instructions": "",
    "character": {"name": "", "age": "", "profile": "", "speech": ""},
    "scenario": "",
    "first_message": "",
    "chars": [],
    "rag_collections": [],  # ✅ Q1 確定：ストーリー固有RAGは引き継がない
}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id():
    return uuid.uuid4().hex[:10]


def _epoch(ts):
    """now() の時刻（ローカル）も Antigravity の時刻（UTC、末尾 Z）も秒にする。読めなければ None"""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z)?", ts or "")
    if not m:
        return None
    t = time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    sec = datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc).timestamp() if m.group(3) else time.mktime(t)
    return sec + (float(m.group(2)) if m.group(2) else 0)


TASK_TS_KEYS = {
    "user": "ts",
    "image_refine": "image_refine_ts",
    "choices": "choices_ts",
    "memory_extract": "memory_ts",
    "consolidation": "consolidation_ts",
}


def task_ts(msg, task_type):
    """タスク種別に対応した開始時刻を返す（他のタスクの古い ts を拾わない）"""
    if not isinstance(msg, dict):
        return ""
    return msg.get(TASK_TS_KEYS.get(task_type, "ts")) or msg.get("ts", "")


def age(ts):
    ep = _epoch(ts)
    if ep is not None:
        return time.time() - ep
    try:
        return time.time() - time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return 1e9


def archive_sort_key(fname):
    """アーカイブファイル名を数値でソートするためのキー（rot10がrot2の後に来ないようにする）"""
    m = re.search(r"_rot(\d+)_turn(\d+)\.json$", fname)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (0, 0)


def calc_turn_idx(msgs, i, total_turns=None):
    """メッセージリスト msgs 内のインデックス i における通算ユーザーターン番号を計算する。
    ローテーション後のアクティブセッションやアーカイブ snapshot でも正確な通算ターンを返す。"""
    idx_in_file = len([x for x in msgs[:i + 1] if x.get("role") == "user"])
    if not idx_in_file:
        return 0
    if total_turns is not None and total_turns > 0:
        file_total_user = len([x for x in msgs if x.get("role") == "user"])
        if file_total_user > 0 and total_turns >= file_total_user:
            return total_turns - (file_total_user - idx_in_file)
    return idx_in_file


def extract_matching_sentences(text, keywords, max_len=120):
    if not text or not keywords:
        return []
    raw_sentences = re.split(r'([。！？!?\n]+)', text)
    sentences = []
    curr = ""
    for part in raw_sentences:
        curr += part
        if re.search(r'[。！？!?\n]', part):
            s = curr.strip()
            if s:
                sentences.append(s)
            curr = ""
    if curr.strip():
        sentences.append(curr.strip())
    if not sentences:
        sentences = [text.strip()]

    matched = []
    for s in sentences:
        clean_s = s.strip()
        if not clean_s or len(clean_s) < 5:
            continue
        if any(kw in clean_s for kw in keywords):
            if clean_s not in matched:
                matched.append(clean_s[:max_len])
    return matched


def extract_memories_from_text(user_text="", asst_text="", char_name="", user_name="", extra_names=None):
    """ユーザー発言・アシスタント発言から8カテゴリの重要情報をキーワード抽出する（キャラ名またはユーザー名を含む文に限定）"""
    extracted = []
    added_pairs = set()

    raw_names = set()
    for n in (char_name, user_name, "ユーザー", "あなた"):
        if n and isinstance(n, str) and n.strip():
            raw_names.add(n.strip())
    if extra_names:
        for en in extra_names:
            if en and isinstance(en, str) and en.strip():
                raw_names.add(en.strip())

    valid_names = set()
    for n in raw_names:
        valid_names.add(n)
        clean_n = re.sub(r"[\s・,、]+", " ", n).strip()
        for part in clean_n.split():
            if len(part) >= 2:
                valid_names.add(part)
        if len(clean_n) >= 3 and not re.search(r"\s", clean_n):
            valid_names.add(clean_n[-2:])
            if len(clean_n) == 4:
                valid_names.add(clean_n[:2])

    def has_subject(sentence):
        return any(name in sentence for name in valid_names)

    # 1. calling (呼称・口調)
    user_calling_kws = ("呼び方", "呼んで", "名前で呼", "呼び捨て", "って呼", "と呼", "あだ名", "敬語", "お兄ちゃん", "様")
    asst_calling_kws = ("お呼び", "と呼ぶ", "って呼ぶ", "と呼んで", "って呼んで", "呼びます", "呼ぶね", "呼ぶよ", "呼び方", "あだ名")
    for s in extract_matching_sentences(user_text, user_calling_kws):
        if has_subject(s) and ("calling", s) not in added_pairs:
            extracted.append({"category": "calling", "content": s})
            added_pairs.add(("calling", s))
    for s in extract_matching_sentences(asst_text, asst_calling_kws):
        if has_subject(s) and ("calling", s) not in added_pairs:
            extracted.append({"category": "calling", "content": s})
            added_pairs.add(("calling", s))

    # 2. rule_taboo (規則・禁忌)
    rule_kws = ("校則", "禁止", "門限", "ルール", "タブー", "掟", "規則", "約束事", "地雷", "絶対ダメ", "絶対だめ", "二人だけの秘密", "二人の秘密", "内緒", "誰にも言", "秘密にして", "秘密ね")
    for text in (user_text, asst_text):
        for s in extract_matching_sentences(text, rule_kws):
            if has_subject(s) and ("rule_taboo", s) not in added_pairs:
                extracted.append({"category": "rule_taboo", "content": s})
                added_pairs.add(("rule_taboo", s))

    # 3. item_status (アイテム・痕跡 - 一時的な衣装・下着・リボン等は除外)
    item_kws = (
        "プレゼント", "合鍵", "指輪", "ネックレス", "ペンダント", "ブローチ", "アンクレット",
        "首輪", "アナルプラグ", "プラグ", "拘束具", "キスマーク", "傷跡", "傷", "痕",
        "受け取って", "受け取る", "あげた", "あげるよ", "あげるね", "もらった", "お守り", "ピアス", "ブレスレット"
    )
    temp_clothing_kws = ("下着", "ショーツ", "パンツ", "パンティ", "ブラジャー", "ブラ", "リボン", "衣装", "服", "レース")
    persistent_item_kws = ("合鍵", "指輪", "ネックレス", "首輪", "アナルプラグ", "プラグ", "拘束具", "キスマーク", "傷跡", "お守り", "ピアス", "ブレスレット", "ペンダント")

    for text in (user_text, asst_text):
        for s in extract_matching_sentences(text, item_kws):
            if any(cw in s for cw in temp_clothing_kws) and not any(pw in s for pw in persistent_item_kws):
                continue
            if has_subject(s) and ("item_status", s) not in added_pairs:
                extracted.append({"category": "item_status", "content": s})
                added_pairs.add(("item_status", s))

    # 4. promise (約束・予定)
    promise_kws = ("約束", "予定", "誓約", "誓い", "誓う", "行こう", "会おう", "行く約束", "会う約束", "一緒に行", "一緒にいよう", "デートしよう")
    for text in (user_text, asst_text):
        for s in extract_matching_sentences(text, promise_kws):
            if has_subject(s) and ("promise", s) not in added_pairs:
                extracted.append({"category": "promise", "content": s})
                added_pairs.add(("promise", s))

    # 5. char_fact (キャラの秘密・過去・弱点)
    char_kws = ("トラウマ", "弱点", "コンプレックス", "生い立ち", "体質", "私の秘密", "秘密がある", "過去の傷", "実は昔")
    for s in extract_matching_sentences(asst_text, char_kws):
        if has_subject(s) and ("char_fact", s) not in added_pairs:
            extracted.append({"category": "char_fact", "content": s})
            added_pairs.add(("char_fact", s))
    # Contextual char_fact: '実は' or '本当は' + personal facts
    for s in extract_matching_sentences(asst_text, ("実は", "本当は")):
        if any(w in s for w in ("苦手", "好き", "秘密", "怖い", "怖く", "寂し", "昔", "家族", "両親", "病気", "体質", "弱点", "隠して", "本当は", "コンプレックス", "生い立ち")):
            if has_subject(s) and ("char_fact", s) not in added_pairs:
                extracted.append({"category": "char_fact", "content": s})
                added_pairs.add(("char_fact", s))

    # 6. user_fact (ユーザーの好み・個人情報・秘密)
    user_kws = ("好き", "嫌い", "苦手", "趣味", "タイプ", "誕生日", "飼って", "飼い", "飼う", "ペット", "好み", "好物", "俺の秘密", "僕の秘密", "私の秘密")
    for s in extract_matching_sentences(user_text, user_kws):
        if has_subject(s) or any(p in s for p in ("俺", "僕", "私", "自分")):
            content = f"ユーザー: {s}" if not s.startswith("ユーザー:") else s
            if ("user_fact", content) not in added_pairs:
                extracted.append({"category": "user_fact", "content": content})
                added_pairs.add(("user_fact", content))

    # 7. relationship (関係性の変化)
    rel_kws = ("恋人", "付き合", "付き合う", "付き合おう", "仲直り", "信頼", "親密", "両思い", "告白", "愛してる", "愛して", "大切にする", "特別", "大好き", "好きだよ", "好きです", "好きだった", "ずっと一緒", "彼氏", "彼女", "結婚")
    for text in (user_text, asst_text):
        for s in extract_matching_sentences(text, rel_kws):
            if has_subject(s) and ("relationship", s) not in added_pairs:
                extracted.append({"category": "relationship", "content": s})
                added_pairs.add(("relationship", s))

    # 8. event (重要出来事)
    event_kws = ("初めて", "告白", "助けた", "助けて", "救った", "救って", "出会った", "記念", "事件", "雨宿り", "旅行", "お泊まり", "デート")
    for text in (user_text, asst_text):
        for s in extract_matching_sentences(text, event_kws):
            if has_subject(s) and ("event", s) not in added_pairs:
                extracted.append({"category": "event", "content": s})
                added_pairs.add(("event", s))

    return extracted


def extract_memories_fallback(sid, s, m):
    """長期記憶のフォールバック自動抽出（LLMがmemoriesを出力しなかった場合やタイムアウト時）"""
    try:
        if not sid or not isinstance(s, dict) or not isinstance(m, dict):
            return
        st = s.get("setting", {})
        char_name = (st.get("character") or {}).get("name", "")
        user_name = st.get("user_name") or "ユーザー"
        extra = []
        if user_name and user_name not in ("ユーザー", "あなた", ""):
            extra.append(user_name)
        for c in st.get("chars") or []:
            if isinstance(c, dict) and c.get("name"):
                extra.append(c["name"])

        msgs = s.get("messages", [])
        m_id = m.get("id") if isinstance(m, dict) else None
        idx = next((i for i, x in enumerate(msgs) if m_id and x.get("id") == m_id), -1)
        if idx == -1:
            idx = msgs.index(m) if m in msgs else len(msgs) - 1
        user_msgs = [x for x in msgs[:idx + 1] if x.get("role") == "user"]
        turn_idx = st.get("total_turns") or ((st.get("rotation_count", 0) * ROTATION_PERIOD) + len(user_msgs))
        user_m = user_msgs[-1] if user_msgs else None
        user_text = user_m.get("text", "") if user_m else ""
        asst_text = m.get("text", "")

        extracted = extract_memories_from_text(user_text, asst_text, char_name=char_name, user_name=user_name, extra_names=extra)
        if extracted:
            elapsed_time = m.get("elapsed_time") or st.get("elapsed_time") or {"day": 1, "period": "午前"}
            memory_store.save_extracted_memories(
                sid,
                char_name,
                extracted,
                turn_idx=turn_idx,
                mid=m.get("id"),
                elapsed_time=elapsed_time
            )
            print(f"[auto_extract_fallback] saved {len(extracted)} memories for {sid} (turn {turn_idx})")
    except Exception as e:
        print(f"[auto_extract_fallback] error: {e}")



def merge_defaults(setting):
    out = copy.deepcopy(DEFAULT_SETTING)
    for k, v in (setting or {}).items():
        if k in DEPRECATED_SETTING_KEYS:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        elif isinstance(out.get(k), dict) and not isinstance(v, dict):
            continue
        else:
            out[k] = v
    # ループ後にも明示的に除去（二重保険）
    for dk in DEPRECATED_SETTING_KEYS:
        out.pop(dk, None)
    if isinstance(out.get("image"), dict):
        for dk in DEPRECATED_IMAGE_KEYS:
            out["image"].pop(dk, None)
    if isinstance(setting, dict) and isinstance(setting.get("image"), dict):
        img_conf = setting["image"]
        # speed_lora が明示されていない場合、高ステップ（>12）や SD1.5 系モデルなら高速化LoRAを勝手に補完しない
        if "speed_lora" not in img_conf:
            try:
                st_val = int(img_conf.get("steps", 0) or 0)
                if st_val > 12:
                    out["image"]["speed_lora"] = ""
            except (ValueError, TypeError):
                pass
            ckpt_str = str(img_conf.get("checkpoint", "")).lower()
            if any(m in ckpt_str for m in ("1.5", "v1-5", "abyss", "v15")):
                out["image"]["speed_lora"] = ""
                if "clip_skip" not in img_conf:
                    out["image"]["clip_skip"] = 1
    if "suggest_choices" in out:
        v = out["suggest_choices"]
        out["suggest_choices"] = (v.lower() not in ("false", "0", "")) if isinstance(v, str) else bool(v)
    return out


def has_parallel_choices_ide(st):
    """選択肢専用の別セッションが有効に設定されているか判定する"""
    if not isinstance(st, dict) or not st.get("suggest_choices", True):
        return False
    c_ide = (st.get("choices_ide_session") or "").strip()
    m_ide = (st.get("ide_session") or "").strip()
    return bool(c_ide and c_ide != m_ide)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def app_settings():
    return dict(DEFAULT_APP, **read_json(APP_SETTINGS, {}))


# ---------------------------------------------------------------- push
def get_vapid():
    """VAPID キーを取得する（なければ生成して data/vapid_keys.json に保存）"""
    if not Vapid:
        return None, None
    with lock:
        keys = read_json(VAPID_FILE, {})
        if isinstance(keys, dict) and keys.get("private_key") and keys.get("public_key"):
            try:
                v = Vapid.from_pem(keys["private_key"].encode("utf-8"))
                return v, keys["public_key"]
            except Exception as e:
                print(f"[push] failed to load vapid keys: {e}")
        try:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            v = Vapid()
            v.generate_keys()
            raw_pub = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            pub_b64 = base64.urlsafe_b64encode(raw_pub).decode("utf-8").rstrip("=")
            priv_pem = v.private_pem().decode("utf-8")
            write_json(VAPID_FILE, {"private_key": priv_pem, "public_key": pub_b64})
            print("[push] generated new vapid keys")
            return v, pub_b64
        except Exception as e:
            print(f"[push] failed to generate vapid keys: {e}")
            return None, None


def send_push_notification(payload):
    """登録済みのブラウザに Web Push 通知を一括送信する"""
    if not pywebpush or not Vapid:
        print("[push] pywebpush is not installed; skipping push notification")
        return {"sent": 0, "total": 0, "error": "pywebpush not installed"}
    vapid, _ = get_vapid()
    if not vapid:
        print("[push] vapid keys not available; skipping push")
        return {"sent": 0, "total": 0, "error": "vapid keys not available"}

    with lock:
        subs_data = read_json(PUSH_SUBS, {})

    if isinstance(subs_data, dict):
        subs = list(subs_data.values())
    elif isinstance(subs_data, list):
        subs = subs_data
    else:
        subs = []

    if not subs:
        print("[push] no active push subscriptions")
        return {"sent": 0, "total": 0}

    data_str = json.dumps(payload, ensure_ascii=False)
    expired_endpoints = set()
    sent_count = 0

    for sub in subs:
        if not isinstance(sub, dict) or not sub.get("endpoint"):
            continue
        endpoint = sub["endpoint"]
        try:
            pywebpush.webpush(
                subscription_info=sub,
                data=data_str,
                vapid_private_key=vapid,
                vapid_claims={"sub": "mailto:rpchat@example.com"},
                ttl=86400
            )
            sent_count += 1
            print(f"[push] notification sent to {endpoint[:45]}...")
        except pywebpush.WebPushException as ex:
            status_code = getattr(getattr(ex, "response", None), "status_code", None)
            print(f"[push] error sending to {endpoint[:45]}: status={status_code} {ex}")
            if status_code in (404, 410):
                expired_endpoints.add(endpoint)
        except Exception as e:
            print(f"[push] unexpected error sending to {endpoint[:45]}: {e}")

    if expired_endpoints:
        with lock:
            cur = read_json(PUSH_SUBS, {})
            if isinstance(cur, dict):
                for ep in expired_endpoints:
                    cur.pop(ep, None)
                write_json(PUSH_SUBS, cur)
            elif isinstance(cur, list):
                cur = [x for x in cur if isinstance(x, dict) and x.get("endpoint") not in expired_endpoints]
                write_json(PUSH_SUBS, cur)
        print(f"[push] removed {len(expired_endpoints)} expired subscriptions")

    return {"sent": sent_count, "total": len(subs)}


# ---------------------------------------------------------------- storage
def spath(sid):
    if not sid or not all(c.isalnum() or c in "-_" for c in sid):
        raise ValueError("bad sid")
    return os.path.join(SESS_DIR, sid + ".json")


def choices_path(sid):
    return os.path.join(CHOICES_DIR, sid + ".json")


def load_choices_data(sid, readonly=False):
    """choices データを読み込む。
    注意: readonly=True の返り値はキャッシュ本体の参照であるため、呼び出し側での書き換え（破壊的変更）は厳禁。"""
    p = choices_path(sid)
    if not os.path.exists(p):
        choices_cache.pop(sid, None)
        return {}
    try:
        st = os.stat(p)
        cached = choices_cache.get(sid)
        if cached is not None and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
            return cached["data"] if readonly else copy.deepcopy(cached["data"])
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
            data = d if isinstance(d, dict) else {}
            choices_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": data}
            return data if readonly else copy.deepcopy(data)
    except Exception:
        choices_cache.pop(sid, None)
        return {}


def save_choices_data(sid, data):
    os.makedirs(CHOICES_DIR, exist_ok=True)
    p = choices_path(sid)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for _ in range(10):
        try:
            os.replace(tmp, p)
            try:
                st = os.stat(p)
                choices_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": copy.deepcopy(data)}
            except OSError:
                choices_cache.pop(sid, None)
            return
        except PermissionError:
            time.sleep(0.05)


def states_path(sid):
    return os.path.join(STATES_DIR, sid + ".json")


def load_states_data(sid, readonly=False):
    """states データを読み込む。
    注意: readonly=True の返り値はキャッシュ本体の参照であるため、呼び出し側での書き換え（破壊的変更）は厳禁。"""
    p = states_path(sid)
    if not os.path.exists(p):
        states_cache.pop(sid, None)
        return {}
    try:
        st = os.stat(p)
        cached = states_cache.get(sid)
        if cached is not None and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
            return cached["data"] if readonly else copy.deepcopy(cached["data"])
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
            data = d if isinstance(d, dict) else {}
            states_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": data}
            return data if readonly else copy.deepcopy(data)
    except Exception:
        states_cache.pop(sid, None)
        return {}


def save_states_data(sid, data):
    os.makedirs(STATES_DIR, exist_ok=True)
    p = states_path(sid)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for _ in range(10):
        try:
            os.replace(tmp, p)
            try:
                st = os.stat(p)
                states_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": copy.deepcopy(data)}
            except OSError:
                states_cache.pop(sid, None)
            return
        except PermissionError:
            time.sleep(0.05)


def images_path(sid):
    return os.path.join(IMAGES_DIR, sid + ".json")


def load_images_data(sid, readonly=False):
    """images データを読み込む。
    注意: readonly=True の返り値はキャッシュ本体の参照であるため、呼び出し側での書き換え（破壊的変更）は厳禁。"""
    p = images_path(sid)
    if not os.path.exists(p):
        images_cache.pop(sid, None)
        return {}
    try:
        st = os.stat(p)
        cached = images_cache.get(sid)
        if cached is not None and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
            return cached["data"] if readonly else copy.deepcopy(cached["data"])
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
            data = d if isinstance(d, dict) else {}
            images_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": data}
            return data if readonly else copy.deepcopy(data)
    except Exception:
        images_cache.pop(sid, None)
        return {}


def save_images_data(sid, data):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    p = images_path(sid)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for _ in range(10):
        try:
            os.replace(tmp, p)
            try:
                st = os.stat(p)
                images_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": copy.deepcopy(data)}
            except OSError:
                images_cache.pop(sid, None)
            return
        except PermissionError:
            time.sleep(0.05)


def session_needs_migration(s):
    if not isinstance(s, dict):
        return False
    msgs = s.get("messages", [])
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if "state" in m:
            return True
        im = m.get("image")
        if isinstance(im, dict):
            if any(k not in ("file", "status", "error") for k in im):
                return True
        if "choices_updated_ts" in m or "claimed_ts" in m:
            return True
        if m.get("consolidation_status") not in (None, "pending", "processing"):
            return True
    return False


def migrate_session(sid, s):
    """旧形式セッションの自動マイグレーション:
    1. 元ファイルを data/trash/migration_backup_<sid>_<timestamp>.json にバックアップ
    2. state を data/states/<sid>.json に抽出・保存
    3. image の詳細パラメータを data/images/<sid>.json に抽出・保存
    4. セッションJSON本体をスリム化して保存
    """
    p = spath(sid)
    if os.path.exists(p):
        os.makedirs(TRASH, exist_ok=True)
        ts = int(time.time())
        backup_name = f"migration_backup_{sid}_{ts}.json"
        backup_path = os.path.join(TRASH, backup_name)
        try:
            shutil.copy2(p, backup_path)
            print(f"[migration] backed up {sid} to {backup_path}")
        except Exception as e:
            print(f"[migration] failed backup {sid}: {e}")

    sdata = load_states_data(sid)
    idata = load_images_data(sid)
    msgs = s.get("messages", [])
    states_count = 0
    images_count = 0

    for m in msgs:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not mid:
            continue
        if "state" in m:
            st_val = m.pop("state")
            if st_val is not None:
                sdata[mid] = copy.deepcopy(st_val)
                states_count += 1
        im = m.get("image")
        if isinstance(im, dict):
            details = {k: v for k, v in im.items() if k not in ("file", "status", "error")}
            if details:
                cur_im = idata.get(mid, {})
                cur_im.update(details)
                idata[mid] = cur_im
                images_count += 1
            m["image"] = {k: v for k, v in im.items() if k in ("file", "status", "error")}

    save_states_data(sid, sdata)
    save_images_data(sid, idata)
    save(s)
    print(f"[migration] completed for {sid}: extracted {states_count} states, {images_count} image details")


def migrate_all_sessions():
    """サーバー起動時に全セッションの旧形式データを検知して自動マイグレーション"""
    for sid in list_ids():
        try:
            p = spath(sid)
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8-sig") as f:
                raw_s = json.load(f)
            if session_needs_migration(raw_s):
                print(f"[migration] auto-migrating session {sid}...")
                with lock:
                    migrate_session(sid, raw_s)
        except Exception as e:
            print(f"[migration] error checking/migrating {sid}: {e}")



def list_ids():
    return [n[:-5] for n in os.listdir(SESS_DIR) if n.endswith(".json")]


def clone_session(s):
    """copy.deepcopy(s) より高速なセッション辞書専用のディープコピー"""
    if not isinstance(s, dict):
        return s
    out = dict(s)
    if "setting" in out and isinstance(out["setting"], dict):
        st = dict(out["setting"])
        if "character" in st and isinstance(st["character"], dict):
            st["character"] = dict(st["character"])
        if "image" in st and isinstance(st["image"], dict):
            st["image"] = dict(st["image"])
        if "chars" in st and isinstance(st["chars"], list):
            st["chars"] = [dict(c) if isinstance(c, dict) else c for c in st["chars"]]
        if "rag_collections" in st and isinstance(st["rag_collections"], list):
            st["rag_collections"] = list(st["rag_collections"])
        if "ref_files" in st and isinstance(st["ref_files"], list):
            st["ref_files"] = list(st["ref_files"])
        if "elapsed_time" in st and isinstance(st["elapsed_time"], dict):
            st["elapsed_time"] = dict(st["elapsed_time"])
        if "initial_elapsed_time" in st and isinstance(st["initial_elapsed_time"], dict):
            st["initial_elapsed_time"] = dict(st["initial_elapsed_time"])
        if "folder_files" in st and isinstance(st["folder_files"], list):
            st["folder_files"] = list(st["folder_files"])
        if "rotation" in st and isinstance(st["rotation"], dict):
            st["rotation"] = copy.deepcopy(st["rotation"])
        if "rotation_reserve" in st and isinstance(st["rotation_reserve"], dict):
            st["rotation_reserve"] = dict(st["rotation_reserve"])
        out["setting"] = st
    if "messages" in out and isinstance(out["messages"], list):
        msgs = []
        for m in out["messages"]:
            if not isinstance(m, dict):
                msgs.append(m)
                continue
            mc = dict(m)
            if "image" in mc and isinstance(mc["image"], dict):
                im = dict(mc["image"])
                if "history" in im and isinstance(im["history"], list):
                    im["history"] = [dict(h) if isinstance(h, dict) else h for h in im["history"]]
                if "chars" in im and isinstance(im["chars"], list):
                    im["chars"] = list(im["chars"])
                mc["image"] = im
            if "choices" in mc and isinstance(mc["choices"], list):
                mc["choices"] = [dict(c) if isinstance(c, dict) else c for c in mc["choices"]]
            if "staged_choices" in mc and isinstance(mc["staged_choices"], list):
                mc["staged_choices"] = [dict(c) if isinstance(c, dict) else c for c in mc["staged_choices"]]
            if "state" in mc and isinstance(mc["state"], list):
                states = []
                for st_item in mc["state"]:
                    if isinstance(st_item, dict):
                        sc = dict(st_item)
                        if "situation" in sc and isinstance(sc["situation"], list):
                            sc["situation"] = list(sc["situation"])
                        if "expression" in sc and isinstance(sc["expression"], list):
                            sc["expression"] = list(sc["expression"])
                        states.append(sc)
                    else:
                        states.append(st_item)
                mc["state"] = states
            if "state_turn" in mc and isinstance(mc["state_turn"], list):
                mc["state_turn"] = [dict(c) if isinstance(c, dict) else c for c in mc["state_turn"]]
            if "files" in mc and isinstance(mc["files"], list):
                mc["files"] = [dict(f) if isinstance(f, dict) else f for f in mc["files"]]
            msgs.append(mc)
        out["messages"] = msgs
    return out


def _has_expired_subtasks(s):
    if not s or not isinstance(s, dict):
        return False
    msgs = s.get("messages", [])
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("image_refine_status") in ("pending", "processing"):
            is_proc = m.get("image_refine_status") == "processing"
            t_ref = m.get("image_refine_claimed_ts") if is_proc else (m.get("image_refine_ts") or m.get("ts"))
            timeout_limit = 120 if is_proc else 300
            if t_ref and age(t_ref) > timeout_limit:
                return True
        if m.get("choices_status") in ("pending", "processing"):
            is_proc = m.get("choices_status") == "processing"
            t_ref = m.get("choices_claimed_ts") if is_proc else (m.get("choices_ts") or m.get("ts"))
            timeout_limit = 90 if is_proc else 120
            if t_ref and age(t_ref) > timeout_limit:
                return True
        if m.get("role") == "assistant":
            if m.get("memory_status") in ("pending", "processing"):
                is_proc = m.get("memory_status") == "processing"
                t_ref = m.get("memory_claimed_ts") if is_proc else (m.get("memory_ts") or m.get("ts"))
                timeout_limit = 90 if is_proc else 180
                if t_ref and age(t_ref) > timeout_limit:
                    return True
            if m.get("consolidation_status") in ("pending", "processing"):
                is_proc = m.get("consolidation_status") == "processing"
                t_ref = m.get("consolidation_claimed_ts") if is_proc else (m.get("consolidation_ts") or m.get("ts"))
                timeout_limit = 90 if is_proc else 180
                if t_ref and age(t_ref) > timeout_limit:
                    return True
    return False


def load(sid, readonly=False):
    with lock:
        p = spath(sid)
        try:
            st_sess = os.stat(p)
        except OSError:
            session_cache.pop(sid, None)
            session_revs.pop(sid, None)
            session_ide_cache.pop(sid, None)
            summary_cache.pop(sid, None)
            raise FileNotFoundError(f"No session file for {sid}")

        cp = choices_path(sid)
        try:
            st_choices_mtime = os.stat(cp).st_mtime
        except OSError:
            st_choices_mtime = 0.0

        sp = states_path(sid)
        try:
            st_states_mtime = os.stat(sp).st_mtime
        except OSError:
            st_states_mtime = 0.0

        ip = images_path(sid)
        try:
            st_images_mtime = os.stat(ip).st_mtime
        except OSError:
            st_images_mtime = 0.0

        cached = session_cache.get(sid)
        if (cached is not None
                and cached["mtime"] == st_sess.st_mtime
                and cached["size"] == st_sess.st_size
                and cached.get("choices_mtime") == st_choices_mtime
                and cached.get("states_mtime") == st_states_mtime
                and cached.get("images_mtime") == st_images_mtime
                and not _has_expired_subtasks(cached.get("s"))):
            return cached["s"] if readonly else clone_session(cached["s"])

        with open(p, encoding="utf-8-sig") as f:
            s = json.load(f)

        if session_needs_migration(s):
            migrate_session(sid, s)
            try:
                st_sess = os.stat(p)
            except OSError:
                pass
            try:
                st_states_mtime = os.stat(sp).st_mtime
            except OSError:
                st_states_mtime = 0.0
            try:
                st_images_mtime = os.stat(ip).st_mtime
            except OSError:
                st_images_mtime = 0.0

        s["setting"] = merge_defaults(s.get("setting"))
        session_revs[s["id"]] = s.get("rev", 0)
        st = s.get("setting", {})
        session_ide_cache[sid] = (st.get("ide_session", ""), st.get("choices_ide_session", ""))

        # 選択肢（choices）の分離読み込み & 旧形式データの自動移行
        cdata = load_choices_data(sid)
        dirty_cdata = False
        msgs = s.get("messages", [])
        for m in msgs:
            mid = m.get("id")
            if mid and "choices" in m:
                if mid not in cdata:
                    cdata[mid] = {"choices": m["choices"], "updated_ts": m.get("choices_updated_ts") or m.get("ts")}
                    dirty_cdata = True
                del m["choices"]

        # 直近5ターンの choices のみ残してプルーニング
        asst_mids = [m["id"] for m in msgs if m.get("role") == "assistant" and m.get("id")]
        recent_5 = set(asst_mids[-5:]) if len(asst_mids) > 5 else set(asst_mids)
        pruned = {mid: v for mid, v in cdata.items() if mid in recent_5}
        if len(pruned) != len(cdata):
            cdata = pruned
            dirty_cdata = True

        if dirty_cdata:
            save_choices_data(sid, cdata)
            try:
                st_choices_mtime = os.stat(cp).st_mtime
            except OSError:
                st_choices_mtime = 0.0

        # メモリ上のメッセージに choices をマージ
        for m in msgs:
            mid = m.get("id")
            if mid and mid in cdata:
                m["choices"] = cdata[mid].get("choices") or []
            else:
                m.pop("choices", None)

        # キャラクターステータス（state）および画像詳細メタデータ（image）の最新1〜2件マージ
        sdata = load_states_data(sid, readonly=True)
        idata = load_images_data(sid, readonly=True)
        recent_2_assts = set(asst_mids[-2:]) if len(asst_mids) > 2 else set(asst_mids)

        for m in msgs:
            mid = m.get("id")
            if not mid:
                continue
            if mid in recent_2_assts and mid in sdata:
                m["state"] = copy.deepcopy(sdata[mid])
            else:
                m.pop("state", None)

            im = m.get("image")
            if isinstance(im, dict):
                keep_img_meta = (
                    mid in recent_2_assts
                    or im.get("status") in ("queued", "generating")
                    or im.get("refined_prompt")
                    or (mid in idata and idata[mid].get("refined_prompt"))
                    or m.get("image_refine_status") in ("pending", "processing")
                )
                if keep_img_meta:
                    if mid in idata:
                        for k, v in idata[mid].items():
                            if (k not in im or im[k] is None) and v is not None:
                                im[k] = copy.deepcopy(v)
                else:
                    if mid in idata and idata[mid].get("refined_prompt") and not im.get("refined_prompt"):
                        im["refined_prompt"] = copy.deepcopy(idata[mid]["refined_prompt"])

        dirty_s = False
        for m in msgs:
            if m.get("image_refine_status") in ("pending", "processing"):
                is_proc = m.get("image_refine_status") == "processing"
                t_ref = m.get("image_refine_claimed_ts") if is_proc else (m.get("image_refine_ts") or m.get("ts"))
                timeout_limit = 120 if is_proc else 300
                if t_ref and age(t_ref) > timeout_limit:
                    m["image_refine_status"] = "error"
                    m["image_refine_error"] = "推敲がタイムアウトしました"
                    m.pop("image_refine_mode", None)
                    m.pop("image_refine_ts", None)
                    m.pop("image_refine_claimed_ts", None)
                    m.pop("image_refine_prompt", None)
                    dirty_s = True
            if m.get("choices_status") in ("pending", "processing"):
                is_proc = m.get("choices_status") == "processing"
                t_ref = m.get("choices_claimed_ts") if is_proc else (m.get("choices_ts") or m.get("ts"))
                timeout_limit = 90 if is_proc else 120
                if t_ref and age(t_ref) > timeout_limit:
                    m.pop("choices_status", None)
                    m.pop("choices_type", None)
                    m.pop("choices_ts", None)
                    m.pop("choices_claimed_ts", None)
                    m.pop("staged_choices", None)
                    m["choices_timeout"] = True
                    dirty_s = True
            if m.get("role") == "assistant":
                if m.get("memory_status") in ("pending", "processing"):
                    is_proc = m.get("memory_status") == "processing"
                    t_ref = m.get("memory_claimed_ts") if is_proc else (m.get("memory_ts") or m.get("ts"))
                    timeout_limit = 90 if is_proc else 180
                    if t_ref and age(t_ref) > timeout_limit:
                        extract_memories_fallback(sid, s, m)
                        m.pop("memory_status", None)
                        m.pop("memory_ts", None)
                        m.pop("memory_claimed_ts", None)
                        dirty_s = True
                if m.get("consolidation_status") in ("pending", "processing"):
                    is_proc = m.get("consolidation_status") == "processing"
                    t_ref = m.get("consolidation_claimed_ts") if is_proc else (m.get("consolidation_ts") or m.get("ts"))
                    timeout_limit = 90 if is_proc else 180
                    if t_ref and age(t_ref) > timeout_limit:
                        m.pop("consolidation_status", None)
                        m.pop("consolidation_ts", None)
                        m.pop("consolidation_claimed_ts", None)
                        dirty_s = True

        if dirty_s:
            save(s)
            try:
                st_sess = os.stat(p)
            except OSError:
                pass

        session_cache[sid] = {
            "mtime": st_sess.st_mtime,
            "size": st_sess.st_size,
            "choices_mtime": st_choices_mtime,
            "states_mtime": st_states_mtime,
            "images_mtime": st_images_mtime,
            "s": clone_session(s)
        }
        for m in msgs[-20:]:
            mid = m.get("id")
            if mid:
                msg_to_session[mid] = sid
        return s if readonly else clone_session(s)


def save(s):
    with lock:
        s["rev"] = int(s.get("rev", 0)) + 1
        s["updated"] = now()
        sid = s["id"]
        session_revs[sid] = s["rev"]
        summary_cache.pop(sid, None)

        msgs = s.get("messages", [])
        for m in msgs[-20:]:
            mid = m.get("id")
            if mid:
                msg_to_session[mid] = sid

        # 選択肢（choices）を data/choices/<sid>.json に同期（変更時のみ書き込む Copy-on-Write）
        has_choices = any("choices" in m and m["choices"] for m in msgs)
        cp = choices_path(sid)
        cdata_ro = load_choices_data(sid, readonly=True)
        cdata = None
        if has_choices:
            for m in msgs:
                mid = m.get("id")
                if mid and "choices" in m and m["choices"]:
                    if cdata_ro.get(mid, {}).get("choices") != m["choices"]:
                        if cdata is None:
                            cdata = dict(cdata_ro)
                        cdata[mid] = {"choices": m["choices"], "updated_ts": m.get("choices_updated_ts") or now()}

        asst_mids = [m["id"] for m in msgs if m.get("role") == "assistant" and m.get("id")]
        recent_5 = set(asst_mids[-5:]) if len(asst_mids) > 5 else set(asst_mids)
        recent_2_assts = set(asst_mids[-2:]) if len(asst_mids) > 2 else set(asst_mids)
        base_cdata = cdata if cdata is not None else cdata_ro
        if any(mid not in recent_5 for mid in base_cdata):
            pruned = {mid: v for mid, v in base_cdata.items() if mid in recent_5}
            save_choices_data(sid, pruned)
        elif cdata is not None:
            pruned = cdata
            save_choices_data(sid, pruned)
        else:
            pruned = cdata_ro

        # メモリ上(s)でも直近5ターン以外の古い choices を除去
        for m in msgs:
            mid = m.get("id")
            if mid and mid not in pruned:
                m.pop("choices", None)

        try:
            st_choices_mtime = os.stat(cp).st_mtime
        except OSError:
            st_choices_mtime = 0.0

        existing_mids = {m["id"] for m in msgs if m.get("id")}

        # states を data/states/<sid>.json に同期（Copy-on-Write）
        sp = states_path(sid)
        sdata_ro = load_states_data(sid, readonly=True)
        sdata = None
        for m in msgs:
            mid = m.get("id")
            if mid and "state" in m and m["state"] is not None:
                if sdata_ro.get(mid) != m["state"]:
                    if sdata is None:
                        sdata = dict(sdata_ro)
                    sdata[mid] = m["state"]
        base_sdata = sdata if sdata is not None else sdata_ro
        if any(mid not in existing_mids for mid in base_sdata):
            pruned_sdata = {mid: v for mid, v in base_sdata.items() if mid in existing_mids}
            save_states_data(sid, pruned_sdata)
        elif sdata is not None:
            pruned_sdata = sdata
            save_states_data(sid, pruned_sdata)
        else:
            pruned_sdata = sdata_ro
        try:
            st_states_mtime = os.stat(sp).st_mtime
        except OSError:
            st_states_mtime = 0.0

        # images 詳細メタデータを data/images/<sid>.json に同期（Copy-on-Write）
        ip = images_path(sid)
        idata_ro = load_images_data(sid, readonly=True)
        idata = None
        for m in msgs:
            mid = m.get("id")
            im = m.get("image")
            if mid and isinstance(im, dict):
                details = {k: v for k, v in im.items() if k not in ("file", "status", "error")}
                if details:
                    cur_saved = idata_ro.get(mid, {})
                    merged = {k: v for k, v in dict(cur_saved, **details).items() if v is not None}
                    if cur_saved != merged:
                        if idata is None:
                            idata = dict(idata_ro)
                        idata[mid] = merged
                for k in [k for k, v in im.items() if v is None]:
                    im.pop(k, None)
        base_idata = idata if idata is not None else idata_ro
        if any(mid not in existing_mids for mid in base_idata):
            pruned_idata = {mid: v for mid, v in base_idata.items() if mid in existing_mids}
            save_images_data(sid, pruned_idata)
        elif idata is not None:
            pruned_idata = idata
            save_images_data(sid, pruned_idata)
        else:
            pruned_idata = idata_ro
        try:
            st_images_mtime = os.stat(ip).st_mtime
        except OSError:
            st_images_mtime = 0.0

        # メモリ上(s)の最新1〜2件のアシスタントには最新 state および画像詳細メタデータを確実に同期マージし、
        # それ以前のアシスタントからは除去（キャッシュ一貫性とスリム化の両立）
        for m in msgs:
            mid = m.get("id")
            if not mid:
                continue
            if mid in recent_2_assts and mid in pruned_sdata:
                m["state"] = copy.deepcopy(pruned_sdata[mid])
            else:
                m.pop("state", None)

            im = m.get("image")
            if isinstance(im, dict):
                keep_img_meta = (
                    mid in recent_2_assts
                    or im.get("status") in ("queued", "generating")
                    or im.get("refined_prompt")
                    or (mid in pruned_idata and pruned_idata[mid].get("refined_prompt"))
                    or m.get("image_refine_status") in ("pending", "processing")
                )
                if keep_img_meta:
                    for k in [k for k, v in im.items() if v is None]:
                        im.pop(k, None)
                    if mid in pruned_idata:
                        for k, v in pruned_idata[mid].items():
                            if (k not in im or im[k] is None) and v is not None:
                                im[k] = copy.deepcopy(v)
                else:
                    m["image"] = {k: v for k, v in im.items() if k in ("file", "status", "error", "refined_prompt") and v is not None}

        st = s.get("setting", {})
        session_ide_cache[sid] = (st.get("ide_session", ""), st.get("choices_ide_session", ""))

        # session.json に保存する用のクローンを作成（choices, state および一時フラグ・画像詳細メタデータを完全に除外してファイル肥大化を根本防止）
        disk_s = dict(s)
        disk_st = disk_s.get("setting", {})
        if isinstance(disk_st, dict):
            for dk in DEPRECATED_SETTING_KEYS:
                disk_st.pop(dk, None)
            if isinstance(disk_st.get("image"), dict):
                for dk in DEPRECATED_IMAGE_KEYS:
                    disk_st["image"].pop(dk, None)
        disk_msgs = []
        for m in msgs:
            dm = {k: v for k, v in m.items() if k not in (
                "choices", "choices_updated_ts", "claimed_ts", "state",
                "relationship_core", "memo", "elapsed_time",
                "snapshot_relationship_core", "snapshot_elapsed_time"
            )}
            if dm.get("role") == "user" and dm.get("status") not in ("pending", "processing"):
                dm.pop("staged_choices", None)
                dm.pop("choices_status", None)
                dm.pop("choices_type", None)
                dm.pop("choices_ts", None)
                dm.pop("choices_claimed_ts", None)
            if dm.get("consolidation_status") not in ("pending", "processing"):
                dm.pop("consolidation_status", None)
                dm.pop("consolidation_ts", None)
                dm.pop("consolidation_claimed_ts", None)
            if dm.get("memory_status") not in ("pending", "processing"):
                dm.pop("memory_status", None)
                dm.pop("memory_ts", None)
                dm.pop("memory_claimed_ts", None)
            if dm.get("choices_status") not in ("pending", "processing"):
                dm.pop("choices_status", None)
                dm.pop("choices_type", None)
                dm.pop("choices_ts", None)
                dm.pop("choices_claimed_ts", None)
                dm.pop("choices_orig_id", None)
            if dm.get("image_refine_status") not in ("pending", "processing"):
                dm.pop("image_refine_status", None)
                dm.pop("image_refine_mode", None)
                dm.pop("image_refine_ts", None)
                dm.pop("image_refine_claimed_ts", None)
                dm.pop("image_refine_prompt", None)
                dm.pop("image_refine_error", None)

            if "image" in dm and isinstance(dm["image"], dict):
                dm["image"] = {k: v for k, v in dm["image"].items() if k in ("file", "status", "error")}
            disk_msgs.append(dm)
        disk_s["messages"] = disk_msgs

        p = spath(sid)
        tmp = f"{p}.{os.getpid()}.tmp"  # サーバーが2つ動いていても一時ファイルがぶつからないように
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(disk_s, f, ensure_ascii=False, indent=2)
        for _ in range(10):
            try:
                os.replace(tmp, p)
                poke_event.set()
                st_cur = s.get("setting", {})
                if st_cur.get("ide_auto") or st_cur.get("choices_ide_auto"):
                    ide_auto_event.set()
                try:
                    st_sess = os.stat(p)
                    session_cache[sid] = {
                        "mtime": st_sess.st_mtime,
                        "size": st_sess.st_size,
                        "choices_mtime": st_choices_mtime,
                        "states_mtime": st_states_mtime,
                        "images_mtime": st_images_mtime,
                        "s": clone_session(s)
                    }
                except OSError:
                    session_cache.pop(sid, None)
                return
            except PermissionError:
                time.sleep(0.05)
        raise


known_ides = None


def user_turns_of(msgs):
    return sum(1 for m in msgs if m.get("role") == "user")


def total_turns_base(st, file_turns):
    """通算ターン番号の基準。total_turns が未設定(0)の旧セッションだけ回転回数から補う。
    既存の読み取り側フォールバック (rot_cnt * ROTATION_PERIOD + in_file) と**同じ式**にすること。"""
    total = int(st.get("total_turns") or 0)
    if total > 0:
        return total
    return (int(st.get("rotation_count") or 0) * ROTATION_PERIOD) + int(file_turns)


def rotation_state(s):
    r = s["setting"].get("rotation")
    return r if isinstance(r, dict) and r.get("phase") else {}


def rotation_phase(s):
    return rotation_state(s).get("phase", "")


def rotation_idle(s):
    """cutover を止めるべき「未応答のユーザー発言」があるか（choices/memo/image は止めない）"""
    for m in s.get("messages", []):
        if m.get("role") == "user" and m.get("status") in ("pending", "processing"):
            return False
    return True


def rotation_skipping(s):
    """中止直後のスキップ期間中か（in-file ターン数で判定）"""
    until = int(s["setting"].get("rotation_skip_until") or 0)
    return bool(until) and user_turns_of(s.get("messages", [])) < until


def ide_is_known(ide):
    global known_ides
    if known_ides is None:
        known_ides = set(read_json(IDE_KNOWN, []))
    return ide in known_ides


def rotation_put(sid, **kw):
    """rotation を部分更新して保存（ロック内。**RPC 禁止**）"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return {}
        r = rotation_state(s)
        if not r:
            return {}
        r.update(kw)
        r["updated"] = now()
        s["setting"]["rotation"] = r
        save(s)
        return clone_session(s)


def rotation_schedule(sid, delay=0.0):
    with rotation_guard:
        rotation_queue[sid] = time.monotonic() + max(0.0, delay)
    rotation_event.set()          # phase 書き込みの後で呼ぶこと


def rotation_fail(sid, why, log=True):
    """中止して旧会話のまま安全側に戻す（I9）。作ってあった予備会話は再利用候補として残す（I11）。"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return
        st = s["setting"]
        r = rotation_state(s)
        if not r:
            return
        if r.get("new_ide") or r.get("new_choices_ide"):
            st["rotation_reserve"] = {
                "ide": r.get("new_ide") or "",
                "choices_ide": r.get("new_choices_ide") or "",
                "title": r.get("new_ide_title") or r.get("new_choices_ide_title") or "",
                "choices_title": r.get("new_choices_ide_title") or "",
                "ts": now(),
            }
        st["rotation_skip_until"] = user_turns_of(s["messages"]) + ROT_SKIP_TURNS
        st["rotation_notice"] = f"⚠️ 世代交代の準備に失敗しました（{now()[11:16]} / {why}）"
        st.pop("rotation", None)
        save(s)
    if log:
        print(f"[rot] {sid[:6]} failed: {why}")
    with rotation_guard:
        rotation_queue.pop(sid, None)


def rotation_abort_if_active(s, why):
    """回転中なら中止し、メモリ上のセッション辞書 s も同期する（直後の save(s) で復活するのを防ぐ）"""
    if rotation_state(s):
        rotation_fail(s["id"], why)
        st = s["setting"]
        st.pop("rotation", None)
        st["rotation_skip_until"] = user_turns_of(s.get("messages", [])) + ROT_SKIP_TURNS
        st["rotation_notice"] = f"⚠️ 世代交代の準備に失敗しました（{now()[11:16]} / {why}）"
        try:
            fresh = load(s["id"])
            if "rotation_reserve" in fresh["setting"]:
                st["rotation_reserve"] = fresh["setting"]["rotation_reserve"]
            else:
                st.pop("rotation_reserve", None)
        except (OSError, ValueError):
            pass


def rotation_maybe_arm(s):
    """30ターン到達を検知して armed にする（冪等・数ミリ秒）。
    - 既に phase があれば何もしない
    - 非常停止 / 担当 IDE が無い / ide_poke が無い環境では回転しない
    - 中止直後のスキップ期間は武装しない
    """
    if not ROTATION_ENABLED:
        return False
    st = s["setting"]
    if not ide_poke or not (st.get("ide_session") or "").strip():
        return False
    if rotation_state(s):
        return False
    if rotation_skipping(s):
        return False
    in_file = user_turns_of(s.get("messages", []))
    if in_file < ROTATION_TURNS:
        return False
    st["rotation"] = {
        "phase": "armed",
        "rot_no": int(st.get("rotation_count") or 0) + 1,
        "armed_turn": in_file,
        "old_ide": st.get("ide_session") or "",
        "old_ide_title": st.get("ide_title") or "",
        "old_model": st.get("ide_model") or "",
        "old_choices_ide": st.get("choices_ide_session") or "",
        "old_choices_ide_title": st.get("choices_ide_title") or "",
        "old_choices_model": st.get("choices_ide_model") or "",
        "attempts": {},
        "since": now(), "updated": now(),
    }
    print(f"[rot] {s['id'][:6]} armed (turn {in_file}, rot {st['rotation']['rot_no']})")
    return True


def rotation_note_body(sid):
    """本文が確定した直後に呼ぶ（I1: RPC 禁止・数ミリ秒）。
    armed を archiving に進める。armed が無い旧セッションはここで作る。"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return
        st = s["setting"]
        r = rotation_state(s)
        if not r:
            if not rotation_maybe_arm(s):
                return
            r = rotation_state(s)
        if r.get("phase") != "armed":
            return                      # すでに進行中（冪等）
        if user_turns_of(s["messages"]) < ROTATION_TURNS:
            return
        # 【重要】ここで rotation_idle を要求してはいけない。
        # ユーザーが応答を待たずに連投すると pending が消えず、退避も新規作成も永久に始まらない。
        # 「未応答ゼロ」を要求するのは cutover の1箇所だけ（I7）。
        r["phase"] = "archiving"
        r["settle_since"] = now()
        r["updated"] = now()
        st["rotation"] = r
        save(s)
    rotation_schedule(sid, 0.0)


rotate_ide = rotation_note_body


def backup(s, why):
    """消える操作の前に今の状態を data/trash に残しておく（外部化した state と image も結合した完全スナップショットを退避）"""
    os.makedirs(TRASH, exist_ok=True)
    sid = s.get("id")
    snap = clone_session(s)
    if sid:
        try:
            sdata = load_states_data(sid, readonly=True)
            idata = load_images_data(sid, readonly=True)
            for m in snap.get("messages", []):
                mid = m.get("id")
                if mid:
                    if mid in sdata:
                        m["state"] = copy.deepcopy(sdata[mid])
                    if mid in idata:
                        im = m.setdefault("image", {})
                        for k, v in idata[mid].items():
                            if k not in im or im[k] is None:
                                im[k] = copy.deepcopy(v)
        except Exception as e:
            print(f"[backup] failed to enrich snapshot with states/images: {e}")
    write_json(os.path.join(TRASH, f"{s['id']}_{int(time.time())}_{why}.json"), snap)


VALID_PERIODS = ("早朝", "午前", "昼", "夕方", "夜", "深夜")


def normalize_period(p):
    p = str(p or "").strip()
    if p in VALID_PERIODS:
        return p
    if p in ("朝", "あさ"):
        return "早朝"
    if p in ("午後", "昼下がり"):
        return "昼"
    return "午前"


def infer_initial_period(text):
    if not text:
        return "午前"
    if any(k in text for k in ("深夜", "真夜中", "ベッドの中", "草木も眠る", "午前2時", "午前3時", "未明")):
        return "深夜"
    if any(k in text for k in ("夜", "帰宅", "夕食", "晩御飯", "晩ごはん", "お風呂", "入浴", "夜道", "夜更かし")):
        return "夜"
    if any(k in text for k in ("放課後", "夕方", "黄昏", "夕焼け", "下校", "終礼", "夕暮れ")):
        return "夕方"
    if any(k in text for k in ("昼休み", "昼食", "お弁当", "ランチ", "正午", "お昼", "午後", "5時間目", "6時間目", "昼下がり")):
        return "昼"
    if any(k in text for k in ("早朝", "朝焼け", "夜明け", "目覚まし", "起床", "朝練", "朝の", "朝 ")):
        return "早朝"
    if any(k in text for k in ("朝", "登校", "ホームルーム", "午前", "1時間目", "始業")):
        return "午前"
    return "午前"


def restore_latest_snapshot(s):
    st = s["setting"]
    last_asst = next((m for m in reversed(s.get("messages", [])) if m.get("role") == "assistant"), None)
    if last_asst:
        et = last_asst.get("elapsed_time") or last_asst.get("snapshot_elapsed_time")
        if et:
            st["elapsed_time"] = copy.deepcopy(et)

        memo_val = last_asst.get("memo")
        if memo_val is not None:
            st["memo"] = memo_val
    else:
        # 会話が全削除された場合は初期設定スナップショットへ完全復帰
        if st.get("initial_elapsed_time"):
            st["elapsed_time"] = copy.deepcopy(st["initial_elapsed_time"])
        else:
            st["elapsed_time"] = copy.deepcopy(DEFAULT_SETTING.get("elapsed_time", {"day": 1, "period": "午前"}))

        if st.get("initial_memo") is not None:
            st["memo"] = st["initial_memo"]
        else:
            st["memo"] = ""


def create_session(setting=None, messages=None):
    with lock:
        sid = new_id()
        has_custom_elapsed = (
            isinstance(setting, dict)
            and isinstance(setting.get("elapsed_time"), dict)
            and bool(setting["elapsed_time"].get("period"))
        )
        has_custom_initial = (
            isinstance(setting, dict)
            and isinstance(setting.get("initial_elapsed_time"), dict)
            and bool(setting["initial_elapsed_time"].get("period"))
        )
        st = merge_defaults(setting)
        # rotation_notice と last_memo_mid は新規作成・インポート問わず常に除去
        st.pop("rotation_notice", None)
        st.pop("last_memo_mid", None)
        st.pop("rotation", None)              # 進行中の回転は持ち込まない
        st.pop("rotation_skip_until", None)
        st.pop("rotation_reserve", None)
        if not has_custom_elapsed:
            scenario_text = (st.get("scenario") or "") + " " + (st.get("first_message") or "") + " " + (st.get("summary") or "")
            inferred = infer_initial_period(scenario_text)
            st["elapsed_time"] = {"day": 1, "period": inferred}
        elif not isinstance(st.get("elapsed_time"), dict) or not st["elapsed_time"].get("period"):
            st["elapsed_time"] = {"day": 1, "period": "午前"}
        if not has_custom_initial or not isinstance(st.get("initial_elapsed_time"), dict) or not st["initial_elapsed_time"].get("period"):
            st["initial_elapsed_time"] = copy.deepcopy(st["elapsed_time"])
        if not st.get("initial_memo"):
            st["initial_memo"] = st.get("memo", "")
        s = {"id": sid, "rev": 0, "created": now(), "updated": now(), "setting": st, "messages": messages or []}
        if messages is None and st.get("first_message"):
            s["messages"].append({
                "id": new_id(),
                "role": "assistant",
                "text": st["first_message"],
                "ts": now()
            })
        save(s)
        return s


def find_msg(s, mid):
    for m in reversed(s.get("messages", [])):
        if m.get("id") == mid:
            return m
    return None


def session_of_msg(mid):
    cached_sid = msg_to_session.get(mid)
    if cached_sid and os.path.exists(spath(cached_sid)):
        return cached_sid
    for sid in list_ids():
        s = load(sid, readonly=True)
        if find_msg(s, mid):
            msg_to_session[mid] = sid
            return sid
    if os.path.isdir(ARCHIVE_DIR):
        for fname in sorted(os.listdir(ARCHIVE_DIR), key=archive_sort_key, reverse=True):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(ARCHIVE_DIR, fname), "r", encoding="utf-8") as af:
                    arc_s = json.load(af)
                    if find_msg(arc_s, mid):
                        sid = arc_s.get("id") or (fname.rsplit("_rot", 1)[0] if "_rot" in fname else fname.split("_")[0])
                        msg_to_session[mid] = sid
                        return sid
            except Exception:
                continue
    return None


def rag_path(cid):
    if not isinstance(cid, str) or not cid or not all(c.isalnum() or c in "-_" for c in cid):
        raise ValueError("bad collection id")
    return os.path.join(RAG_DIR, cid + ".json")


def load_rag(cid):
    with open(rag_path(cid), encoding="utf-8") as f:
        return json.load(f)


def save_rag(col):
    write_json(rag_path(col["id"]), col)


def rag_summary():
    result = []
    for name in sorted(os.listdir(RAG_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            col = load_rag(name[:-5])
            result.append({"id": col["id"], "name": col["name"], "entryCount": len(col.get("entries", []))})
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return result


def rag_context(setting):
    parts = []
    for cid in setting.get("rag_collections", []):
        try:
            col = load_rag(cid)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if col.get("entries"):
            parts.append("【" + col.get("name", "") + "】")
            parts.extend("## " + e.get("title", "") + "\n" + e.get("content", "") for e in col["entries"])
    return "\n\n".join(parts)


# ---------------------------------------------------------------- setting files
def safe_name(n):
    return isinstance(n, str) and n and n not in (".", "..") and "/" not in n and "\\" not in n


def system_files():
    root = app_settings()["system_root"]
    out = []
    if not os.path.isdir(root):
        return out
    for folder in sorted(os.listdir(root)):
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d, n)) and n.lower().endswith((".md", ".txt")):
                out.append(f"{folder}/{n}")
    return out


def system_file_path(rel):
    parts = rel.split("/") if isinstance(rel, str) else []
    if len(parts) != 2 or not all(safe_name(p) for p in parts):
        raise ValueError(f"ファイル指定が不正です: {rel}")
    return os.path.join(app_settings()["system_root"], *parts)


def str_list(v):
    """文字列のリスト（空・文字列以外は落とす）"""
    return [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def file_folders():
    """「読み込むファイル」の欄で登録したフォルダ（全チャンネル共通。重複は1つに）"""
    out = []
    for f in str_list(app_settings().get("file_folders")):
        f = os.path.normpath(f.strip())
        if f not in out:
            out.append(f)
    return out


def collect_files(d, base, out):
    try:
        entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
    except OSError:
        return
    for e in entries:
        if len(out) >= FOLDER_MAX:
            return
        if e.name.startswith("."):
            continue
        if e.is_dir():
            collect_files(e.path, base, out)
        elif e.is_file() and e.name.lower().endswith(FOLDER_EXT):
            out.append({"label": os.path.relpath(e.path, base).replace("\\", "/"), "path": e.path})


def folder_options():
    """登録フォルダごとに、中のファイル（.md .txt、下のフォルダも）を返す"""
    res = []
    for f in file_folders():
        files = []
        if os.path.isdir(f):
            collect_files(f, f, files)
        res.append({"folder": f, "name": os.path.basename(f) or f, "ok": os.path.isdir(f),
                    "files": files, "more": len(files) >= FOLDER_MAX})
    return res


def folder_file_resolve(p):
    """選んだファイルの実パスと、表示用の名前（登録フォルダ名/フォルダ内の場所）。登録フォルダの外は読まない"""
    if not isinstance(p, str) or not p.strip():
        raise ValueError("ファイルの指定が空です")
    full = os.path.normpath(p.strip())
    for f in file_folders():
        try:
            if os.path.commonpath((f, full)) == f and full != f:
                return full, (os.path.basename(f) or f) + "/" + os.path.relpath(full, f).replace("\\", "/")
        except ValueError:  # ドライブが違う
            continue
    raise ValueError(f"登録したフォルダの外のファイルです: {p}")


def file_key(path):
    """設定の差分を見るときの名前。選ぶ順番や数が変わっても、同じファイルは同じ名前になるように"""
    return "file_" + hashlib.sha1(os.path.normcase(path).encode("utf-8")).hexdigest()[:10] + ".md"


def read_text(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read().strip()


def settings_parts(st, agents=None, files=None):
    """設定の各パーツを (ラベル, ファイル名, 内容) のリストで返す。
    agents: 担当の会話が読んでいる AGENTS.md の場所。そこに書いた System と追加のルールはここに出さない
    files: dict を渡すと、ファイルから読んだパーツの {ファイル名: 実際のパス} を入れる"""
    parts, missing = [], []
    files = {} if files is None else files
    if agents:
        if st.get("system_file"):
            parts.append((f"System（{st['system_file']}）", "system.md",
                          f"{agents} にルールとして書いてある（Antigravity が毎ターン読み込んでいる）。ここには出さない"))
    else:
        if st.get("system_file"):
            try:
                files["system.md"] = system_file_path(st["system_file"])
                parts.append((f"System（{st['system_file']}）", "system.md", read_text(files["system.md"])))
            except (OSError, ValueError):
                missing.append(st["system_file"])
        if st.get("agents_md") and (st.get("agents_extra") or "").strip() and not st.get("rules_every"):
            # AGENTS.md を読まない会話が担当のときは、ルールに書いた分もここで見せる（毎回最後に付けるなら、そちらで見せる）
            parts.append(("追加のルール", "agents_extra.md", st["agents_extra"].strip()))
    for i, rel in enumerate(st.get("ref_files") or []):
        try:
            files[f"ref_{i}.md"] = system_file_path(rel)
            parts.append((f"参照ファイル（{rel}）", f"ref_{i}.md", read_text(files[f"ref_{i}.md"])))
        except (OSError, ValueError):
            missing.append(rel)
    for p in str_list(st.get("folder_files")):
        try:
            path, label = folder_file_resolve(p)
            name = file_key(path)
            parts.append((f"読み込むファイル（{label}）", name, read_text(path)))
            files[name] = path
        except (OSError, ValueError):
            missing.append(p)
    if (st.get("files_extra") or "").strip():
        parts.append(("追加設定", "extra.md", st["files_extra"].strip()))
    for title, key, name in (("チャンネル概要", "summary", "summary.md"),
                              ("指示", "instructions", "instructions.md"),
                              ("舞台・状況", "scenario", "scenario.md")):
        if (st.get(key) or "").strip():
            parts.append((title, name, st[key].strip()))
    ch = st.get("character") or {}
    chara = "\n".join(f"{k}: {v}" for k, v in [
        ("名前", ch.get("name")), ("年齢", ch.get("age")),
        ("口調", ch.get("speech")), ("設定", ch.get("profile"))] if v)
    if chara:
        parts.append(("キャラクター", "chara.md", chara))
    if (st.get("user_name") or "").strip():
        parts.append(("ユーザーの呼び名", "username.md", st["user_name"].strip()))
    rag = rag_context(st)
    if rag:
        parts.append(("参照知識（RAG）", "rag.md", rag))

    el = st.get("elapsed_time") or {"day": 1, "period": "午前"}
    el_text = f"作中時間: {el.get('day', 1)}日目・{el.get('period', '午前')}"
    parts.append(("作中経過時間", "elapsed_time", el_text))

    if os.path.isfile(WRITING_STYLE):
        try:
            parts.append(("執筆スタイル", "writing_style.md", read_text(WRITING_STYLE)))
            files["writing_style.md"] = WRITING_STYLE
        except OSError:
            pass
    parts.append(("応答の書き方（outbox の JSON）", "output.md", output_text(st)))
    return parts, missing


# 毎ターン必ず送る設定。チャンネルの指示とキャラは IDE 会話の文脈が圧縮されても失われないよう常に再送する
ALWAYS_SHOW_KEYS = {"elapsed_time", "output.md", "chars", "instructions.md", "chara.md"}
HOLD_KEYWORDS = ("脱衣", "下着", "挿入", "絶頂", "汗だく", "戦闘", "全裸", "結合", "射精", "潮吹き", "イク", "オーガズム", "喘ぎ")


def is_in_action_hold(msgs, idx, sid=None):
    st_list = state_before(msgs, idx, sid=sid)
    for item in st_list:
        if isinstance(item, dict):
            for sit in item.get("situation") or []:
                if isinstance(sit, str) and any(k in sit for k in HOLD_KEYWORDS):
                    return True
            if item.get("inner") and any(k in item["inner"] for k in ("絶頂", "挿入", "結合", "射精", "全裸")):
                return True
    return False


def build_expanded_rag_query(s, m, idx):
    parts = []
    if m.get("text"):
        parts.append(m["text"].strip())
    msgs = s.get("messages", [])
    prev_asst = next((x for x in reversed(msgs[:idx]) if x.get("role") == "assistant"), None)
    if prev_asst:
        if prev_asst.get("memory_query"):
            parts.append(prev_asst["memory_query"].strip())
        states = prev_asst.get("state")
        if not states and s.get("id") and prev_asst.get("id"):
            states = load_states_data(s["id"], readonly=True).get(prev_asst["id"])
        states = states or []
        for st_item in states:
            if isinstance(st_item, dict):
                if st_item.get("inner"):
                    parts.append(st_item["inner"].strip())
                for sit in st_item.get("situation") or []:
                    if isinstance(sit, str) and sit.strip():
                        parts.append(sit.strip())
        if prev_asst.get("text"):
            parts.append(prev_asst["text"].strip()[:80])
    combined = " ".join(parts).strip()
    if len(combined) > 180:
        combined = combined[:180]
    return combined


def output_text(st, is_diff=False):
    ic = st.get("image") or {}
    mode = st.get("image_mode", "always")
    inline_choices = bool(st.get("suggest_choices", True) and not has_parallel_choices_ide(st))
    schema = {
        "session": "<session>",
        "reply_to": "<reply_to>",
        "text": "ロールプレイ本文（キャラの一人称視点）",
        "memo": f"（{MEMO_REMIND_TURNS}ターンごと、または大きな場面転換時のみ更新する全文）",
        "elapsed_time": {
            "day": 2,
            "period": "夕方"
        },
        "memories": [
            {
                "category": "event",
                "content": "放課後の空き教室で初めてキスを交わし、秘密の関係になった"
            }
        ],
        "resolved_ids": ["mem_00112233"],
        "memory_query": "夏祭り 浴衣 神社 雨宿り 水族館"
    }
    if inline_choices:
        schema["choices"] = [{"label": "行動・発言の要約", "text": "「短いセリフ」＋具体的なアクション"}]
    if mode != "off":
        schema["image"] = {"characters": ["キャラ名"]}
        schema["characters"] = [{"name": "キャラ名", "tags": "外見タグ"}]
    if st.get("rp_state"):
        schema["state"] = [{
            "name": "キャラ名",
            "outfit": "衣装＆露出（例: 制服、全裸）",
            "outerwear": "上着＆露出（例: カーディガン、なし）",
            "underwear": "下着＆露出（例: 白ショーツ、ノーパン）",
            "equipped_items": ["装具（例: バイブ。なければ []）"],
            "action": ["行為・接触タグ（例: anal fingering, hand on another's ass, anus。挿入は anal penetration / vaginal penetration。手の位置も書く）"],
            "body_fluids": "痕跡（例: 汗、紅潮、涙、精液、なし）",
            "pose": ["体勢タグ（例: lying, on back, spread legs）"],
            "expression": ["表情タグ（例: ahegao, teary eyes, open mouth, tongue out）"],
            "background": ["背景タグを必ず3個（例: bathroom, indoors, steamy）"],
            "camera": ["構図タグを3個（例: pov, from behind, three-quarter view）"],
            "penis_present": False,
            "situation": ["状況・姿勢・体調の箇条書き"],
            "inner": "内心"
        }]
    sample_json = json.dumps(schema, ensure_ascii=False, indent=2)

    if is_diff:
        for k in ("memo", "elapsed_time", "characters",
                  "memories", "resolved_ids", "memory_query"):
            schema.pop(k, None)
        if st.get("rp_state"):
            schema["state"] = [{
                "name": "キャラ名",
                "pose": "今の体勢（毎回）",
                "expression": ["今の表情（毎回・1〜3個）"],
                "situation": ["今の状況・姿勢・体調など箇条書き"],
                "inner": "今の内心"
            }]
        sample_json = json.dumps(schema, ensure_ascii=False, indent=2)
        lines = [
            "応答は wait_input.py が表示した outbox に UTF-8 JSON で書く（session, reply_to はそのまま入れる）。",
            "```json",
            sample_json,
            "```",
            "- text: ロールプレイの本文。応答の主体となるキャラクター本人の一人称視点で、そのキャラのセリフ・動作・知覚・情景を記述する。地の文は客観描写にせず、そのキャラの脳内にダダ漏れしている肉声（心の声・ツッコミ・ひとり実況）で書く（視点キャラは1人に固定。他キャラとユーザーは三人称で描写し、ユーザーの内心や、そのキャラが知覚できない出来事は書かない）。メタ説明はしない。",
            "- text の長さ: 通常は300字以内。見せ場・行為中でも最大600字を目安にし、それを超えない。セリフ（「」）は1回につき2〜3文まで（チャンネルの「指示」に長さの指定があればそちらを優先）。",
            "- 任意キー（変化・該当がない通常ターンはキー自体を省略すること）:",
            f"  - memo: {MEMO_REMIND_TURNS}ターンごとの定期更新指示時、または大きな場面転換時のみ「これまでの主な出来事＋現在の状況」の全文を出力（それ以外は省略）。",
            "  - elapsed_time: 時間帯が進んだ時のみ {\"day\": 日数, \"period\": \"早朝/午前/昼/夕方/夜/深夜\"} を追加（行為中ホールド時は時間を維持し省略）。",
            "  - memories: 忘れてはならない重要情報が新たに発生した時のみ [{\"category\": \"calling/rule/taboo/rule_taboo/item/status/item_status/promise/char_fact/user_fact/relationship/event\", \"content\": \"キャラ名やユーザー名を主語にした三人称完全自立文\"}] を追加（一時的な下着の細部等は含めず state で管理）。",
            "  - resolved_ids: 達成した約束、消費/返却/消失したアイテム・痕跡、解除されたルールの記憶ID配列 [\"mem_...\"]（該当がある時のみ）。",
            "  - memory_query: 次回想起用キーワード文字列（必要な時のみ）。",
        ]
        if inline_choices:
            lines.append("- choices: プレイヤーが次に取れる自然な返答候補8個（label: 10文字以内の要約, text: 「短いセリフ」＋具体的な行動）。")
        if st.get("rp_state"):
            lines.append(
                "- state: pose と expression は毎回必ず書く（変化がなくても）。background と camera は変わった時だけ視点キャラに書く（省略時は維持）。衣装・装具・行為・体液（outfit, outerwear, underwear, equipped_items, action, body_fluids）は変化した項目のみキーを追加（省略は維持）。pose / expression / background / camera / action は Danbooru 英語タグで書く（例: pose=[\"lying\",\"on back\",\"spread legs\"], expression=[\"ahegao\",\"teary eyes\",\"open mouth\"], background=[\"bathroom\",\"indoors\",\"steamy\"], camera=[\"pov\",\"from behind\",\"three-quarter view\"], action=[\"anal fingering\",\"hand on another's ass\",\"anus\"]）。背景は必ず3個、構図も3個。penis_present は画面内に男性器が写る時だけ true。"
                "【重要】終了・解除した状態（挿入終了・脱衣・液体拭取・装具解除）は必ず \"なし\"（equipped_items は []）で明示的に上書き。新規登場キャラは全項目を書く。"
            )
        if mode == "off":
            lines.append("- image と characters は付けない。")
        else:
            lines.append("- image: image.characters だけ書く。image.prompt はサーバーが state から生成するので書かない。")
            lines.append("- image.characters: この画像に写る固定キャラの name 配列（固定キャラ一覧と同じ表記。毎回省略せず必ず指定すること）。")
            lines.append("- characters: 画像に描く人物が初登場した時、または髪型等の不変外見が変化した時のみ [{\"name\": \"キャラ名\", \"tags\": \"不変外見タグ\"}] を追加（登録済みで変化がなければ省略）。")
        return "\n".join(lines)

    lines = ["応答は wait_input.py が表示した outbox の場所に、UTF-8 の JSON ファイルで書く"
             "（ファイル名は <session>_<任意>.json。session と reply_to は表示された値をそのまま入れる）。",
             "```json",
             sample_json,
             "```",
             "- text: ロールプレイの本文。ユーザーへの返事そのもの。応答の主体となるキャラクター本人の一人称視点で、そのキャラのセリフ・動作・知覚・情景を記述する。地の文は客観描写にせず、そのキャラの脳内にダダ漏れしている肉声（心の声・ツッコミ・ひとり実況）で書く（視点キャラは1人に固定。他キャラとユーザーは三人称で描写し、ユーザーの内心や、そのキャラが知覚できない出来事は書かない）。メタな説明やファイル操作の話はしない。",
             "- text の長さ: 通常は300字以内。見せ場・行為中でも最大600字を目安にし、それを超えない。セリフ（「」）は1回につき2〜3文まで（チャンネルの「指示」に長さの指定があればそちらを優先）。",
             f"- memo: {MEMO_REMIND_TURNS}ターンごとの定期更新指示時、またはローテーション時・大きな場面転換時に「これまでの主な出来事＋現在の状況」を整理した全文を出力する。指示のない通常ターンはキーを省略してよい（空文字は書かない）。",
             "- elapsed_time: 作中の時間経過に合わせて更新する場合に出力（day: 1から始まる作中日数、period: 早朝/午前/昼/夕方/夜/深夜）。"
             "変化がなければ省略する。※行為中や緊迫シーン（In-Action Hold）では就寝や翌朝を迎えるまで時間を維持すること。",
             "- memories: 今後の会話で忘れてはならない重要情報があれば抽出して出力する（なければ空配列 [] または省略）。"
             "カテゴリ: calling（呼称・口調）, rule（ルール・規則・校則）, taboo（タブー・禁忌・地雷）, item（アイテム・所持品）, status（状態・痕跡）, item_status（アイテム・装着具・持続的痕跡）, promise（約束・予定）, char_fact（キャラの秘密）, user_fact（ユーザーの好み・秘密）, relationship（関係性の変化）, event（重要出来事）。"
             "※必ずキャラ名またはユーザー名を主語にした三人称完全自立文で誰が何をどうしたのか記述すること（❌主語抜け曖昧文は禁止）。"
             "- resolved_ids: 達成・完了した約束の記憶IDがあれば指定（なければ空配列 [] または省略）。",
             "- memory_query: 次回の会話で想起させたいキーワードがあれば出力する（任意）。"]
    if inline_choices:
        lines.append("- choices: プレイヤーが次に取れる自然な返答候補8個（label: 10文字以内の要約, text: 「短いセリフ」＋具体的な行動）。")
    if st.get("rp_state"):
        lines += [
            "- state: 場面に出ているキャラの、この応答を書き終えた時点の状態。毎回入れる。"
            "入力の「キャラの状態」および今回のユーザー発言での指示・変化をもとに、この応答で変わったところを直して書く。",
            "  - outfit: 現在の衣装＆露出状態（例: 「セーラー服」「ワンピース」「全裸」や、めくれ・はだけ等の露出状況）。",
            "  - outerwear: 上着＆露出状態（例: 「ブレザー脱衣」「おっぱいが露出」「なし」など）。",
            "  - underwear: 現在の下着＆露出状態（例: 「白レースショーツ」「丸見えの股間」「捲れあがったスカート」「ノーパン」など）。",
            "  - equipped_items: 現在装着・挿入中の装具＆大人のおもちゃの配列（例: [\"バイブ\", \"首輪\"]。何も付けていなければ空配列 [] または [\"なし\"]）。",
            "  - action: 行為・接触を Danbooru タグの配列で書く（例: [\"anal fingering\", \"hand on another's ass\", \"anus\"] / [\"vaginal penetration\", \"penis\", \"hand on another's hip\"] / [\"breast grab\", \"hand on another's breast\"]）。挿入は anal penetration / vaginal penetration。手の位置・指・口・胸などの接触も書く。",
            "  - body_fluids: 身体痕跡・付着物（例: 「大量の汗」「紅潮」「愛液垂れ」「涙」「顔に精液付着」「なし」など）。",
            "  - pose: 体勢を Danbooru タグの配列で書く（例: [\"lying\", \"on back\", \"spread legs\"] / [\"all fours\"] / [\"standing\"]）。",
            "  - expression: 表情を Danbooru タグの配列で1〜3個（例: [\"ahegao\", \"teary eyes\", \"open mouth\", \"tongue out\"] / [\"blush\", \"teary eyes\", \"heart-shaped pupils\"]）。",
            "  - background: 背景を Danbooru タグの配列で必ず3個（例: [\"bathroom\", \"indoors\", \"steamy\"] / [\"bedroom\", \"bed\", \"night\"] / [\"ruined building\", \"indoors\", \"dim lighting\"]）。",
            "  - camera: 構図を Danbooru タグの配列で3個（例: [\"pov\", \"from behind\", \"ass focus\"] / [\"three-quarter view\", \"close-up\", \"from above\"]）。",
            "  - penis_present: 画面内に男性器が写るか（true/false）。台詞・地の文に男性器の単語が出ただけでは true にしない。実際に画面内に存在する時だけ true。",
            "  - situation: 姿勢・体調・居場所・カラダの状態（紅潮・快楽反応など）を短い文の配列（箇条書き）で。",
            "  - inner: そのキャラの今の内心・本音。",
            "  - 何も変わらなかったキャラは入れなくてよい（前の状態が残る）。ただし場面に出ているキャラは、pose / expression を毎回書く必要があるため基本的に毎回入れる。退場して記録が要らなくなったキャラは残す"
            ' {"name": "キャラ名", "remove": true}。ユーザーが演じる人物は入れない。',
        ]
    if mode == "off":
        lines.append("- image と characters は付けない（このチャンネルは画像を描かない）。")
    else:
        lines += [
            "- image: image.characters だけ書く。画像プロンプト（image.prompt）はサーバーが state から自動生成するので書かない（タグを書いても無視される）。",
            "- image.characters: この画像に写る固定キャラの name の配列（固定キャラ一覧か、今回の characters と同じ表記）。"
            "固定キャラが写らなければ空配列。",
            "- characters: 画像に初登場時、または不変の外見が変わった時だけ {name, tags} を入れる。tags は髪色・髪型・目の色・体型・肌色・種族特徴など不変の外見タグのみ（服装・表情・ポーズ・人数は禁止）。変化がなければ入れない。固定キャラ一覧と同表記。",
            f"- 画像の大きさは通常 {ic.get('width')}x{ic.get('height')}。変えたいときだけ image に width / height を入れる。",
        ]
    return "\n".join(lines)


def settings_block(st):
    parts, missing = settings_parts(st)
    return "\n\n".join(f"## {lb}\n{ct}" for lb, _, ct in parts), missing


def sig_of(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def history_text(msgs, user_name, limit=None):
    lines = []
    for m in msgs:
        who = (user_name or "ユーザー") if m["role"] == "user" else "AI"
        line = f"[{who}]\n{m.get('text', '')}"
        for f in m.get("files") or []:
            line += f"\n（添付: {f['name']} … {upload_abs(f['path'])}）"
        lines.append(line)
    out, total = [], 0
    for line in reversed(lines):
        total += len(line)
        if total > (limit or HISTORY_CHARS):
            out.append("（これより前の会話は省略。AIメモを参照）")
            break
        out.append(line)
    return "\n\n".join(reversed(out))


def last_image_prompt(msgs, idx, sid=None):
    """msgs[idx] より前で最後に出力された画像プロンプトと対象キャラを返す"""
    if not sid and msgs:
        for x in reversed(msgs):
            if x and x.get("id") and x["id"] in msg_to_session:
                sid = msg_to_session[x["id"]]
                break
    idata = None
    for m in reversed(msgs[:idx]):
        im = m.get("image") or {}
        if im.get("prompt"):
            return im["prompt"], im.get("chars") or []
        if sid:
            if idata is None:
                idata = load_images_data(sid, readonly=True)
            mid = m.get("id")
            if mid and mid in idata:
                img_meta = idata[mid]
                if img_meta.get("prompt"):
                    return img_meta["prompt"], img_meta.get("chars") or []
    return "", []


def image_prompt_guidelines_text(for_refine=False, brief=False):
    """画像プロンプト生成および推敲時における、性器・結合部・行為・エロタグの共通判定規則"""
    if brief and not for_refine:
        return (
            "【画像プロンプト（image.prompt）作成の必須要点（初回規則の遵守）】\n"
            "1. 状態同期: 最新 state（outfit, outerwear, underwear, equipped_items, penetration, body_fluids, pose, expression, place, camera）と今回の変化を英語タグ化し、脱衣・体勢変化時は前ターンの古い着衣・体勢タグを削除。\n"
            "2. 解剖要素の先頭明記: 口淫・性交・露出時は penis, fellatio, deepthroat, vaginal/anal penetration, pussy, anus, erect nipples 等を人数・pov の直後（第3〜5タグ）に必ず置く。非性交時（ハグ・キス等）は penis, fellatio, 1boy を追加しない。\n"
            "3. 射精前後の厳密区別: 射精前は cum 不可（love juice, sweat）。射精後のみ cum on face / cum in mouth / creampie / anal creampie 等を1〜2個。大量時は cum overflow。\n"
            "4. 禁止・重複排除: off-screen male と曖昧タグ（submissive, dazed, cheeks puffed 等）は禁止。fellatio/oral、blush/heavy blush、creampie/cum inside 等は強い方1つ。\n"
            "5. タグ順（25〜40個）: 人数・視点 → 構図 → 行為・性器・結合部 → 体勢 → 衣装・露出 → 精液・体液 → 表情 → 場所（最大2個）。\n"
            "6. ポーズ: ユーザー指定の体勢は実在タグで (3) の位置に（I字バランス→standing split, leg up / 四つん這い→all fours / 逆立ち→handstand）。造語・日本語タグ禁止。古いポーズタグは削除。\n"
            "7. 物・装具・排泄: アナルゼリー→lubricant, anal object insertion / 浣腸→enema / バイブ→vibrator / 縄→bondage / 目隠し→blindfold / 手錠→handcuffs / 放尿→peeing, urine / 脱糞→feces, pooping。equipped_items は必ずタグ化。\n"
            "8. 前回プロンプトの丸写し禁止: 新しい要素は追加、無くなった要素（脱いだ服・抜いた装具・終わった行為）は削除。\n"
            "9. state の pose / expression / place / camera も実在タグ化（pose→standing split / expression→blush / place→bathroom / camera→from behind）。"
        )
    target_action = "推敲にあたり、まず" if for_refine else "image.prompt の作成にあたり、現在の状態（state の outfit, outerwear, underwear, equipped_items, penetration, body_fluids）および"
    return (
        f"【最優先プロセス：構図および解剖学的整合性の判定と不要タグの排除】\n"
        f"{target_action}直前の会話ログ・状況から「画面内に何が物理的に配置・接触しているか（身体部位・相互作用・行為）」を最優先で判断し、プロンプトの中核としてチョイスすること:\n\n"
        "■ 【作画品質・解剖学的整合性の最重要要件：被写体・接触対象タグの完全記述】\n"
        "- 画像生成モデル（CLIP/Diffusion）の作画特性上、相互作用（口淫、性交、愛撫、露出等）が存在するシーンで物理的対象（penis, fellatio, pussy, anus 等）が指定されない場合、空間把握の失敗による四肢・顔面の作画崩壊や被写体欠落（何もない空間を見つめる・浮遊した手等）が必然的に発生します。\n"
        "- したがって、sweat, saliva, heavy blush 等の副次的な体液・表情タグや、head grab, hands on user thighs, cheeks puffed, submissive などの曖昧タグだけで構成することは避け、物理的に相互作用している主要被写体・解剖要素タグ（penis, fellatio, pussy, anus 等）を、プロンプトの最優先（人数・構図タグの直後、第3〜第5タグ以内）に必ず明記してください。\n\n"
        "■ 【構図破綻・作画希釈を防ぐための除外・圧縮タグ一覧】\n"
        "- off-screen male の完全排除: 相手との接触・口淫・性交シーンでは男性器や手・体の一部が画面内に描画されるため、off-screen male を指定すると被写体が描画対象から消滅し構図崩壊します。主観構図には必ず pov を使用してください。\n"
        "- 不要な抽象感情・状況タグの排除: submissive, dazed, satisfied, breathless, cheeks puffed, faint smile, narrow space など、画像クオリティに寄与せずトークン枠を浪費・混乱させる曖昧タグは出力しないでください。\n"
        "- 過剰な背景・環境タグの圧縮: dawn, morning light, sunbeam, soft focus などの背景演出タグを大量に並べることは避け、背景は indoors, dim lighting などの基本タグ最大2個までに限定し、浮いたトークン枠を人物・身体・接触描写に集中させてください。\n\n"
        "1. 【男性器（penis）の有無の最優先判定】\n"
        "   - 有り（描くべき場合）:\n"
        "     - 口淫（フェラ、イラマチオ等）: penis, fellatio, deepthroat を最優先必須付与（oral は fellatio と重複するため不要）。\n"
        "     - 奉仕直後・お掃除後: 直前に口淫やお掃除奉仕が行われ、相手の足元で跪いて見上げている構図（pov, looking up, kneeling, under table 等）では、相手の男性器が画面手前に映っている構図として penis を付与し、口元の唾液・精液（drooling, cum in mouth 等）と組み合わせる。\n"
        "     - 挿入・本番（正常位、後背位、騎乗位等）: 結合部が映る構図では vaginal penetration または anal penetration, penis を必須適用（広義の penetration との重複を避ける）。\n"
        "     - 手コキ・パイズリ等: handjob, paizuri, penis を付与。\n"
        "   - 無し（描かない場合）:\n"
        "     - 日常会話、着衣、ソロ、または直接の男性器接触を伴わない非性交シーン（抱擁・ハグ、キス、接触のない愛撫等）では、penis, fellatio, 1boy を絶対に追加してはならず、元プロンプトに誤って含まれている場合は確実に削除すること（意図しない男性器・口淫タグ混入を徹底防止）。\n\n"
        "2. 【女性器（pussy）の視認性・露出状態の最優先判定】\n"
        "   - 視認・露出あり（描くべき場合）:\n"
        "     - 脚を開いている（spread legs）、パンツをずらしている（panties aside）、全裸（completely naked）、指入れ（fingering）、クンニ、挿入中など、女性器が露出または行為対象となっているシーンでは、pussy, spread pussy, pussy juice などを状況に応じて的確に付与（hyper detailed や exposed は不要）。\n"
        "   - 視認なし（描かない場合）:\n"
        "     - 着衣状態、バストアップや顔のアップ（close-up, upper body）、手やシーツで局所が隠れている構図では、不自然な pussy タグを混入させない。\n\n"
        "3. 【アナル・肛門（anus）の視認性・露出状態の最優先判定】\n"
        "   - 視認・露出あり（描くべき場合）:\n"
        "     - 後背位（doggystyle）、四つん這い（all fours）、バックアングル（from behind, ass focus）、開帳・M字開脚、アナル性交（anal penetration）、アナル弄り（anal fingering）、全裸で臀部・後方が映る構図など、肛門部が露出または行為対象となっているシーンでは anus を的確に付与（exposed anus 等の重複タグは避ける）。\n"
        "   - 視認なし（描かない場合）:\n"
        "     - 正面からのクローズアップ、前面のみが映るアングル、着衣、臀部が隠れている構図では、不自然な anus タグを含めない。\n\n"
        "4. 【乳首（nipples）の視認性・露出状態の最優先判定】\n"
        "   - 視認・露出あり（描くべき場合）:\n"
        "     - トップレス（topless）、全裸（completely naked）、胸チラ・はだけ（breast exposure, clothes pull, lifted shirt）、パイズリ（paizuri）、乳首責め・愛撫（nipple tweak, groping）、勃起乳首（erect nipples）など、乳首が露出または視認できるシーンでは、erect nipples または nipples を的確にチョイス（hyper detailed や hard nipples は不要）。\n"
        "   - 視認なし（描かない場合）:\n"
        "     - ブラや衣服を着ていて胸が露出していないシーン、または下半身アップ・顔アップで胸が画面外の構図では detailed nipples や nipples を混入させない。\n\n"
        "5. 【精液（cum）の有無と付着位置（射精状態の厳密判定）】\n"
        "   - 射精前（未射精・挿入中・奉仕中）:\n"
        "     - まだ射精に至っていない段階では cum タグは一切入れず、愛液（love juice）、汗（sweat）、唾液（saliva）で濡れ感を表現する。\n"
        "   - 射精後（中出し・ぶっかけ・口内射精・お掃除後）:\n"
        "     - 会話ログや状況で射精があった文脈の場合、射精箇所に応じた具体的な位置タグを1〜2個厳選して付与（同義・近縁タグの併記厳禁）:\n"
        "       - 顔・目元: cum on face（facial と重複させない）\n"
        "       - 口・唇・喉奥: cum in mouth または cum on lips\n"
        "       - 胸・谷間: cum on breasts\n"
        "       - 腹部・胴体: cum on stomach（cum on body と重複させない）\n"
        "       - 秘部・中出し: creampie または cum dripping from pussy（cum inside と重複させない）\n"
        "       - 尻・アナル: anal creampie または cum on ass\n"
        "       - 大量射精時: excessive cum\n\n"
        "6. 【表情・瞳・口元の詳細化（状況連動の感情・快楽反応）】\n"
        "   - 曖昧な感情タグを排し、具体的で効果の高い表情タグを1〜2個に厳選:\n"
        "     - 目元・瞳: heart-shaped pupils, rolling eyes, empty eyes, teary eyes, looking at viewer, looking away\n"
        "     - 口元・舌・唾液: open mouth, parted lips, tongue out, drooling\n"
        "     - 快感・陶酔: ahegao, heavy blush, trembling\n\n"
        "7. 【意味の重複・類語タグの徹底排除（Semantic Deduplication）】\n"
        "   - 行為・結合: fellatio と oral、vaginal penetration と penetration は具体的で強い方1つに絞る。\n"
        "   - 性器・部位: pussy と spread pussy / exposed pussy、anus と exposed anus、nipples と erect nipples / hard nipples のような重複・擬似タグ（hyper detailed等）を排除。\n"
        "   - 表情・紅潮: blush と heavy blush、intense blush と heavy blush、embarrassed と shy などは強い方1つに絞る。\n"
        "   - 体液・濡れ: wet と drenched、sweat と sweating、pussy juice と wet pussy、drooling と saliva trail 等は整理し単一の明確なタグにする。\n"
        "   - 精液表現: cum on face と facial、creampie と cum inside、cum on stomach と cum on body などは具体的な方1つに統一する。\n"
        "   - 服装・下着: panties と underwear のような親子関係タグは、具体的な panties（または下着種類）のみ残す。\n\n"
        "8. 【矛盾タグの解消】\n"
        "   - 服装矛盾: 当該ターンの state が下着や全裸の場合、既存タグの着衣タグ（school uniform, skirt 等）を確実に削除し、脱衣・下着タグへ差し替える。\n"
        "   - ポーズ・構図矛盾: standing と lying、closed eyes と looking at viewer、open mouth と parted lips などの競合は文脈に沿ってどちらか一方に統一する。\n\n"
        "9. 【タグ配置順序（CLIP最重要ソート順）】\n"
        "   - 全体で 25〜40個程度 に厳選し、以下の順序で並べる:\n"
        "     (1) 人数・視点（1girl, 1boy, pov）\n"
        "     (2) 構図・アングル（例: from behind, from above, close-up, full body。pov は人数の直後）\n"
        "     (3) 最重要 行為・性器・結合部（例: penis, fellatio, pussy, anus, erect nipples, vaginal penetration, fingering 等）★最優先で先頭に配置！\n"
        "     (4) ポーズ・体勢（例: kneeling, lying on back, spread legs, on all fours, looking up 等）\n"
        "     (5) 衣装状態・露出度（例: disheveled clothes, panties aside, completely naked 等）\n"
        "     (6) 精液・体液・肌状態（例: cum in mouth, creampie, cum on face, love juice, sweat, heavy blush 等）\n"
        "     (7) 表情・視線・瞳・口元（例: looking at viewer, ahegao, heart-shaped pupils, teary eyes, open mouth, drooling 等）\n"
        "     (8) 場所・背景・照明（最大3〜4個のみ。例: indoors, dim lighting, under table）\n\n"
        "10. 【ユーザー指定ポーズのタグ化（必須）】\n"
        "   - 直近のユーザー発言で指示されたポーズ・体勢は、本文で描写を省略しても必ず実在の Danbooru ポーズタグにしてプロンプトに含めること。造語タグ（i-balance, i-shaped balance 等）や日本語のままのポーズ表現は画像モデルに伝わらないため使用禁止。\n"
        "   - 変換例: I字バランス→standing split, leg up / M字開脚→spread legs, knees up, sitting / V字バランス→sitting, legs up / 四つん這い→all fours / 逆立ち→handstand / うつ伏せ→lying, on stomach / 仰向け→lying, on back / 正座→seiza / 片足立ち→standing on one leg / お姫様抱っこ→princess carry / 手を繋ぐ→holding hands\n"
        "   - ポーズタグは (3) の位置（行為・性器タグの直後、衣装より前）に置き、既存のポーズタグ（standing と lying 等）と矛盾する場合は古い方を必ず削除すること。\n\n"
        "11. 【小道具・装具・排泄のタグ化（必須）】\n"
        "   - state の equipped_items / body_fluids と、ユーザー発言・本文に出てきた物・装具・体液・排泄は、本文で描写を省略しても必ず実在の Danbooru タグにして含めること。\n"
        "   - 変換例: アナルゼリー/アナルジェル→lubricant, anal, anal object insertion / ローション→lubricant / 浣腸・浣腸器→enema, anal object insertion / アナルビーズ→anal beads / アナルプラグ→butt plug / バイブ・電マ→vibrator / 縄・緊縛→bondage, rope / 目隠し→blindfold / 手錠→handcuffs / 口枷→gag / コンドーム→condom / 放尿・おしっこ→peeing, urine / うんち・脱糞→feces, pooping\n"
        "   - 【重要】lubricant は「潤滑液・液体」のタグで、ゼリーや浣腸器そのものは描かれない。ジェル状のものを注入・排泄する場面では anal object insertion を、浣腸器・チューブを見せる場面では enema を必ず併記すること。\n"
        "   - 造語タグ（anal jelly, anal gel, lube 等）や日本語のままのタグは画像モデルに伝わらないため禁止。既に近いタグがある場合は重複させず実在タグに置き換えること。\n"
        "   - 前のターンにあった物・行為でも、もう終わった・抜いた・出し終えたものはタグを削除し、新しい状態（after anal, empty 等ではなく単にタグを消す）に合わせること。\n\n"
        "12. 【state の pose / expression / place / camera のタグ化（必須）】\n"
        "   - state の体勢・表情・場所・構図も必ず実在の Danbooru タグにして含めること。\n"
        "   - 変換例: pose: 四つん這い→all fours / I字バランス→standing split, leg up / 仰向け→lying, on back\n"
        "   - expression: 羞恥→blush / 涙目→teary eyes / 笑顔→smile / 恍惚→ahegao\n"
        "   - place: バスルーム→bathroom / 寝室→bedroom / 教室→classroom\n"
        "   - camera: 背面→from behind / 俯瞰→from above / 接写→close-up\n"
        "   - 造語タグや自由文・日本語のままタグにしないこと。"
    )


def upload_abs(rel):
    return os.path.normpath(os.path.join(UPLOADS, rel))


def save_upload(sid, name, data):
    """添付を data/uploads/<sid>/ に置いて、発言に付ける形（{name, path, type, size}）で返す"""
    name = os.path.basename(str(name or "").replace("\\", "/")).strip() or "file"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[-120:]
    d = os.path.join(UPLOADS, sid)
    os.makedirs(d, exist_ok=True)
    fn = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6] + "_" + name
    with open(os.path.join(d, fn), "wb") as f:
        f.write(data)
    return {"name": name, "path": f"{sid}/{fn}", "type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "size": len(data)}


def clean_files(sid, files):
    """送信で付けてきた添付が、このチャンネルに上げたファイルかを確かめて揃える"""
    out = []
    for f in files if isinstance(files, list) else []:
        rel = f.get("path") if isinstance(f, dict) else None
        if not isinstance(rel, str) or "/" not in rel or rel.split("/", 1)[0] != sid:
            raise ValueError("bad attachment")
        full = upload_abs(rel)
        if (os.path.normcase(os.path.dirname(os.path.abspath(full)))
                != os.path.normcase(os.path.abspath(os.path.join(UPLOADS, sid)))) or not os.path.isfile(full):
            raise ValueError("bad attachment")
        name = f.get("name") if isinstance(f.get("name"), str) and f["name"].strip() else rel.split("_", 3)[-1]
        out.append({"name": name.strip()[:120], "path": rel, "size": os.path.getsize(full),
                    "type": mimetypes.guess_type(full)[0] or "application/octet-stream"})
    return out


RECENT = 15  # light で全部見せるときに全文で見せる直近の発言数。それより前は AIメモ（あらすじ）で補う
CTX = os.path.join(DATA, "ctx")  # 長い会話の書き出し先（チャンネルごとに上書き。wait_input.py と同じ場所）
LONG_TEXT, LONG_LINES = 2500, 100  # これより長い会話は表示せずファイルに書いて読ませる（Antigravity が途中を省略するので）


def history_section(sid, name, label, text, split=True):
    """会話の全文を出す。長ければファイルに書いて場所を出す。戻り値: (セクション, 読ませるパス or None)"""
    if not split or (len(text) <= LONG_TEXT and text.count("\n") <= LONG_LINES):
        return {"label": label, "text": text}, None
    os.makedirs(CTX, exist_ok=True)
    path = os.path.join(CTX, f"{sid}_{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {label}\n\n{text}\n")
    return {"label": label,
            "text": f"ファイル: {path}\n（{text.count(chr(10)) + 1}行。このファイルを最初から最後まで読むこと）"}, path


def msg_sig(m):
    return [m["id"], sig_of(m.get("text", ""))]


def sent_path(sid):
    return os.path.join(SENT, sid + ".json")


def read_sent(sid):
    p = sent_path(sid)
    if not os.path.exists(p):
        sent_cache.pop(sid, None)
        return {}
    try:
        st = os.stat(p)
        cached = sent_cache.get(sid)
        if cached is not None and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
            return copy.deepcopy(cached["data"])
        d = read_json(p, {})
        sent_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": copy.deepcopy(d)}
        return copy.deepcopy(d)
    except Exception:
        sent_cache.pop(sid, None)
        return {}


def write_sent(sid, obj):
    os.makedirs(SENT, exist_ok=True)
    p = sent_path(sid)
    write_json(p, obj)
    try:
        st = os.stat(p)
        sent_cache[sid] = {"mtime": st.st_mtime, "size": st.st_size, "data": copy.deepcopy(obj)}
    except OSError:
        sent_cache.pop(sid, None)


def build_context(s, m, record=True):
    """wait_input.py が受け取った入力について、AIに見せる内容を組み立てる（そのまま表示する。ファイルには書かない）。
    担当IDEの会話は前に送ったものを覚えているので、担当が前回と同じで、そのとき送った会話が書き換わっていなければ
    増えた発言と変わった設定だけ送る（diff）。担当が変わった・初めて・過去が書き換わったときは全部送る（full）。
    チャンネルの input_mode が light なら、全部のときは直近15件を全文で（それより前は AIメモ。メモが空なら前の分も全文）、
    ファイルの設定と長い会話は場所を出して読ませる。続きのときは今回の発言（と変わった設定）だけ（省いた設定の一覧も出さない）。
    戻り値: {"mode", "reason", "sections": [{"label", "text"}], "same": [省いた設定のラベル], "files": [読ませるパス],
            "light": bool, "missing": [...], "rules": 毎回最後に付けるルール {"label", "text"} か None}"""
    st = s["setting"]
    if st.get("agents_md") and sync_agents(s):  # PC で System ファイルを書き換えていても、ここで AGENTS.md に反映する
        save(s)
    light = st.get("input_mode") == "light"
    paths = {}
    parts, missing = settings_parts(st, agents_in_use(s), paths)
    memo_val = (st.get("memo") or "").strip()
    parts.append(("AIメモ（これまでのあらすじ）", "memo", memo_val if memo_val else f"（これまでの出来事＋現在の状況を{MEMO_REMIND_TURNS}ターンごと又は大きな場面転換時に記録）"))
    if st.get("image_mode") != "off" and st.get("chars"):
        parts.append(("固定キャラ（画像に写るときこの外見タグが自動で付く）", "chars", chars_block(st["chars"])))
    msgs = s["messages"]
    mid = m.get("id") if isinstance(m, dict) else None
    idx = next((i for i, msg in enumerate(msgs) if msg.get("id") == mid), -1) if mid else -1
    if idx == -1:
        idx = msgs.index(m) if m in msgs else len(msgs) - 1
    ide = st.get("ide_session") or ""
    old = read_sent(s["id"])
    known = old.get("msgs") or []
    if not ide:
        reason = "担当のIDE会話が決まっていないチャンネル（受け取るAIが前回と同じとは限らない）"
    elif old.get("reset"):
        reason = old.get("why") or "UIで「次は全部送る」が押された"
    elif not known:
        reason = "このチャンネルをこの会話に送るのは初めて"
    elif old.get("ide") != ide:
        reason = "担当のIDE会話が切り替わった"
    elif len(known) > idx + 1 or [msg_sig(x) for x in msgs[:len(known)]] != known:
        reason = "前回送ったあとに会話が書き換えられた（編集・作り直し・巻き戻し）"
    else:
        reason = ""
    user = st.get("user_name")
    sections, same, files = [], [], []

    # ターン数と memo 更新タイミング（memo_due）を事前に計算する
    user_msgs_up_to_m = [x for x in msgs[:idx + 1] if x.get("role") == "user"]
    file_user_turns = len(user_msgs_up_to_m)
    rot_cnt = st.get("rotation_count", 0)
    total_turns_val = st.get("total_turns") or ((rot_cnt * ROTATION_PERIOD) + file_user_turns)
    last_memo_mid = st.get("last_memo_mid")
    last_memo_m = find_msg(s, last_memo_mid) if last_memo_mid else None
    if last_memo_m and last_memo_m in msgs[:idx]:
        memo_idx = msgs.index(last_memo_m)
        asst_after = [x for x in msgs[memo_idx + 1:idx] if x.get("role") == "assistant"]
    else:
        asst_after = [x for x in msgs[:idx] if x.get("role") == "assistant" and (not last_memo_m or x.get("ts", "") > last_memo_m.get("ts", ""))]
    memo_due = (file_user_turns >= ROTATION_TURNS) or (len(asst_after) >= MEMO_REMIND_TURNS)

    def section(lb, key, ct):
        if light and key in paths:  # ファイルの設定は中身を出さずに読ませる（長い出力は Antigravity が途中を省略するので）
            files.append(paths[key])
            return {"label": lb, "text": f"ファイル: {paths[key]}\n（このファイルを最初から最後まで読むこと）"}
        return {"label": lb, "text": ct}

    if reason:
        sections = [section(lb, key, ct) for lb, key, ct in parts]
        if idx:
            user_indices = [i for i, x in enumerate(msgs[:idx]) if x.get("role") == "user"]
            cut = user_indices[-ROTATION_KEEP_TURNS] if len(user_indices) > ROTATION_KEEP_TURNS else 0
            if cut:
                if (st.get("memo") or "").strip():
                    sections.append({"label": "これまでのあらすじ（下の直近ログより前の分を含む）",
                                     "text": "上の「AIメモ（これまでのあらすじ）」を参照。"})
                sec, path = history_section(s["id"], "recent", f"直近の会話（直近{idx - cut}件）",
                                            history_text(msgs[cut:idx], user, limit=10 ** 9), split=light)
                sections.append(sec)
                files += [path] if path else []
            else:
                sections.append({"label": f"これまでの会話（全{idx}件）", "text": history_text(msgs[:idx], user)})
    else:
        # In diff mode, replace output.md with compact version
        for i, (lb, k, ct) in enumerate(parts):
            if k == "output.md":
                parts[i] = (lb, k, output_text(st, is_diff=True))
                break

        sigs, labels = old.get("sigs") or {}, old.get("labels") or {}
        for lb, key, ct in parts:
            if key in ALWAYS_SHOW_KEYS or (key == "memo" and memo_due):
                sections.append(section(lb, key, ct))
            elif sigs.get(key) == sig_of(ct):
                same.append(lb)
            else:
                sections.append(section(lb + "（前回から変わった）", key, ct))
        if light:
            same = []
            gone = [labels.get(k, k) for k in sigs if k not in {key for _, key, _ in parts}]
            if gone:
                sections.append({"label": "使わなくなった設定", "text": "次の設定はもう使わない（前に読んだ内容は忘れる）:\n"
                                 + "\n".join(f"- {x}" for x in gone)})
        new = msgs[len(known):idx]  # 前回送ったあとに増えた、AIがまだ見ていない発言
        if new and light:
            sec, path = history_section(s["id"], "new", "前回のあとに増えた会話", history_text(new, user))
            sections.append(sec)
            files += [path] if path else []
        elif new:
            sections.append({"label": "前回のあとに増えた会話", "text": history_text(new, user)})

    # 経過時間リマインド（日常雑談で10ターン経過、行為中ホールド時は作動しない）
    in_action = is_in_action_hold(msgs, idx, sid=s.get("id"))
    turns_in_p = st.get("turns_in_period", 0)
    cur_period = (st.get("elapsed_time") or {}).get("period", "午前")
    if not in_action and turns_in_p >= 10:
        sections.append({
            "label": "【時間の経過】",
            "text": f"現在の時間帯（{cur_period}）で{turns_in_p}ターン経過しました。今回の応答で「あ、もう放課後だね」「そろそろ行こっか」など、自然な理由をつけて次の時間帯へ話を展開してください。"
        })

    # 世代交代の節目（armed のときだけ・同じターンで1回だけ）
    r = rotation_state(s)
    rot_armed = (r.get("phase") == "armed" and file_user_turns <= int(r.get("armed_turn") or 0))
    if rot_armed or (not r and file_user_turns >= ROTATION_TURNS and not rotation_skipping(s)):
        rot_no_next = int(st.get("rotation_count") or 0) + 2
        sections.append({
            "label": f"【最重要】セッション世代交代（通算{total_turns_val}ターン・要約圧縮）のターン",
            "text": (
                f"今回の応答はセッション世代交代の節目（第{rot_no_next}世代への移行・通算{total_turns_val}ターン目）です。\n"
                "返答本文（text）を先に書き、同じ応答の中で memo に "
                "これまでの全会話を総括した詳細なあらすじ・出来事史・現在の状況・関係性を全文出力してください。\n"
                f"次スレッドには直近{ROTATION_KEEP_TURNS}ターンの会話ログと長期記憶（RAG）もそのまま引き継がれるため、"
                "memo には主にそれより前の出来事を整理して書いてください。\n"
                "この応答で、会話ログの退避・新しい会話の作成・ファイル操作などを自分で行う必要はありません"
                "（アプリが応答の取り込み後に自動で行います）。"
            )
        })
    elif len(asst_after) >= MEMO_REMIND_TURNS:
        sections.append({
            "label": "【重要】あらすじ（memo）の定期更新ターン",
            "text": f"前回のあらすじ更新から{len(asst_after)}ターン経過しました。これまでの展開や現在の状況を整理するため、今回の応答では必ず memo にこれまでの主な出来事と現在の状況をまとめた全文を出力してください。"
        })
    if st.get("rp_state"):  # 全部・差分に関係なく毎回（AI が前のターンを覚えていなくても、手で直した分も届くように）
        sections.append({"label": "キャラの状態（前の応答のとき。これを踏まえて書き、応答の state で更新する）",
                         "text": state_block(state_before(msgs, idx, sid=s.get("id")))})
    if st.get("image_mode") != "off":
        prev_prompt, prev_chars = last_image_prompt(msgs, idx, sid=s.get("id"))
        if prev_prompt and not prev_chars:
            prev_chars = [c["name"] for c in resolve_image_chars(st, prompt_text=prev_prompt)]
        elif prev_chars:
            norm_chars = []
            for pc in (prev_chars if isinstance(prev_chars, list) else [prev_chars]):
                if isinstance(pc, dict) and pc.get("name"):
                    norm_chars.append(str(pc["name"]))
                elif isinstance(pc, str) and pc.strip():
                    norm_chars.append(pc.strip())
            prev_chars = norm_chars
        if prev_prompt:
            ch_info = f"（写っていたキャラ: {', '.join(prev_chars)}）\n" if prev_chars else ""
            sections.append({
                "label": "直前の画像プロンプト（前回の服装・カラダの状態・場面の参考）",
                "text": f"{ch_info}{prev_prompt}\n※前回のタグをそのまま貼らないこと。継続している服装・状態だけを残し、今回のユーザーの発言や展開で変化した部分（着替え・脱衣・着崩れ・汗や赤面などのカラダの状態・ポーズ・場所・装具・小道具・液体や排泄など）は必ず書き換え、新しく出た物・行為は実在の Danbooru タグで追加し、もう無い要素の古いタグは削除すること。"
            })
    if m.get("files"):
        paths = [upload_abs(f["path"]) for f in m["files"]]
        sections.append({"label": "ユーザーが今回の発言に添付したファイル（"
                         + ("画像はこの会話のチャット欄にも貼った。そこで見えていれば開き直さなくてよい" if m.get("pasted")
                            else "応答を書く前に開いて見る") + "）",
                         "text": "\n".join(f"ファイル: {p}（{f['name']}）" for p, f in zip(paths, m["files"]))})
        files += paths
    # 長期記憶（RAG）の拡張クエリによるハイブリッド検索とプロンプト注入
    current_turn = total_turns_val
    char_name = (st.get("character") or {}).get("name", "")
    shared = bool(st.get("shared_memories", False))
    try:
        expanded_q = build_expanded_rag_query(s, m, idx)
        matched_memories = memory_store.search_memories(
            s["id"],
            expanded_q,
            current_turn=current_turn,
            shared_memories=shared,
            character_name=char_name,
            limit=10
        )
        if matched_memories:
            prompt_text = memory_store.format_prompt_memories(
                matched_memories,
                current_turn,
                current_elapsed_time=st.get("elapsed_time")
            )
            sections.append({"label": "過去の記憶・約束（関連する記録）", "text": prompt_text})
    except Exception as e:
        print(f"[build_context] error searching memories: {e}")

    sections.append({"label": "ユーザーの発言（これに応答する）", "text": m["text"] or "（本文なし。添付だけ）"})

    if st.get("image_mode") != "off":
        sections.append({
            "label": "画像プロンプト（image.prompt）作成時の構図および解剖学的整合性規則" if reason else "画像プロンプト（image.prompt）作成の要点（解剖学的整合性規則）",
            "text": (
                image_prompt_guidelines_text(for_refine=False, brief=False) if reason
                else "画像プロンプト（image.prompt）はサーバーが state / pose / expression / place / camera / equipped_items / penetration から自動生成する。AIは image.prompt を書かず、image.characters だけ書くこと。"
            )
        })
    rules = None  # 入力のたびに表示の最後に付けるルール（全部・差分に関係なく毎回。長ければファイルに書いて場所を出す）
    if st.get("rules_every") and (st.get("agents_extra") or "").strip():
        rules, path = history_section(s["id"], "rules", "このチャンネルのルール（毎回必ず守る）", st["agents_extra"].strip())
        files += [path] if path else []
    # 応答スタイルの最終リマインド: full/ローテーション直後に指示が埋もれて「セリフだけ」になるのを防ぐ
    # （wait_input.py がこの rules をプロンプトのいちばん最後に表示するので、recency bias で必ず目に入る）
    _style_rule = ("text はキャラ本人の一人称視点で、セリフ（「」）だけでなく、動作・知覚・情景・心情・身体感覚・周囲の様子などの地の文も具体的に描写すること。"
                   "セリフのみで終わらせない。セリフ（「」）は1回につき2〜3文まで（チャンネルの「指示」に長さ指定があればそちらを優先）。")
    if rules:
        rules = {"label": rules.get("label") or "応答スタイル（毎回必ず守る）",
                 "text": f"{rules.get('text', '')}\n{_style_rule}"}
    else:
        rules = {"label": "応答スタイル（毎回必ず守る）", "text": _style_rule}
    sent = {
        "ide": ide,
        "choices_ide": old.get("choices_ide", ""),  # 選択肢セッションの送信履歴を消さない
        "sigs": {key: sig_of(ct) for _, key, ct in parts},
        "labels": {key: lb for lb, key, _ in parts},
        "msgs": [msg_sig(x) for x in msgs[:idx + 1]],
        "reset": bool(old.get("reset")),     # AI が実際に応答するまで「送った」ことにしない
        "why": old.get("why", ""),
        "ts": now(),
    }
    if record:
        write_sent(s["id"], sent)
    if os.environ.get("RPCHAT_PERF_LOG") == "1":
        try:
            perf = {
                "ts": now(),
                "sid": s.get("id"),
                "mode": "full" if reason else "diff",
                "reason": bool(reason),
                "sections": len(sections),
                "body_chars": sum(len(sec.get("text", "")) for sec in sections),
                "output_chars": next((len(sec.get("text", "")) for sec in sections if "応答の書き方" in sec.get("label", "")), 0),
                "image_rules_chars": next((len(sec.get("text", "")) for sec in sections if "画像プロンプト" in sec.get("label", "")), 0),
                "state_chars": next((len(sec.get("text", "")) for sec in sections if "キャラの状態" in sec.get("label", "")), 0),
                "prev_prompt_chars": next((len(sec.get("text", "")) for sec in sections if "直前の画像プロンプト" in sec.get("label", "")), 0),
            }
            os.makedirs(DATA, exist_ok=True)
            with open(os.path.join(DATA, "perf.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(perf, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[build_context] perf log error: {e}")
    ctx = {"mode": "full" if reason else "diff", "reason": reason, "sections": sections,
           "same": same, "files": files, "light": light, "missing": missing, "rules": rules,
           "title": st.get("title")}
    if not record:
        ctx["_sent"] = sent
    return ctx


CTX_DIR = os.path.join(DATA, "ctx")
LONG_CTX, LONG_CTX_LINES = 6500, 220   # wait_input.py と同じ値


def render_input_text(sid, m, ctx, task_type=None, ide="", note="", direct=False):
    """wait_input.py が表示する文面を組み立てる。表示と直送で同じ文字列を使う。
    direct=True: ファイルに逃がさず全部inline、末尾の指示を直送用に差し替える"""
    title = ctx.get("title") or sid
    out = ["=== NEW INPUT ==="]
    if direct:
        out.append("（このメッセージだけで応答する。wait_input.py は実行しない。"
                   "AGENTS.md や過去の手順より、この指示を優先する）")
    if note:
        out.append(f"!!! {note}")
    tag = task_type or ctx.get("mode") or "reply"
    tag = re.sub(r"[^a-zA-Z0-9_-]", "", str(tag)) or "reply"
    outbox = os.path.join(OUTBOX, f"{sid}_{tag}_{int(time.time())}.json")
    out += [f"session : {sid}", f"title   : {title}", f"reply_to: {m.get('id', '')}"]

    mode = ctx.get("mode")
    if mode == "full":
        out.append(f"内容    : 全部（{ctx['reason']}）。これまでに覚えているこのチャンネルの内容より、下の内容を正とする")
        if ctx.get("files"):
            out.append("「ファイル:」とある項目は、応答を書く前にそのファイルを全部読む")
    elif mode == "image_refine":
        out.append("内容    : 画像プロンプトのAI推敲・構図および状態整合性の最適化。意味重複の徹底排除、上限30〜40個、重要度ソート、時系列厳守。本文や会話応答は出力せず、推敲・整理したDanbooruプロンプトのJSON（type: image_refine）のみを outbox に出力する")
    elif mode == "memory_extract":
        out.append("内容    : 長期記憶の自動要約・抽出。本文や画像は出力せず、抽出した記憶のJSON（type: memory_extract）のみを outbox に出力する")
    elif mode == "consolidation":
        out.append("内容    : 世代交代の定期統合。本文や画像は出力せず、memo / elapsed_time / state のみを出力する")
    elif mode in ("choices_normal",) or (mode == "choices" and ctx.get("choices_type") != "erotic"):
        out.append("内容    : 返答候補（choices）の自動生成。本文や画像は出力せず、文脈に沿った自然な8つのchoicesのみを outbox に出力する")
    elif mode in ("choices_erotic", "choices") or ctx.get("choices_type") == "erotic":
        out.append("内容    : 返答候補（choices）のドスケベ再生成。本文や画像は出力せず、8つのchoicesのみを outbox に出力する")
    elif ctx.get("light"):
        out.append("内容    : 前回の続き。設定・会話はこの会話でもう見たものを使う。下は今回の発言（と、変わった設定があればその分）だけ")
        if ctx.get("files"):
            out.append("「ファイル:」とある項目は、応答を書く前にそのファイルを全部読む")
    else:
        out.append("内容    : 前回からの続き。設定と会話は前に表示したものをそのまま使い、増えた分・変わった分だけ下に出す")

    is_subtask = mode in ("choices", "choices_normal", "choices_erotic",
                          "image_refine", "memory_extract", "consolidation")
    if is_subtask:
        out.append("決まり  : 読むのはこの表示だけ。ほかのファイル・コマンド・API・プロセスは一切見ずに、指定された JSON のみを直ちに outbox に書くこと。")
    else:
        read_cmd = f'python "{os.path.join(ROOT, "read_past_messages.py")}" --sid {sid} --mid <確認したい記憶のmid> --window 5'
        out.append("決まり  : 読むのはこの表示と、指定されたファイルだけ。ほかのフォルダの一覧・ファイル・API・プロセスは見ない。\n"
                   "ただし、【過去の記憶・約束】にある出来事の当時の具体的なセリフや生々しいニュアンスを原文で確認したい時のみ、"
                   "以下のコマンドを実行して前後の生ログを参照してよい（要約だけで十分な場合は実行不要）：\n"
                   f"  {read_cmd}\n"
                   "分からないこと（演じるキャラが決まっていない等）は探さずに、応答の text でユーザーに短く質問する（このときだけメタな質問でよい）")
    out.append("思考    : 思考では、この表示の内容（設定・会話・ユーザーの発言）も、これから書く応答の本文も、復唱・書き写しをしない。"
               "触れる必要があるときは短い要約にとどめる。応答の本文は outbox の JSON に直接書く")
    for x in ctx.get("missing") or []:
        out.append(f"!!! 設定ファイルが読めなかった: {x}")

    body = "".join(f"\n--- {sec['label']} ---\n{sec['text']}\n" for sec in ctx.get("sections", []))
    if ctx.get("same"):
        body += "\n（前回と同じなので省略: " + "、".join(ctx["same"]) + "）\n"

    if not direct and not ctx.get("light") and (len(body) > LONG_CTX or body.count("\n") > LONG_CTX_LINES):
        os.makedirs(CTX_DIR, exist_ok=True)
        path = os.path.join(CTX_DIR, f"{sid}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title} / reply_to {m.get('id', '')}\n{body}")
        out.append(f"\n設定・会話・発言はファイルに書いた（{body.count(chr(10)) + 1}行）。"
                   "このコマンドの表示ではなく、必ずこのファイルを最初から最後まで読んでから応答する:")
        out.append(f"  {path}")
        out.append("（/api/context などを自分で呼んで集め直さないこと。呼ぶと送った扱いになり、次からは差分しか返らない）")
        for sec in ctx.get("sections", []):
            if sec.get("label", "").startswith("ユーザーが今回の発言に添付"):
                out.append(f"\n--- {sec['label']} ---\n{sec['text']}")
        last = ctx.get("sections")[-1] if ctx.get("sections") else None
        if last and len(last.get("text", "")) <= 1500:
            out.append(f"\n--- {last['label']} ---\n{last['text']}")
    else:
        out.append(body)

    out.append("")
    out.append(f"応答は {outbox} に JSON で書く（書き方は「応答の書き方」のとおり）")
    if direct:
        out.append("書いたら待機はしないで、このターンを終える。この会話への返事は書かない（問題があったときだけ、その内容を短く書く）")
        out.append("次の入力は、この会話に新しい指示として届く。wait_input.py をバックグラウンドで常駐させないこと")
    elif ide:
        me = f'python "{os.path.join(ROOT, "wait_input.py")}" --ide {ide}'
        out.append("書いたら待機はしないで、このターンを終える。この会話への返事は半角スペース1文字だけにする（問題があったときだけ、その内容を書く）")
        out.append("wait_input.py をバックグラウンドで常駐させないこと（受け取った入力を誰も読めなくなる）")
        out.append(f"次の入力があると、チャットからこの会話にスペース1文字（合図）が届く。届いたら {me} --wait 30 を実行する（前回の応答が届いたかもそこで確認される）")
    else:
        out.append("書いたら wait_input.py をもう一度実行せずに、このターンを終える（待ち続けると、入力がなくても使用量が減っていく）。"
                   "次の入力が来たら、ユーザーがこの会話で知らせる")
    if ctx.get("rules"):
        out.append(f"\n--- {ctx['rules']['label']} ---\n{ctx['rules']['text']}")
    return "\n".join(out) + "\n"


def with_render(s, m, ctx, task_type=None, ide="", note="", direct=False):
    """build_context 系の戻り値に表示文を付ける（/api/context, /api/claim, 直送で共用）"""
    if isinstance(ctx, dict) and "sections" in ctx:
        sid = s.get("id", "") if isinstance(s, dict) else str(s)
        ctx["render"] = render_input_text(sid, m, ctx, task_type, ide, note, direct)
    return ctx


def note_reply(s, reply_to, msg):
    """取り込んだ応答はAI自身が書いたものなので、送ったものとして記録する（次の差分に入れない）"""
    old = read_sent(s["id"])
    if not old.get("msgs") or old["msgs"][-1][0] != reply_to:
        return
    if (msg in s.get("messages", [])) or (isinstance(msg, dict) and msg.get("id") and find_msg(s, msg["id"])):
        old["msgs"].append(msg_sig(msg))
    memo = (s["setting"].get("memo") or "").strip()
    memo_ct = memo if memo else f"（これまでの出来事＋現在の状況を{MEMO_REMIND_TURNS}ターンごと又は大きな場面転換時に記録）"
    old.setdefault("sigs", {})["memo"] = sig_of(memo_ct)
    old.setdefault("labels", {})["memo"] = "AIメモ（これまでのあらすじ）"
    old.pop("reset", None)
    old.pop("why", None)
    write_sent(s["id"], old)


def mark_choices_sent(s):
    st = s.get("setting", {})
    target_ide = (st.get("choices_ide_session") if has_parallel_choices_ide(st) else st.get("ide_session")) or ""
    if target_ide:
        old = read_sent(s["id"])
        if old.get("choices_ide") != target_ide:
            old["choices_ide"] = target_ide
            write_sent(s["id"], old)


def normal_choices_output_text(s, m):
    user = s["setting"].get("user_name") or "プレイヤー"
    schema = {
        "session": s["id"],
        "reply_to": m["id"],
        "choices": [
            {
                "label": "行動・発言の要約",
                "text": "「短いセリフ」＋具体的なアクションや行動"
            }
        ]
    }
    sample_json = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        f"【💡 返答候補（choices）の自動生成ルール】\n"
        f"直前のAI応答に対して、{user}が次に取れる自然な返答候補（choices）を8個作成し、"
        f"outbox の JSON ファイルに出力してください。\n\n"
        f"■ バリエーション方針:\n"
        f"- 会話の文脈や現在の状況に沿って、{user}の自然で多彩な選択肢を作成すること。\n"
        f"- 日常会話・受け答え、状況を進める行動、リアクション・感想、スキンシップ、提案や問いかけなど、"
        f"文脈に応じた適切なバリエーションをバランスよく8個揃えること。\n\n"
        f"■ 各要素のフォーマット:\n"
        f"- label: 行動や発言の短い要約（10文字以内推奨。例: 優しく抱きしめる、状況を尋ねる、からかう、スキンシップ、提案する、感謝を伝える など）。\n"
        f"- text: 「短いセリフ」＋「具体的なアクションや行動」の組み合わせ（そのまま{user}の返答として送信できる自然な文章）。\n\n"
        f"■ JSON出力ルール:\n"
        f"- outbox/<session>_<任意>.json に UTF-8 で出力すること。\n"
        f"- 【重要】本文（text）や画像（image）は出力せず、choices（8件）のみを出力すること（本文や画像は既存のものがそのまま維持されます）。\n"
        f"```json\n"
        f"{sample_json}\n"
        f"```"
    )


def build_normal_choices_context(s, m):
    """返答候補（choices）を通常文脈に沿って自然に自動生成するためのコンテキスト"""
    st = s["setting"]
    msgs = s["messages"]
    idx = msgs.index(m) if m in msgs else len(msgs) - 1
    user = st.get("user_name") or "プレイヤー"
    ch = st.get("character") or {}
    ch_name = ch.get("name") or "キャラクター"

    sections = [
        {
            "label": "今回のタスク",
            "text": f"直前のAI応答に対する、{user}の次の自然な返答候補（choices）を8個作成する（本文や画像は出力不要）。"
        }
    ]

    chara_lines = []
    if ch_name:
        chara_lines.append(f"名前: {ch_name}")
    if ch.get("speech"):
        chara_lines.append(f"口調: {ch.get('speech')}")
    summary = (st.get("summary") or "").strip()
    if summary:
        chara_lines.append(f"概要・関係: {summary}")
    elif ch.get("profile"):
        p_lines = [l for l in ch["profile"].splitlines() if l.strip()]
        if len(p_lines) > 5:
            chara_lines.append("設定: " + "\n".join(p_lines[:5]) + "\n…")
        else:
            chara_lines.append("設定: " + "\n".join(p_lines))
    if chara_lines:
        sections.append({"label": "キャラクター情報", "text": "\n".join(chara_lines)})

    if user:
        sections.append({"label": "プレイヤー（ユーザー）名", "text": user})

    # 直前のユーザー発言があれば文脈として添付
    prev_user_msg = None
    for prev in reversed(msgs[:idx]):
        if prev.get("role") == "user":
            prev_user_msg = prev
            break
    if prev_user_msg and prev_user_msg.get("text"):
        prev_txt = prev_user_msg["text"]
        if len(prev_txt) > 300:
            prev_txt = prev_txt[:300] + "…"
        sections.append({
            "label": f"直前の{user}の発言",
            "text": prev_txt
        })

    sections.append({
        "label": "直前のAI応答本文（この応答に対する返答候補を作成する）",
        "text": m.get("text") or "（本文なし）"
    })

    if st.get("rp_state"):
        m_state = m.get("state")
        if not m_state and s.get("id") and m.get("id"):
            m_state = load_states_data(s["id"], readonly=True).get(m["id"])
        sections.append({
            "label": "キャラの状態",
            "text": state_block(m_state or state_before(msgs, idx, sid=s.get("id")))
        })

    sections.append({
        "label": "応答の書き方（outbox の JSON）",
        "text": normal_choices_output_text(s, m)
    })

    mark_choices_sent(s)
    return {
        "mode": "choices_normal",
        "choices_type": "normal",
        "reason": "返答候補（choices）の自動生成",
        "sections": sections,
        "same": [],
        "files": [],
        "light": True,
        "missing": [],
        "rules": None,
        "title": st.get("title")
    }


def parallel_choices_output_text(s, user_m):
    user = s["setting"].get("user_name") or "プレイヤー"
    ch_name = (s["setting"].get("character") or {}).get("name") or "キャラクター"
    schema = {
        "session": s["id"],
        "reply_to": user_m["id"],
        "type": "choices",
        "choices": [
            {
                "label": "行動・発言の要約",
                "text": "「短いセリフ」＋具体的なアクションや行動"
            }
        ]
    }
    sample_json = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        f"【💡 返答候補（choices）の並列先行生成ルール】\n"
        f"現在、メインのAIが『今回の{user}の発言』に対する{ch_name}の返答を執筆中です。\n"
        f"あなたは以下の2ステップで、**その{ch_name}の返答を受けた後に{user}が次に取る自然な返答候補（choices）を8個**作成し、outbox の JSON ファイルに出力してください。\n\n"
        f"■ ステップ1（内部推論・出力不要）:\n"
        f"- 直前の会話の流れ、キャラの状態（衣装・体勢・内心）、および『今回の{user}の発言』から、{ch_name}がどのように反応・返答するか（感情、態度、距離感、場面の進展）を予測する。\n\n"
        f"■ ステップ2（全方位カバー型・8択の作成）:\n"
        f"- 予測した{ch_name}の反応に対して、{user}が次ターンで返せる選択肢を8個作成する。\n"
        f"- メインAIが書く細かいセリフ回しに依存しすぎないよう、以下の8つの異なる方向性にバランスよく分散させて8個揃えること（どの展開になっても自然に繋がるようにする）：\n"
        f"  1. 共感・優しい声かけ（相手の反応を受け止めて安心させる・褒める）\n"
        f"  2. 行動・移動の提案（場面を一歩進める具体的なアクション）\n"
        f"  3. スキンシップ・距離を縮める（手を繋ぐ、抱き寄せる、触れる等、現在の関係性に合った接触）\n"
        f"  4. 質問・深掘り（相手の本音や状態、次の意向を尋ねる）\n"
        f"  5. 冗談・からかい（少し茶化す、照れさせる、空気を和ませる）\n"
        f"  6. 主導権を握る・積極的アプローチ（少し強引・情熱的にリードする）\n"
        f"  7. 相手に委ねる・様子を見る（相手のペースに合わせる、反応を待つ）\n"
        f"  8. 話題・空気の展開（周囲の状況やアイテム、時間帯を活かした別アプローチ）\n\n"
        f"■ 各要素のフォーマットと厳守事項:\n"
        f"- 【重要】{ch_name}のセリフではなく、必ず**プレイヤー（{user}）自身のセリフと行動**を書くこと。\n"
        f"- label: 行動や発言の短い要約（10文字以内推奨）。\n"
        f"- text: 「短いセリフ」＋「具体的なアクションや行動」の組み合わせ。\n"
        f"- outbox/<session>_choices_<任意>.json に UTF-8 で出力し、本文（text）や画像（image）は一切含めず choices（8件）のみを出力すること。\n"
        f"```json\n"
        f"{sample_json}\n"
        f"```"
    )


def build_parallel_choices_context(s, user_m):
    """並列先行生成用：ユーザー発言起点でメイン応答を待たずに次ターンの8択候補を先行生成するためのコンテキスト"""
    st = s["setting"]
    msgs = s["messages"]
    idx = msgs.index(user_m) if user_m in msgs else len(msgs) - 1
    user = st.get("user_name") or "プレイヤー"
    ch = st.get("character") or {}
    ch_name = ch.get("name") or "キャラクター"

    sections = [
        {
            "label": "今回のタスク",
            "text": (
                f"現在、メインのAIが今回の{user}の発言に対する{ch_name}の返答を執筆中です。\n"
                f"直前の流れとキャラ状態・{user}の発言から{ch_name}の返答を予測し、"
                f"その後に{user}が取れる自然な返答候補（choices）を全方位カバー型の8方向で作成してください。"
            )
        }
    ]

    chara_lines = []
    if ch_name:
        chara_lines.append(f"名前: {ch_name}")
    if ch.get("speech"):
        chara_lines.append(f"口調: {ch.get('speech')}")
    summary = (st.get("summary") or "").strip()
    if summary:
        chara_lines.append(f"概要・関係: {summary}")
    elif ch.get("profile"):
        p_lines = [l for l in ch["profile"].splitlines() if l.strip()]
        chara_lines.append("設定: " + "\n".join(p_lines[:5]) + ("\n…" if len(p_lines) > 5 else ""))

    el = st.get("elapsed_time") or {"day": 1, "period": "午前"}
    chara_lines.append(f"【作中時間】: {el.get('day', 1)}日目・{el.get('period', '午前')}")

    sections.append({"label": "キャラクター情報・作中時間", "text": "\n".join(chara_lines)})

    if st.get("rp_state"):
        sections.append({
            "label": "直前のキャラの状態",
            "text": state_block(state_before(msgs, idx, sid=s.get("id")))
        })

    memo = (st.get("memo") or "").strip()
    if memo:
        sections.append({"label": "これまでのあらすじ（AIメモ）", "text": memo})

    recent_msgs = msgs[max(0, idx - 6):idx]
    if recent_msgs:
        conv_lines = []
        for rm in recent_msgs:
            role_label = user if rm.get("role") == "user" else ch_name
            txt = rm.get("text") or ""
            if len(txt) > 300:
                txt = txt[:300] + "…"
            conv_lines.append(f"{role_label}: {txt}")
        sections.append({"label": "直近の会話の流れ", "text": "\n".join(conv_lines)})

    user_text = user_m.get("text") or "（セリフなし）"
    files = user_m.get("files") or []
    if files:
        fnames = ", ".join(f.get("name", "") for f in files if f.get("name"))
        user_text += f"\n[添付ファイル: {fnames}]"
    sections.append({
        "label": f"今回の{user}の発言（これに対するキャラの反応を予測し、さらにその次の{user}の選択肢を作る）",
        "text": user_text
    })

    sections.append({
        "label": "応答の書き方（outbox の JSON）",
        "text": parallel_choices_output_text(s, user_m)
    })

    mark_choices_sent(s)
    return {
        "mode": "choices",
        "choices_type": "normal",
        "reason": "返答候補（choices）の並列先行生成",
        "sections": sections,
        "same": [],
        "files": [],
        "light": True,
        "missing": [],
        "rules": None,
        "title": st.get("title"),
        "reply_to": user_m.get("id")
    }


def build_memory_extract_context(s, m):
    """長期記憶の要約・抽出タスク用のコンテキスト"""
    st = s["setting"]
    msgs = s["messages"]
    idx = msgs.index(m) if m in msgs else len(msgs) - 1
    user = st.get("user_name") or "ユーザー"
    ch = st.get("character") or {}
    ch_name = ch.get("name") or "あなた"

    sections = [
        {
            "label": "長期記憶の要約・抽出タスク（指示）",
            "text": (
                "【今回のタスク: 長期記憶の要約・抽出】\n"
                f"直前に行われたあなた（{ch_name}）と{user}のやり取りから、\n"
                "「今後の会話や将来の展開で忘れてはならない重要な情報」を抽出して outbox に出力してください。\n\n"
                "■ 抽出カテゴリ（8種）:\n"
                "1. calling: 互いの呼び方・呼称・敬語の有無などの口調の協定（例: ユーザーを「お兄ちゃん」と呼ぶことにした）\n"
                "2. rule_taboo: 世界観ルール、校則、触れてはならない地雷・禁忌、二人だけの秘密（例: 夜10時以降は部屋から出てはならない）\n"
                "3. item_status: キーアイテム、プレゼント、持続的刻印・痕跡（合鍵、指輪、首輪、アナルプラグ等の持続的装着具、キスマーク等）\n"
                "   ※【除外対象】脱着・着替えで容易に変化する一時的な衣服・下着の細かいパーツ（ショーツの柄、リボン、レース等）は絶対に登録しないでください（これらは state で管理します）。\n"
                "4. promise: 交わした約束、予定、誓約（例: 次の日曜日に水族館へ行く約束をした）\n"
                "5. char_fact: キャラ自身（あなた）が開示した過去、トラウマ、弱点、秘密、体質（例: 実は雷が極端に苦手である）\n"
                "6. user_fact: 判明したユーザーの好み、素性、個人情報、弱点（例: ユーザーは猫が好きで甘党である）\n"
                "7. relationship: 二人の関係性の進展、心の距離の変化、好意の深まり（例: 互いに特別な好意を抱き、恋人同士になった）\n"
                "8. event: 上記に当てはまらない二人の間に起きた重要な出来事・事件・場面転換（例: 初めて空き教室で二人きりで過ごした）\n\n"
                "■ 記述ルール:\n"
                "- 【重要: 三人称完全自立文の義務付け】主語（キャラ名やユーザー名）を省略せず、前後の文脈がなくても1行単体で誰の何の情報か100%特定できる完全な文で記述してください。\n"
                f"  ❌ NG例: 「奥まで挿入されて疼いている。」 / 「フロントに赤いリボンが付いている。」\n"
                f"  ⭕ OK例: 「{ch_name}の肛門にアナルプラグが装着された状態になっている。」 / 「{ch_name}が{user}からペアリングをプレゼントされた。」\n"
                "- 日常的な挨拶、単なる相槌、一時的な雑談は抽出せず、空配列 [] にしてください。\n"
                "- 「明日」「来週」などの相対的な日時表現は避け、文脈上の具体的な日付・曜日、または「日曜日に水族館へ行く約束をした」のように時間が経過しても矛盾しない客観的表現で記述してください。\n"
                "- 直前の会話によって、現在保持している【有効な約束・アイテム・ルール】が「達成・完了した」「返却・消費された」「消失した」「解除された」場合は、その記憶IDを resolved_ids に指定してください。必要であれば完了・解除した事実を event として新しく抽出してください。\n"
                "- 会話本文や画像プロンプト、返答候補等は出力せず、指定されたJSONスキーマのみを出力してください。"
            )
        }
    ]

    user_m = next((x for x in reversed(msgs[:idx]) if x.get("role") == "user"), None)
    exchange_lines = []
    if user_m:
        exchange_lines.append(f"【{user}の発言】\n{user_m.get('text', '')}")
    exchange_lines.append(f"【{ch_name}の返答】\n{m.get('text', '')}")

    sections.append({
        "label": "直前の会話（抽出対象）",
        "text": "\n\n".join(exchange_lines)
    })

    try:
        mem_data = memory_store.load_memories(s["id"], ch_name)
        all_mems = mem_data.get("memories", [])
        active_items = []
        for m_item in all_mems:
            if m_item.get("status", "active") != "active":
                continue
            c = m_item.get("category")
            if c in ("promise", "item_status", "item", "status", "rule_taboo", "rule", "taboo", "calling"):
                c_lbl = memory_store.CATEGORY_LABELS.get(c, c)
                active_items.append(f"- ID: {m_item['id']} | [{c_lbl}] {m_item['content']}")
        if active_items:
            sections.append({
                "label": "現在有効な約束・アイテム・ルール・呼称（達成・消費・解除されたものがあれば resolved_ids に指定）",
                "text": "\n".join(active_items)
            })
    except Exception as e:
        print(f"[build_memory_extract_context] error reading memories: {e}")

    sample_json = json.dumps({
        "session": s["id"],
        "reply_to": m["id"],
        "type": "memory_extract",
        "memories": [
            {
                "category": "promise",
                "content": f"{ch_name}は{user}と日曜日に駅前の水族館へ行く約束をした"
            }
        ],
        "resolved_ids": []
    }, ensure_ascii=False, indent=2)

    sections.append({
        "label": "応答の書き方（outbox の JSON）",
        "text": (
            "outbox の場所に、以下の形式の UTF-8 JSON ファイルを出力してください（ファイル名: <session>_<任意>.json）。\n"
            "```json\n"
            f"{sample_json}\n"
            "```"
        )
    })

    return {
        "mode": "memory_extract",
        "reason": "長期記憶の要約・抽出",
        "sections": sections,
        "same": [],
        "files": [],
        "light": True,
        "missing": [],
        "rules": None,
        "title": st.get("title")
    }


def build_consolidation_context(s, m):
    """世代交代の節目での睡眠プロセス（一括蒸留＆作中時間確定タスク）用のコンテキスト"""
    st = s["setting"]
    msgs = s["messages"]
    idx = msgs.index(m) if m in msgs else len(msgs) - 1
    user = st.get("user_name") or "ユーザー"
    ch = st.get("character") or {}
    ch_name = ch.get("name") or "キャラクター"
    cur_elapsed = st.get("elapsed_time") or {"day": 1, "period": "午前"}
    cur_state = state_before(msgs, idx + 1, sid=s.get("id"))

    sample_json = json.dumps({
        "session": s["id"],
        "reply_to": m["id"],
        "type": "consolidation",
        "memo": "【これまでの主な出来事】\n・1日目: ...\n【現在の状況】\n・2日目: ...",
        "elapsed_time": {
            "day": 2,
            "period": "早朝"
        },
        "state": [
            {
                "name": ch_name,
                "outfit": "パジャマ（または全裸）",
                "outerwear": "なし",
                "underwear": "なし",
                "equipped_items": [],
                "penetration": "なし",
                "body_fluids": "なし",
                "pose": "仰向け",
                "expression": ["穏やか"],
                "place": "寝室",
                "camera": "from above",
                "situation": ["ベッドの中でユーザーの腕枕で眠っている"],
                "inner": "ユーザーの温もりに包まれて幸せを感じている"
            }
        ]
    }, ensure_ascii=False, indent=2)

    sections = [
        {
            "label": "睡眠プロセス（世代交代・一括蒸留＆作中時間確定タスク）",
            "text": (
                "【今回のタスク: 世代交代の節目の睡眠プロセス（時間確定と記憶蒸留）】\n"
                f"これまでの{ch_name}と{user}の会話全体を振り返り、新スレッド開始に向けて状態を整頓してください。\n\n"
                "以下の3点を outbox の JSON に出力してください：\n"
                "1. memo: これまでの出来事史を整理したあらすじ（これまでの主な出来事＋現在の状況の最新全文）\n"
                "2. elapsed_time: ここまでの会話全体の展開を踏まえた作中時間（何日目のどの時間帯に達したか。翌朝を迎えた場合は day += 1 して早朝または午前）\n"
                "3. state: 新スレッド開始時点のキャラの状態（outfit, outerwear, underwear, equipped_items, penetration, body_fluids, pose, expression, place, camera, situation, inner）。就寝中は pose（仰向け・横向き等）と expression（穏やか・寝顔等）も書く。\n"
                "   【シチュエーション連動推移ルール】\n"
                "   - 早朝・ベッドの中への移行: 前夜の全裸・乱れた肌着・装着具挿入状態をそのまま維持（朝の余韻・ピロートークを保護）。\n"
                "   - 午前・昼へのスキップ: 起床・身支度を済ませた場面転換として、状況に適した衣装（制服・普段着）に自然に更新し、就寝時の器具等は外す。\n\n"
                "※本文（text）や画像（image）は出力不要です。指定のJSONのみ出力してください。"
            )
        },
        {
            "label": "これまでの会話ログ（直近分）",
            "text": history_text(msgs[max(0, idx - 25):idx + 1], user, limit=10 ** 9)
        },
        {
            "label": "直前の作中経過時間・キャラ状態",
            "text": f"作中時間: {cur_elapsed.get('day', 1)}日目・{cur_elapsed.get('period', '午前')}\n"
                    f"直前のキャラ状態:\n{state_block(cur_state)}"
        },
        {
            "label": "応答の書き方（outbox の JSON）",
            "text": (
                "outbox の場所に、以下の形式の UTF-8 JSON ファイルを出力してください（ファイル名: <session>_<任意>.json）。\n"
                "```json\n"
                f"{sample_json}\n"
                "```"
            )
        }
    ]

    return {
        "mode": "consolidation",
        "reason": "世代交代の睡眠プロセス（時間確定と記憶蒸留）",
        "sections": sections,
        "same": [],
        "files": [],
        "light": True,
        "missing": [],
        "rules": None,
        "title": st.get("title")
    }


def run_memory_regeneration(sid, chunks, char_name):
    try:
        s_data = load(sid, readonly=True)
        st = s_data.get("setting", {})
        user_name = st.get("user_name") or "ユーザー"
        extra = []
        if user_name and user_name not in ("ユーザー", "あなた", ""):
            extra.append(user_name)
        for c in st.get("chars") or []:
            if isinstance(c, dict) and c.get("name"):
                extra.append(c["name"])

        total = len(chunks)
        cumulative_turns = 0
        for idx, chunk_msgs in enumerate(chunks):
            current = idx + 1
            user_msgs = [m for m in chunk_msgs if m.get("role") == "user"]
            asst_msgs = [m for m in chunk_msgs if m.get("role") == "assistant"]
            cumulative_turns += len(user_msgs)

            extracted = []
            for u in user_msgs:
                for item in extract_memories_from_text(user_text=u.get("text", ""), char_name=char_name, user_name=user_name, extra_names=extra):
                    extracted.append(item)
            for a in asst_msgs:
                for item in extract_memories_from_text(asst_text=a.get("text", ""), char_name=char_name, user_name=user_name, extra_names=extra):
                    extracted.append(item)

            if extracted:
                chunk_elapsed = None
                for a in reversed(asst_msgs):
                    if a.get("elapsed_time"):
                        chunk_elapsed = a["elapsed_time"]
                        break
                if not chunk_elapsed:
                    try:
                        s_data = load(sid, readonly=True)
                        chunk_elapsed = s_data.get("setting", {}).get("elapsed_time")
                    except Exception:
                        pass
                if not chunk_elapsed:
                    chunk_elapsed = {"day": 1, "period": "午前"}
                memory_store.save_extracted_memories(
                    sid,
                    char_name,
                    extracted,
                    turn_idx=cumulative_turns,
                    mid=asst_msgs[-1]["id"] if asst_msgs else None,
                    elapsed_time=chunk_elapsed
                )
            mem_data = memory_store.load_memories(sid)
            mem_count = len(mem_data.get("memories", []))
            progress_pct = int((current / max(1, total)) * 100)
            memory_regen_state[sid] = {
                "running": True,
                "current": current,
                "total": total,
                "processed_chunks": current,
                "total_chunks": total,
                "progress": progress_pct,
                "memories_count": mem_count,
                "status": "processing"
            }
            time.sleep(0.15)
        mem_data = memory_store.load_memories(sid)
        mem_count = len(mem_data.get("memories", []))
        memory_regen_state[sid] = {
            "running": False,
            "current": total,
            "total": total,
            "processed_chunks": total,
            "total_chunks": total,
            "progress": 100,
            "memories_count": mem_count,
            "status": "completed"
        }
    except Exception as e:
        print(f"[regen] error: {e}")
        memory_regen_state[sid] = {
            "running": False,
            "current": 0,
            "total": 0,
            "processed_chunks": 0,
            "total_chunks": 0,
            "progress": 0,
            "memories_count": 0,
            "status": "error",
            "error": str(e)
        }


def choices_regen_output_text(s, m):
    schema = {
        "session": s["id"],
        "reply_to": m["id"],
        "choices": [
            {
                "label": "行動・プレイの要約",
                "text": "「短いセリフ」＋具体的な行動や指示"
            }
        ]
    }
    sample_json = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "【🔞 ドスケベ返答候補（choices）の再生成ルール】\n"
        "プレイヤー（ユーザー）が直前のAI応答に対して次に取れる、極めて刺激的・淫猥・ドスケベな返答候補（choices）を8個作成し、"
        "outbox の JSON ファイルに出力してください。\n\n"
        "■ バリエーション方針:\n"
        "- 8つの枠ごとの役割は固定せず、多彩なドS・変態・ハードプレイリスト（全裸/露出、羞恥ポーズ/変態セリフ強要、放尿/排泄調教、"
        "アナル/異物挿入、拘束/緊縛/スパンキング、尿道/クリ責め/種付け、道具使用など）から文脈・キャラに合わせてAIが自由にランダムな8択をピックアップすること。\n"
        "- 通常の日常会話やノーマルな行動選択肢（愛撫/前戯/言葉責め/直接結合/ピストン等の一般的な選択肢）は排除し、刺激的な行動・セリフに特化すること。\n\n"
        "■ 各要素のフォーマット:\n"
        "- label: 行動・プレイ・指示の短い要約（例: 全裸露出、羞恥強要、アナル調教、緊縛拘束、道具挿入、放尿強要、尿道責め、種付け など）。\n"
        "- text: 「短いセリフ」＋「具体的なアクションや状況を進める指示」の組み合わせ（そのままユーザーの返答として送信できる具体的な文章）。\n\n"
        "■ JSON出力ルール:\n"
        "- outbox/<session>_<任意>.json に UTF-8 で出力すること。\n"
        "- 【重要】本文（text）や画像（image）は再生成せず、choices（8件）のみを出力すること（本文や画像は既存のものがそのまま維持されます）。\n"
        "```json\n"
        f"{sample_json}\n"
        "```"
    )


def build_choices_context(s, m):
    """返答候補（choices）のみをドスケベに再生成するためのコンテキスト"""
    st = s["setting"]
    msgs = s["messages"]
    idx = msgs.index(m) if m in msgs else len(msgs) - 1
    user = st.get("user_name") or "プレイヤー"
    ch = st.get("character") or {}
    ch_name = ch.get("name") or "キャラクター"

    sections = [
        {
            "label": "今回のタスク",
            "text": "直前のAI応答に対する返答候補について、8つの選択肢（choices）のみをドスケベ・変態・ハードプレイに特化して再生成する。"
        }
    ]

    chara_lines = []
    if ch_name:
        chara_lines.append(f"名前: {ch_name}")
    if ch.get("speech"):
        chara_lines.append(f"口調: {ch.get('speech')}")
    profile = ch.get("profile") or ""
    if profile:
        p_lines = [l for l in profile.splitlines() if l.strip()]
        if len(p_lines) > 8:
            chara_lines.append("設定: " + "\n".join(p_lines[:8]) + "\n…")
        else:
            chara_lines.append("設定: " + profile)
    if chara_lines:
        sections.append({"label": "キャラクター設定", "text": "\n".join(chara_lines)})

    if user:
        sections.append({"label": "プレイヤー（ユーザー）名", "text": user})

    # 直前のユーザー発言があれば文脈として添付
    prev_user_msg = None
    for prev in reversed(msgs[:idx]):
        if prev.get("role") == "user":
            prev_user_msg = prev
            break
    if prev_user_msg and prev_user_msg.get("text"):
        prev_txt = prev_user_msg["text"]
        if len(prev_txt) > 300:
            prev_txt = prev_txt[:300] + "…"
        sections.append({
            "label": f"直前の{user}の発言",
            "text": prev_txt
        })

    sections.append({
        "label": "直前のAI応答本文（この応答への返答候補を作成する）",
        "text": m.get("text") or "（本文なし）"
    })

    if st.get("rp_state"):
        m_state = m.get("state")
        if not m_state and s.get("id") and m.get("id"):
            m_state = load_states_data(s["id"], readonly=True).get(m["id"])
        sections.append({
            "label": "キャラの状態",
            "text": state_block(m_state or state_before(msgs, idx, sid=s.get("id")))
        })

    sections.append({
        "label": "応答の書き方（outbox の JSON）",
        "text": choices_regen_output_text(s, m)
    })

    mark_choices_sent(s)
    return {
        "mode": "choices_erotic",
        "choices_type": "erotic",
        "reason": "返答候補（choices）のドスケベ再生成",
        "sections": sections,
        "same": [],
        "files": [],
        "light": True,
        "missing": [],
        "rules": None,
        "title": st.get("title")
    }


COUNT_TAG = re.compile(r"(\d+\+?|multiple |no )(girl|boy|other)s?|solo|solo focus", re.I)

PROHIBITED_ESCAPE_TAGS = {
    "off-screen male", "off screen male", "offscreen male",
    "off-screen user", "off screen user", "offscreen user",
    "off-screen partner", "off screen partner", "offscreen partner",
    "hands on user thighs", "hands on users thighs",
    "hands on user legs", "hands on users legs",
    "hand on user thighs", "hand on users thighs",
    "hand on user thigh", "hands on user thigh", "hand on users thigh", "hands on users thigh",
    "hands on partner thighs", "hands on partners thighs",
    "narrow space", "submissive",
    "cheeks puffed", "puffed cheeks"
}

CORE_ANATOMY_TAGS = {
    "penis", "fellatio", "deepthroat", "blowjob", "irrumatio",
    "vaginal penetration", "anal penetration", "penetration",
    "pussy", "spread pussy", "exposed pussy",
    "anus", "exposed anus", "erect nipples", "nipples",
    "fingering", "paizuri", "handjob", "cunnilingus",
    "creampie", "anal creampie"
}

# ポーズ（体勢）の日本語表現 → 実在の Danbooru 英語タグ変換テーブル。
# 画像生成モデルは造語タグ（i-balance 等）を理解できないため、必ず実在タグへ寄せる。
POSE_TAG_MAP = (
    # 脚・開脚・バランス系
    (("i字バランス", "i字", "iバランス", "iポーズ"), ("standing split", "leg up")),
    (("m字開脚", "m字"), ("spread legs", "knees up", "sitting")),
    (("v字バランス",), ("sitting", "legs up", "spread legs")),
    (("開脚", "がに股", "ガニ股"), ("spread legs",)),
    (("片足立ち", "片脚立ち"), ("standing on one leg",)),
    (("足を上げ", "脚を上げ", "足を持ち上げ", "脚を持ち上げ", "足を高く", "脚を高く"), ("leg up",)),
    # 体勢・姿勢
    (("四つん這い", "四つ這い"), ("all fours",)),
    (("逆立ち", "倒立"), ("handstand", "upside-down")),
    (("ブリッジ",), ("arched back",)),
    (("うつ伏せ", "うつぶせ"), ("lying", "on stomach")),
    (("仰向け", "仰向き"), ("lying", "on back")),
    (("横向き", "横たわ"), ("lying", "on side")),
    (("膝立ち", "ひざ立ち"), ("kneeling",)),
    (("正座",), ("seiza", "sitting")),
    (("体育座り", "膝を抱え"), ("sitting", "knees up", "hugging own legs")),
    (("ぺたん座り", "女の子座り"), ("wariza",)),
    (("横座り",), ("yokozuwari",)),
    (("あぐら", "胡座"), ("indian style",)),
    (("しゃがみ", "しゃがんで"), ("squatting",)),
    (("土下座",), ("dogeza",)),
    (("前屈み", "前かがみ", "屈んで", "屈み"), ("bent over",)),
    (("立ち上が", "立ち姿", "立ったまま", "立っている", "立って", "立ち", "立位"), ("standing",)),
    (("座り", "座って", "着席", "腰掛け"), ("sitting",)),
    (("バンザイ", "万歳"), ("arms up",)),
    (("お姫様抱っこ", "姫抱っこ"), ("princess carry",)),
    (("手を繋", "手をつな"), ("holding hands",)),
    (("ジャンプ", "飛び跳ね"), ("jumping",)),
)

# 造語・表記ゆれのポーズタグ → 実在の Danbooru タグ
POSE_TAG_ALIASES = {
    "i-balance": "standing split",
    "i balance": "standing split",
    "i-shaped balance": "standing split",
    "i shaped balance": "standing split",
    "ishaped balance": "standing split",
    "i-pose": "standing split",
    "i pose": "standing split",
    "standing splits": "standing split",
    "vertical split": "standing split",
}

POSE_SORT_TAGS = {t for _, tags in POSE_TAG_MAP for t in tags} | set(POSE_TAG_ALIASES.values())

# 小道具・装具・排泄の日本語表現 → 実在の Danbooru 英語タグ変換テーブル
# （AI が「anal jelly」等の造語を書いても画像に反映されないため、実在タグへ寄せる）
PROP_TAG_MAP = (
    # lubricant は「液体」のタグなので、ゼリー・浣腸器は物体として描かれる anal object insertion / enema も併記する
    (("アナルゼリー", "アナルジェル", "アナルローション"), ("lubricant", "anal", "anal object insertion")),
    (("ローション", "潤滑油", "潤滑液"), ("lubricant",)),
    (("浣腸", "浣腸器", "エネマ"), ("enema", "anal object insertion")),
    (("アナルビーズ",), ("anal beads",)),
    (("アナルプラグ", "バットプラグ", "プラグ"), ("butt plug",)),
    (("バイブ", "電マ", "電動マッサージャー", "ローター"), ("vibrator",)),
    (("オナホ", "大人のおもちゃ"), ("sex toy",)),
    (("ペニバン", "張り型"), ("strap-on",)),
    (("コンドーム", "ゴムを付け"), ("condom",)),
    (("縄", "緊縛", "縛り", "拘束"), ("bondage", "rope")),
    (("手錠",), ("handcuffs",)),
    (("目隠し",), ("blindfold",)),
    (("ボールギャグ", "口枷", "猿轡"), ("gag", "ball gag")),
    (("スパンキング", "尻叩き", "お尻ペンペン"), ("spanking",)),
    (("蝋燭", "ロウソク", "蝋を垂"), ("wax play",)),
    (("搾乳", "母乳", "お乳を搾"), ("lactation", "breast milk")),
    (("触手",), ("tentacles",)),
    (("放尿", "おしっこ", "小便", "排尿", "検尿", "尿意"), ("peeing", "urine")),
    (("うんち", "大便", "脱糞", "ウンコ", "スカトロ"), ("feces", "pooping")),
)

# 造語・表記ゆれの小道具・排泄タグ → 実在の Danbooru タグ
PROP_TAG_ALIASES = {
    # ゼリー・ジェルは「物体としての注入」なので anal object insertion に寄せる（lubricant だけでは描かれない）
    "anal jelly": "anal object insertion",
    "anal gel": "anal object insertion",
    "anal lube": "lubricant",
    "lube": "lubricant",
    "pink jelly": "anal object insertion",
    "anal jelly insertion": "anal object insertion",
    "anal discharge": "anal",
    "pee": "peeing",
    "piss": "peeing",
    "urinating": "peeing",
    "urinate": "peeing",
    "poop": "pooping",
    "scat": "feces",
    "shit": "feces",
    "defecation": "pooping",
    "sex toys": "sex toy",
}

# state の equipped_items（実際に身につけている物・挿入している物）用の辞書。
# テキストより広いキーワードで拾い、AI がプロンプトに書き忘れてもタグを補完する
ITEM_TAG_MAP = (
    (("アナルゼリー", "アナルジェル", "ジェル", "ゼリー", "ローション", "潤滑"), ("lubricant", "anal object insertion")),
    (("浣腸", "エネマ"), ("enema", "anal object insertion")),
    (("アナルプラグ", "プラグ"), ("butt plug",)),
    (("アナルビーズ", "ビーズ"), ("anal beads",)),
    (("バイブ", "電マ", "ローター", "マッサージ"), ("vibrator",)),
    (("コンドーム", "ゴム"), ("condom",)),
    (("目隠し",), ("blindfold",)),
    (("手錠",), ("handcuffs",)),
    (("口枷", "猿轡"), ("gag",)),
    (("縄", "ロープ"), ("rope", "bondage")),
    (("首輪", "チョーカー"), ("collar",)),
    (("リード",), ("leash",)),
    (("尻尾", "しっぽ"), ("fake tail",)),
    (("ニンジン", "人参"), ("carrot",)),
    (("スポンジ",), ("sponge",)),
    (("蝋", "ロウソク"), ("wax play",)),
    (("搾乳", "吸引器"), ("milking machine",)),
    (("聴診器",), ("stethoscope",)),
    (("おむつ", "オムツ"), ("diaper",)),
)

# 「物が入っている」ことを表す語。penetration がこれらを含む場合は性交と誤判定して penis を補完しない
OBJECT_INSERTION_MARKERS = ("プラグ", "バイブ", "ローター", "玩具", "ビーズ", "ディルド", "張り型", "ペニバン", "ゼリー", "ジェル", "浣腸", "異物", "ボトル", "瓶", "シャンプー")


def log_item_tag_miss(items):
    """state にあったがタグ辞書に無かった装具・小道具を記録する（辞書拡張用。画像には使わない）"""
    try:
        p = os.path.join(DATA, "item_tag_misses.log")
        line = f"{now()} {' / '.join(items)}\n"
        last = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rows = f.readlines()
            last = rows[-1] if rows else ""
        if line != last:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def log_state_tag_miss(field, value):
    """state の値が辞書に無かったことを記録する（辞書拡張用。画像には使わない）"""
    try:
        p = os.path.join(DATA, "state_tag_misses.log")
        line = f"{now()} [{field}] {value}\n"
        last = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rows = f.readlines()
            last = rows[-1] if rows else ""
        last_body = re.sub(r"^\S+\s+", "", last).rstrip("\r\n") if last else ""
        if f"[{field}] {value}" != last_body:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass

_PENIS_VISIBLE_TAGS = {
    "penis", "fellatio", "deepthroat", "irrumatio", "blowjob", "oral",
    "handjob", "paizuri", "penile", "cum on penis", "penis in mouth",
    "penis between breasts", "penis in pussy", "penis in anus",
}

CENSOR_RELATED_TAGS = {
    "uncensored", "censored", "censor", "censoring",
    "mosaic censoring", "mosaic_censoring", "mosaic",
    "bar censor", "bar_censor", "censor bar", "censored bar"
}

# 口淫と断定できる強い語。これがある時だけ自動補完の対象にする
ORAL_ACT_KEYWORDS = (
    "フェラ", "口淫", "イラマチオ", "イラマ", "咥えさせ", "しゃぶら",
    "カリ首", "亀頭", "ペニスを口", "肉棒を口", "男根を口", "お掃除フェラ",
    "口内射精", "ちんぽを口", "ちんちんを口",
    "飲精", "精液を飲"
)

# 単体では口淫と断定できない語（飴を咥える・喉奥に詰まる・指をしゃぶる・口中温度・お掃除など）。
# これらだけでは補完しない（男性器タグがあるときだけ補助的に使う）
ORAL_AMBIGUOUS_KEYWORDS = ("咥え", "くわえ", "しゃぶ", "口に含", "口内", "喉奥", "のど奥", "ご奉仕", "お掃除")


def normalize_tag(tag):
    if not tag or not isinstance(tag, str):
        return ""
    t = tag.strip().lower()
    while True:
        m = re.match(r"^[\(\[\{](.*)[\)\]\}]$", t)
        if m:
            t = m.group(1).strip()
        else:
            break
    t = re.sub(r":\s*\d+(?:\.\d+)?$", "", t).strip()
    while t.endswith((")", "]", "}")) and t.count("(") < t.count(")"):
        t = t[:-1].strip()
    while t.startswith(("(", "[", "{")) and t.count("(") > t.count(")"):
        t = t[1:].strip()
    t = t.replace("'", "")
    t = re.sub(r"[\s_]+", " ", t).strip()
    return t


def image_tag_context(s, msg, extra=""):
    """画像タグ補完用の文脈テキスト。AIの返答本文に加えて、その元になったユーザー発言も含める
    （ユーザーの指示（ポーズ・装具・排泄など）を読み取ってタグに反映するため）"""
    parts = [extra or ""]
    msgs = (s or {}).get("messages") or []
    mid = (msg or {}).get("id")
    idx = next((i for i, x in enumerate(msgs) if mid and x.get("id") == mid), None)
    if idx is not None:
        for x in reversed(msgs[:idx + 1]):
            if x.get("role") == "user":
                parts.append(x.get("text") or "")
                break
    return "\n".join(p for p in parts if p)


def normalize_pose_keyword_text(text):
    """全角英数・大文字小文字を揃えてポーズキーワードを検索できるようにする"""
    if not text:
        return ""
    table = str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    )
    return str(text).translate(table).lower()


# ---------------------------------------------------------------- 画像タグ生成仕様 v2 (Step 1-2)
EXPRESSION_TAG_MAP = (
    (("笑顔", "微笑み"), ("smile",)),
    (("満面の笑み",), ("grin",)),
    (("照れ笑い",), ("smile", "blush")),
    (("羞恥", "恥ずかし"), ("blush",)),
    (("赤面", "真っ赤"), ("heavy blush",)),
    (("涙目",), ("teary eyes",)),
    (("泣き", "号泣"), ("crying",)),
    (("恍惚", "とろけ顔"), ("ahegao",)),
    (("絶頂",), ("rolling eyes",)),
    (("半目", "とろんと"), ("half-closed eyes",)),
    (("舌出し",), ("tongue out",)),
    (("よだれ",), ("drooling",)),
    (("口を開け", "半開き"), ("open mouth",)),
    (("見上げ",), ("looking up",)),
    (("見下ろし",), ("looking down",)),
    (("目をそらす",), ("looking away",)),
    (("こちらを見る",), ("looking at viewer",)),
    (("無表情",), ("expressionless",)),
    (("真剣",), ("serious",)),
    (("怒り", "不機嫌"), ("angry",)),
    (("拗ね",), ("pout",)),
    (("驚き", "目を見開"), ("surprised",)),
    (("怯え", "恐怖"), ("scared",)),
    (("不安", "心配"), ("worried",)),
    (("悲しみ",), ("sad",)),
)

PLACE_TAG_MAP = (
    (("バスルーム", "浴室", "お風呂"), ("bathroom",), "in"),
    (("寝室", "ベッドルーム"), ("bedroom",), "in"),
    (("ベッドの上", "ベッド"), ("bed",), "in"),
    (("リビング", "居間"), ("living room",), "in"),
    (("キッチン",), ("kitchen",), "in"),
    (("教室",), ("classroom",), "in"),
    (("学校",), ("school",), "in"),
    (("保健室",), ("infirmary",), "in"),
    (("屋上",), ("rooftop",), "out"),
    (("公園",), ("park",), "out"),
    (("海辺", "ビーチ"), ("beach",), "out"),
    (("プール",), ("pool",), ""),
    (("オフィス", "会社"), ("office",), "in"),
    (("車内", "車の中"), ("car",), "in"),
    (("電車",), ("train",), "in"),
    (("ホテル",), ("hotel",), "in"),
    (("温泉",), ("onsen",), ""),
    (("神社",), ("shrine",), "out"),
    (("森の中", "森林"), ("forest",), "out"),
    (("洞窟",), ("cave",), "in"),
    (("ダンジョン",), ("dungeon",), "in"),
    (("城内", "お城", "古城"), ("castle",), "in"),
    (("図書館",), ("library",), "in"),
    (("カフェ",), ("cafe",), "in"),
    (("トイレ",), ("toilet",), "in"),
    (("屋外",), ("outdoors",), "out"),
    (("屋内",), ("indoors",), "in"),
)

CAMERA_TAG_MAP = (
    (("俯瞰", "見下ろし"), ("from above",)),
    (("ローアングル", "あおり"), ("from below",)),
    (("後ろから", "背面"), ("from behind",)),
    (("正面",), ("from front",)),
    (("横から",), ("from side",)),
    (("接写", "アップ", "手元"), ("close-up",)),
    (("顔アップ",), ("portrait",)),
    (("全身",), ("full body",)),
    (("バストアップ",), ("upper body",)),
    (("主観", "pov"), ("pov",)),
    (("引きの構図", "遠景"), ("wide shot",)),
)

POSTURE_CONFLICT_TAGS = {
    "standing", "standing on one leg", "standing split", "all fours", "kneeling", "sitting",
    "seiza", "wariza", "yokozuwari", "indian style", "squatting", "dogeza",
    "lying", "on back", "on stomach", "on side", "handstand", "upside-down",
}

PLACE_NEVER_STRIP = {"bed"}
PLACE_COMPATIBLE = {
    frozenset({"bed", "bedroom"}),
    frozenset({"bed", "hotel"}),
    frozenset({"bed", "infirmary"}),
    frozenset({"school", "classroom"}),
    frozenset({"school", "infirmary"}),
    frozenset({"school", "rooftop"}),
    frozenset({"hotel", "bedroom"}),
    frozenset({"hotel", "bathroom"}),
    frozenset({"castle", "dungeon"}),
    frozenset({"onsen", "bathroom"}),
}

PLACE_SORT_TAGS = {t for _, tags, _ in PLACE_TAG_MAP for t in tags} | {"indoors", "outdoors"}

CAMERA_ANGLE_TAGS = {"from above", "from below", "from behind", "from front", "from side"}
CAMERA_FRAMING_TAGS = {"close-up", "portrait", "full body", "upper body", "wide shot"}
CAMERA_SORT_TAGS = ({t for _, tags in CAMERA_TAG_MAP for t in tags} | CAMERA_ANGLE_TAGS | CAMERA_FRAMING_TAGS) - {"pov"}

EXPRESSION_GROUPS = (
    {"smile", "grin"},
    {"blush", "heavy blush"},
    {"teary eyes", "crying"},
    {"open mouth", "parted lips"},
    {"ahegao", "rolling eyes"},
    {"looking at viewer", "looking away", "looking up", "looking down"},
)


# ---------------------------------------------------------------- 辞書のJSON化
# 辞書は tag_dictionary.json（プロジェクト直下）が唯一の正。
# このファイルが無い/壊れている場合は、上のコード内定義をフォールバックとして使う（起動は止めない）。
# 配列の順序は変換の優先順位（長いキー優先・同長は定義順）。並べ替えないこと。
TAG_DICT_PATH = os.path.join(ROOT, "tag_dictionary.json")
_tag_dict_lock = threading.Lock()
_tag_dict_mtime = 0.0

_DEFAULT_POSE_TAG_MAP = POSE_TAG_MAP
_DEFAULT_POSE_TAG_ALIASES = POSE_TAG_ALIASES
_DEFAULT_PROP_TAG_MAP = PROP_TAG_MAP
_DEFAULT_PROP_TAG_ALIASES = PROP_TAG_ALIASES
_DEFAULT_ITEM_TAG_MAP = ITEM_TAG_MAP
_DEFAULT_EXPRESSION_TAG_MAP = EXPRESSION_TAG_MAP
_DEFAULT_PLACE_TAG_MAP = PLACE_TAG_MAP
_DEFAULT_CAMERA_TAG_MAP = CAMERA_TAG_MAP
_DEFAULT_POSTURE_CONFLICT_TAGS = POSTURE_CONFLICT_TAGS
_DEFAULT_PLACE_NEVER_STRIP = PLACE_NEVER_STRIP
_DEFAULT_PLACE_COMPATIBLE = PLACE_COMPATIBLE
_DEFAULT_CAMERA_ANGLE_TAGS = CAMERA_ANGLE_TAGS
_DEFAULT_CAMERA_FRAMING_TAGS = CAMERA_FRAMING_TAGS
_DEFAULT_EXPRESSION_GROUPS = EXPRESSION_GROUPS
# general: 外見・小物などの一般タグ（tag_dictionary.json の "general"）
# コード内には既定値を持たない（JSONが無いときは空）
GENERAL_TAG_MAP = ()
# atmosphere: 泡・紅潮・濡れた肌・湯気などの雰囲気タグ（tag_dictionary.json の "atmosphere"）
ATMOSPHERE_TAG_MAP = ()


def _valid_tag_rows(rows):
    """[[keys...], [tags...]] の配列か検証する"""
    if not isinstance(rows, list):
        return False
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) != 2:
            return False
        if not isinstance(r[0], (list, tuple)) or not isinstance(r[1], (list, tuple)):
            return False
        if not r[0] or not r[1]:
            return False
        if not all(isinstance(x, str) and x.strip() for x in r[0]):
            return False
        if not all(isinstance(x, str) and x.strip() for x in r[1]):
            return False
    return True


def _load_tag_dictionary(path=None):
    """tag_dictionary.json を読み込む。壊れていれば None を返す（起動は止めない）。"""
    p = path or TAG_DICT_PATH
    try:
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        for k in ("pose", "prop", "item", "expression", "camera"):
            if not _valid_tag_rows(d.get(k)):
                return None
        if d.get("general") is not None and not _valid_tag_rows(d.get("general")):
            return None
        if d.get("atmosphere") is not None and not _valid_tag_rows(d.get("atmosphere")):
            return None
        rows = d.get("place")
        if not isinstance(rows, list):
            return None
        for r in rows:
            if not isinstance(r, (list, tuple)) or len(r) != 3:
                return None
            if not isinstance(r[0], (list, tuple)) or not isinstance(r[1], (list, tuple)):
                return None
            if not r[0] or not r[1]:
                return None
            if not all(isinstance(x, str) and x.strip() for x in r[0]):
                return None
            if not all(isinstance(x, str) and x.strip() for x in r[1]):
                return None
            if r[2] not in ("", "in", "out"):
                return None
        for k in ("pose_aliases", "prop_aliases"):
            if not isinstance(d.get(k), dict):
                return None
        for k in ("posture_conflict_tags", "place_never_strip", "camera_angle_tags", "camera_framing_tags"):
            if not isinstance(d.get(k), list):
                return None
        if not isinstance(d.get("place_compatible"), list):
            return None
        if not isinstance(d.get("expression_groups"), list):
            return None
        return d
    except Exception as e:
        print(f"[tag_dictionary] 読み込み失敗: {e}（コード内の辞書を使います）", file=sys.stderr)
        return None


def _apply_tag_dictionary(d):
    """読み込んだJSONをモジュール変数へ反映し、派生集合も再計算する"""
    global POSE_TAG_MAP, POSE_TAG_ALIASES, POSE_SORT_TAGS
    global PROP_TAG_MAP, PROP_TAG_ALIASES, ITEM_TAG_MAP
    global EXPRESSION_TAG_MAP, PLACE_TAG_MAP, CAMERA_TAG_MAP
    global POSTURE_CONFLICT_TAGS, PLACE_NEVER_STRIP, PLACE_COMPATIBLE
    global PLACE_SORT_TAGS, CAMERA_ANGLE_TAGS, CAMERA_FRAMING_TAGS, CAMERA_SORT_TAGS
    global EXPRESSION_GROUPS, GENERAL_TAG_MAP, ATMOSPHERE_TAG_MAP
    if not d:
        return False
    POSE_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d["pose"])
    POSE_TAG_ALIASES = dict(d["pose_aliases"])
    POSE_SORT_TAGS = {t for _, tags in POSE_TAG_MAP for t in tags} | set(POSE_TAG_ALIASES.values())
    PROP_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d["prop"])
    PROP_TAG_ALIASES = dict(d["prop_aliases"])
    ITEM_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d["item"])
    EXPRESSION_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d["expression"])
    PLACE_TAG_MAP = tuple((tuple(r[0]), tuple(r[1]), r[2]) for r in d["place"])
    CAMERA_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d["camera"])
    POSTURE_CONFLICT_TAGS = set(d["posture_conflict_tags"])
    PLACE_NEVER_STRIP = set(d["place_never_strip"])
    PLACE_COMPATIBLE = {frozenset(x) for x in d["place_compatible"]}
    PLACE_SORT_TAGS = {t for _, tags, _ in PLACE_TAG_MAP for t in tags} | {"indoors", "outdoors"}
    CAMERA_ANGLE_TAGS = set(d["camera_angle_tags"])
    CAMERA_FRAMING_TAGS = set(d["camera_framing_tags"])
    CAMERA_SORT_TAGS = ({t for _, tags in CAMERA_TAG_MAP for t in tags} | CAMERA_ANGLE_TAGS | CAMERA_FRAMING_TAGS) - {"pov"}
    EXPRESSION_GROUPS = tuple(frozenset(g) for g in d["expression_groups"])
    GENERAL_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d.get("general") or ())
    ATMOSPHERE_TAG_MAP = tuple((tuple(r[0]), tuple(r[1])) for r in d.get("atmosphere") or ())
    return True


def reload_tag_dictionary():
    """JSONを読み直す。成功したら True（失敗時は現在の辞書のまま）。"""
    d = _load_tag_dictionary()
    if not d:
        return False
    with _tag_dict_lock:
        _apply_tag_dictionary(d)
    return True


def _tag_dict_mtime_now():
    try:
        return os.path.getmtime(TAG_DICT_PATH)
    except OSError:
        return 0.0


def maybe_reload_tag_dictionary():
    """JSONが更新されていれば読み直す（起動中の編集を反映）。"""
    global _tag_dict_mtime
    m = _tag_dict_mtime_now()
    if m and m != _tag_dict_mtime:
        _tag_dict_mtime = m
        reload_tag_dictionary()


_TAG_DICT_LOADED = reload_tag_dictionary()
_tag_dict_mtime = _tag_dict_mtime_now()


def iter_state_chars(state):
    if isinstance(state, dict):
        if isinstance(state.get("name"), str) or any(k in state for k in ("pose", "expression", "place", "camera", "outfit", "equipped_items", "penetration", "body_fluids", "situation", "inner")):
            return [state]
        return [dict(v, name=k) if isinstance(v, dict) and "name" not in v else v for k, v in state.items() if isinstance(v, dict)]
    if isinstance(state, list):
        return [v for v in state if isinstance(v, dict)]
    return []


def state_values(chars, key):
    """文字列 or 文字列配列を、空を除いて平坦化する"""
    out = []
    for c in chars:
        v = c.get(key)
        if isinstance(v, (list, tuple)):
            out += [x.strip() for x in v if isinstance(x, str) and x.strip()]
        elif isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def first_state_value(chars, key):
    """先頭優先。視点キャラ（先頭）の値を採用し、無ければ最初の非空"""
    for c in chars:
        v = c.get(key)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str) and v.strip():
            return v.strip()
        elif isinstance(v, (list, tuple)):
            s = " ".join(str(x).strip() for x in v if isinstance(x, str) and str(x).strip())
            if s:
                return s
    return ""


def iter_turn_chars(state_turn):
    """そのターンの差分（{"pose":..,"expression":[..]} でも [{name,pose,..}] でも可）"""
    if not state_turn:
        return []
    if isinstance(state_turn, dict):
        if any(k in state_turn for k in ("pose", "expression")):
            return [state_turn]
        return [dict(v, name=k) if isinstance(v, dict) and "name" not in v else v for k, v in state_turn.items() if isinstance(v, dict)]
    if isinstance(state_turn, list):
        return [v for v in state_turn if isinstance(v, dict)]
    return []


def turn_by_name(state_turn):
    out = {}
    for c in iter_turn_chars(state_turn):
        k = char_key(c.get("name") or "")
        if k:
            out[k] = c
        elif "__anon__" not in out:
            out["__anon__"] = c
    return out


def image_person_count(tags):
    n = 0
    for t in tags:
        nt = normalize_tag(t)
        m = re.match(r"^(\d+)\+?\s*(?:girl|boy|other)s?$", nt)
        if m:
            n = max(n, int(m.group(1)))
        elif nt in ("multiple girls", "multiple boys", "multiple others"):
            n = max(n, 2)
    return n


def parse_ascii_tags(text, field=None):
    if not text or not isinstance(text, str):
        return []
    parts = re.split(r"[,、;，\n]+", text)
    valid_tags = []
    for p in parts:
        p_str = p.strip()
        if not p_str:
            continue
        nt = normalize_tag(p_str)
        if (
            nt
            and re.match(r"^[a-z0-9][a-z0-9 \-()]*$", nt)
            and len(nt.split()) <= 4
            and not re.sub(r"[\s\-()]+", "", nt).isdigit()
        ):
            valid_tags.append(nt)
        else:
            if field:
                log_state_tag_miss(field, p_str)
    return valid_tags


def lookup_state_tag(text, map_tuple):
    if not text or not isinstance(text, str):
        return None
    norm = normalize_pose_keyword_text(text)
    if not norm:
        return None

    if norm == "城":
        for entry in map_tuple:
            if "castle" in entry[1]:
                return entry
    elif norm == "森":
        for entry in map_tuple:
            if "forest" in entry[1]:
                return entry
    elif norm == "海":
        for entry in map_tuple:
            if "beach" in entry[1]:
                return entry
    elif norm == "車":
        for entry in map_tuple:
            if "car" in entry[1]:
                return entry

    candidates = []
    for entry in map_tuple:
        keys = entry[0]
        for k in keys:
            k_norm = normalize_pose_keyword_text(k)
            if k_norm:
                candidates.append((k_norm, entry))

    candidates.sort(key=lambda x: len(x[0]), reverse=True)

    for k_norm, entry in candidates:
        if k_norm in norm:
            return entry
    return None


def _state_without_pose(st):
    if not st:
        return ""
    if isinstance(st, list):
        filtered = [{k: v for k, v in c.items() if k != "pose"} if isinstance(c, dict) else c for c in st]
        return json.dumps(filtered, ensure_ascii=False)
    if isinstance(st, dict):
        filtered = {k: v for k, v in st.items() if k != "pose"}
        return json.dumps(filtered, ensure_ascii=False)
    return ""


def is_censor_related_tag(tag_str):
    if not tag_str or not isinstance(tag_str, str):
        return False
    norm = normalize_tag(tag_str)
    norm_space = norm.replace("-", " ")
    return norm in CENSOR_RELATED_TAGS or norm_space in CENSOR_RELATED_TAGS


def is_prohibited_escape_tag(tag_str):
    if not tag_str or not isinstance(tag_str, str):
        return False
    norm = normalize_tag(tag_str)
    norm_space = norm.replace("-", " ")
    return norm in PROHIBITED_ESCAPE_TAGS or norm_space in PROHIBITED_ESCAPE_TAGS or is_censor_related_tag(tag_str)


def sanitize_prompt(raw_prompt, context_text=None, state=None, allow_inject=True, state_turn=None):
    maybe_reload_tag_dictionary()
    if not raw_prompt or not isinstance(raw_prompt, str):
        return ""
    text = raw_prompt.strip()

    m_code = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
    if m_code:
        inner = m_code.group(1).strip()
        try:
            parsed = json.loads(inner)
            if isinstance(parsed, dict):
                text = parsed.get("refined_prompt") or parsed.get("prompt") or parsed.get("text") or inner
            elif isinstance(parsed, str):
                text = parsed
            else:
                text = inner
        except Exception:
            text = inner
    elif text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                text = parsed.get("refined_prompt") or parsed.get("prompt") or parsed.get("text") or text
        except Exception:
            pass

    # 前置きフレーズ（「Here is the refined prompt:」等）を行頭・テキスト先頭から除去
    text = re.sub(
        r"^(?:Here\s+(?:is|are)(?:\s+the)?|Sure[!,.]?|Refined\s+prompt|Prompt|Tags|Output|Result|Here\'s\s+the\s+refined\s+prompt)[^:\n]*:\s*",
        "",
        text,
        flags=re.I
    )

    cleaned_lines = []
    for line in text.splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        if line_s.startswith(("#", "//", "/*", "Note:", "Explanation:", "prompt:", "tags:", "Tags:", "Prompt:")):
            continue
        cleaned_lines.append(line_s)

    joined = ", ".join(cleaned_lines)
    raw_tags = [t.strip() for t in re.split(r"[,、，;\n]+", joined) if t.strip()]
    sentence_starters = {"i", "we", "you", "it", "this", "here", "note", "please"}
    sentence_markers = {"because", "according", "following"}

    candidate_tags = []
    for tag in raw_tags:
        tag = tag.strip("\"'` ")
        tag = re.sub(r"[\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]", "", tag).strip()
        if not tag:
            continue
        tag = tag.replace("**", "").replace("*", "")
        # リスト番号（1. 1) (1)）やビュレット記号（- * ・ •）を確実に剥離
        tag = re.sub(r"^(?:\d+[\.\)]\s*|[-・*•]\s*|\(\d+\)\s*)", "", tag).strip()
        if not re.search(r"[a-zA-Z0-9]", tag):
            continue
        tag = re.sub(r"[^a-zA-Z0-9\s_\-:\(\)\[\]\{\}\<\>\.\+\/]", "", tag)
        tag = re.sub(r"\s+", " ", tag).strip()
        tag = tag.rstrip(":,;")
        if not tag:
            continue
        words = [w.lower() for w in tag.split()]
        if len(words) >= 4 and (tag.endswith(".") or words[0] in sentence_starters or any(w in sentence_markers for w in words)):
            continue
        if len(words) > 5:
            continue
        if not re.search(r"[a-zA-Z0-9]", tag):
            continue
        candidate_tags.append(tag)

    # --- 対策3: サーバー側セーフティネット判定（行為示唆タグ・文脈の検出）---
    norm_tag_set = {normalize_tag(t) for t in candidate_tags}
    norm_tag_set.discard("")
    norm_tag_set_expanded = set(norm_tag_set) | {t.replace("-", " ") for t in norm_tag_set}

    # --- ポーズ・小道具指定の英語タグ化 ---
    # 造語タグ（i-balance, anal jelly 等）を実在タグに置換する
    for i, tag in enumerate(candidate_tags):
        norm = normalize_tag(tag)
        alias = POSE_TAG_ALIASES.get(norm) or PROP_TAG_ALIASES.get(norm)
        if alias:
            candidate_tags[i] = alias
    norm_tag_set = {normalize_tag(t) for t in candidate_tags}
    norm_tag_set.discard("")
    norm_tag_set_expanded = set(norm_tag_set) | {t.replace("-", " ") for t in norm_tag_set}

    # 日本語・全角で指定されたポーズを実在タグに変換して補完する（日本語タグはこの後削除されるため）
    pose_source = normalize_pose_keyword_text(
        f"{raw_prompt} {context_text or ''} {_state_without_pose(state)}"
    )
    injected_pose_tags = []
    injected_prop_tags = []
    injected_general_tags = []
    if allow_inject:
        # 長いキーを優先する。例:「膝立ち」が「立ち」にも一致して standing が
        # 混入するのを防ぐ（長いキーの一部になっている短いキーは無視する）。
        _raw_pose_src = normalize_pose_keyword_text(raw_prompt or "")
        _pose_matches = []
        for keys, tags in POSE_TAG_MAP:
            best = None
            for k in keys:
                p = pose_source.rfind(k)
                if p < 0:
                    continue
                if best is None or p > best[0] or (p == best[0] and len(k) > len(best[1])):
                    best = (p, k)
            if best:
                # raw_prompt に明示されたポーズは AI の最終指定なので常に残す
                _pose_matches.append((best[0], best[1], tags, best[1] in _raw_pose_src))
        # 長いキー優先（「膝立ち」が「立ち」にも一致するのを防ぐ）
        _pose_keys = {k for _, k, _, _ in _pose_matches}
        _pose_matches = [
            (p, k, tags, explicit) for p, k, tags, explicit in _pose_matches
            if not any(k != o and k in o for o in _pose_keys)
        ]
        # 姿勢競合は「最後に出現したもの」だけ残す。
        # 例:「四つん這いから身体を起こしてしゃがみこむ」→ squatting を優先。
        # raw_prompt に明示されたポーズは対象外（AI の最終指定を尊重する）。
        _conf_pos = [
            p for p, _, tags, explicit in _pose_matches
            if not explicit and any(t in POSTURE_CONFLICT_TAGS for t in tags)
        ]
        _last_conf = max(_conf_pos) if _conf_pos else None
        for _p, _k, _tags, _explicit in _pose_matches:
            if (not _explicit) and _last_conf is not None and _p != _last_conf and any(
                t in POSTURE_CONFLICT_TAGS for t in _tags
            ):
                _tags = tuple(t for t in _tags if t not in POSTURE_CONFLICT_TAGS)
            injected_pose_tags += list(_tags)
        for keys, tags in PROP_TAG_MAP:
            if any(k in pose_source for k in keys):
                injected_prop_tags += list(tags)
        # general（外見・小物などの一般タグ）。JSONに無ければ空。
        # 場所・カメラ・ポーズの誤爆を避けるため、装い系の項目だけを走査する。
        _gchars = iter_state_chars(state)
        _gparts = []
        for _k in ("outfit", "outerwear", "underwear", "equipped_items", "body_fluids"):
            _gparts.extend(state_values(_gchars, _k))
        general_source = normalize_pose_keyword_text(" ".join(_gparts))
        for keys, tags in GENERAL_TAG_MAP:
            if any(k in general_source for k in keys):
                injected_general_tags += list(tags)
        # atmosphere（泡・紅潮・濡れた肌・湯気・涙・涎・喘ぎ等）は body_fluids と situation から
        _aparts = []
        for _k in ("body_fluids", "situation"):
            _aparts.extend(state_values(_gchars, _k))
        atmosphere_source = normalize_pose_keyword_text(" ".join(_aparts))
        for keys, tags in ATMOSPHERE_TAG_MAP:
            if any(k in atmosphere_source for k in keys):
                injected_general_tags += list(tags)

    # state スキーマ v2: pose, expression, place, camera 処理
    chars = iter_state_chars(state) if allow_inject else []
    raw_penis_present = first_state_value(chars, "penis_present") if allow_inject else ""
    penis_present = None
    if raw_penis_present:
        _pp = str(raw_penis_present).strip().lower()
        if _pp in ("true", "yes", "1", "あり", "はい"):
            penis_present = True
        elif _pp in ("false", "no", "0", "なし", "いいえ"):
            penis_present = False
    turns = turn_by_name(state_turn) if allow_inject else {}
    n_persons = image_person_count(candidate_tags)
    is_single = (n_persons <= 1)

    turn_poses = []
    turn_exprs = []
    if allow_inject:
        if is_single:
            view_char = chars[0] if chars else None
            view_k = char_key(view_char.get("name") or "") if view_char else ""
            tc = turns.get(view_k) or (turns.get("__anon__") if len(turns) == 1 else None)
            if not tc and not chars and turns:
                tc = turns.get("__anon__") or list(turns.values())[0]
            if tc:
                p = tc.get("pose")
                if isinstance(p, (list, tuple)):
                    _plist = [str(x).strip() for x in p if isinstance(x, str) and str(x).strip()]
                    if _plist:
                        turn_poses.append((view_k or "__anon__", _plist))
                elif p and isinstance(p, str) and p.strip():
                    turn_poses.append((view_k or "__anon__", p.strip()))
                e = tc.get("expression")
                if isinstance(e, (list, tuple)):
                    turn_exprs.extend([x.strip() for x in e if isinstance(x, str) and x.strip()])
                elif isinstance(e, str) and e.strip():
                    turn_exprs.extend([x.strip() for x in re.split(r"[\n,、]+", e) if x.strip()])
        else:
            for c in chars:
                k = char_key(c.get("name") or "")
                tc = turns.get(k) or (turns.get("__anon__") if len(turns) == 1 else None)
                if tc:
                    p = tc.get("pose")
                    if isinstance(p, (list, tuple)):
                        _plist = [str(x).strip() for x in p if isinstance(x, str) and str(x).strip()]
                        if _plist:
                            turn_poses.append((k or "__anon__", _plist))
                    elif p and isinstance(p, str) and p.strip():
                        turn_poses.append((k or "__anon__", p.strip()))
                    e = tc.get("expression")
                    if isinstance(e, (list, tuple)):
                        turn_exprs.extend([x.strip() for x in e if isinstance(x, str) and x.strip()])
                    elif isinstance(e, str) and e.strip():
                        turn_exprs.extend([x.strip() for x in re.split(r"[\n,、]+", e) if x.strip()])
            if not chars and turns:
                for tc in turns.values():
                    p = tc.get("pose")
                    if isinstance(p, (list, tuple)):
                        _plist = [str(x).strip() for x in p if isinstance(x, str) and str(x).strip()]
                        if _plist:
                            turn_poses.append(("__anon__", _plist))
                    elif p and isinstance(p, str) and p.strip():
                        turn_poses.append(("__anon__", p.strip()))
                    e = tc.get("expression")
                    if isinstance(e, (list, tuple)):
                        turn_exprs.extend([x.strip() for x in e if isinstance(x, str) and x.strip()])
                    elif isinstance(e, str) and e.strip():
                        turn_exprs.extend([x.strip() for x in re.split(r"[\n,、]+", e) if x.strip()])

    turn_pose_tags = []
    if allow_inject and turn_poses:
        _posture_added_by_char = {}
        for _char, pval in turn_poses:
            if isinstance(pval, (list, tuple)):
                # タグ配列は AI の最終指定なので、そのまま全部使う（lying + on back 等）
                for _part in pval:
                    if not isinstance(_part, str) or not _part.strip():
                        continue
                    _part = _part.strip()
                    pmatch = lookup_state_tag(_part, POSE_TAG_MAP)
                    if pmatch:
                        turn_pose_tags.extend(list(pmatch[1]))
                    else:
                        turn_pose_tags.extend(parse_ascii_tags(_part, field="pose"))
                continue
            # 「仰向け, 開脚」のような複数指定に対応
            for _part in re.split(r"[,、]+", pval):
                _part = _part.strip()
                if not _part:
                    continue
                pmatch = lookup_state_tag(_part, POSE_TAG_MAP)
                if pmatch:
                    _tags = list(pmatch[1])
                else:
                    # pose 欄に行為（例: おっぱいを揉まれている）が入る場合がある。
                    # その場合は prop 辞書で拾って小道具・行為タグとして注入する。
                    pmatch2 = lookup_state_tag(_part, PROP_TAG_MAP)
                    if pmatch2:
                        injected_prop_tags.extend(list(pmatch2[1]))
                    _tags = parse_ascii_tags(_part, field="pose")
                if any(t in POSTURE_CONFLICT_TAGS for t in _tags):
                    if _posture_added_by_char.get(_char):
                        # 姿勢はキャラごとに最初の1つだけ。2つ目以降は付加タグのみ残す。
                        _tags = [t for t in _tags if t not in POSTURE_CONFLICT_TAGS]
                    else:
                        _posture_added_by_char[_char] = True
                turn_pose_tags.extend(_tags)
        if turn_pose_tags:
            injected_pose_tags = [t for t in injected_pose_tags if normalize_tag(t) not in POSTURE_CONFLICT_TAGS] + turn_pose_tags

    turn_expr_tags = []
    if allow_inject and turn_exprs:
        for eval_ in turn_exprs:
            for _part in re.split(r"[,、]+", eval_):
                _part = _part.strip()
                if not _part:
                    continue
                ematch = lookup_state_tag(_part, EXPRESSION_TAG_MAP)
                if ematch:
                    turn_expr_tags.extend(list(ematch[1]))
                else:
                    atags = parse_ascii_tags(_part, field="expression")
                    if atags:
                        turn_expr_tags.extend(atags)
    # expression が未出力でも situation の表情描写から補完する
    if allow_inject:
        _sit_expr = " ".join(state_values(chars, "situation"))
        if _sit_expr:
            for _keys, _tags in EXPRESSION_TAG_MAP:
                if any(k in _sit_expr for k in _keys):
                    turn_expr_tags.extend(list(_tags))

    injected_place_tags = []
    place_category = ""
    _bg_tags = []
    if allow_inject:
        for _c in chars:
            _v = _c.get("background")
            if isinstance(_v, (list, tuple)):
                _bg_tags.extend([str(x).strip() for x in _v if isinstance(x, str) and str(x).strip()])
            elif isinstance(_v, str) and _v.strip():
                _bg_tags.extend([x.strip() for x in re.split(r"[,、]+", _v) if x.strip()])
    if _bg_tags:
        injected_place_tags = list(dict.fromkeys(_bg_tags))
        _pl_norms = {normalize_tag(t) for t in injected_place_tags}
        if "outdoors" in _pl_norms:
            place_category = "out"
        elif "indoors" in _pl_norms:
            place_category = "in"
        else:
            place_category = ""
    raw_place = first_state_value(chars, "place") if allow_inject and not injected_place_tags else ""
    if raw_place:
        plmatch = lookup_state_tag(raw_place, PLACE_TAG_MAP)
        if plmatch:
            injected_place_tags = list(plmatch[1])
            place_category = plmatch[2]
        else:
            atags = parse_ascii_tags(raw_place, field="place")
            if atags:
                injected_place_tags = atags
                place_category = ""
    # place が未出力のターンでも situation から場所を推定する
    if allow_inject and not injected_place_tags:
        _sit_text = " ".join(state_values(chars, "situation"))
        if _sit_text:
            plmatch = lookup_state_tag(_sit_text, PLACE_TAG_MAP)
            if plmatch:
                injected_place_tags = list(plmatch[1])
                place_category = plmatch[2]

    injected_camera_tags = []
    _cam_vals = state_values(chars, "camera") if allow_inject else []
    raw_camera = ", ".join(_cam_vals)
    if raw_camera:
        # 「pov, from behind, ass focus」「主観、後ろから」のような複数指定に対応
        for _part in re.split(r"[,、]+", raw_camera):
            _part = _part.strip()
            if not _part:
                continue
            cmatch = lookup_state_tag(_part, CAMERA_TAG_MAP)
            if cmatch:
                injected_camera_tags += list(cmatch[1])
            else:
                injected_camera_tags += parse_ascii_tags(_part, field="camera")
    # camera が未出力でも situation から構図を推定する
    if allow_inject and not injected_camera_tags:
        _sit_cam = " ".join(state_values(chars, "situation"))
        if _sit_cam:
            cmatch = lookup_state_tag(_sit_cam, CAMERA_TAG_MAP)
            if cmatch:
                injected_camera_tags = list(cmatch[1])

    strip_tags = set()
    if allow_inject:
        # 1. ポーズ競合除去（1人画像のみ・state のポーズが姿勢を指定している場合だけ）
        #    state_turn が無いターンでも situation 由来の injected_pose_tags で競合を解消する
        _posture_src = turn_pose_tags or injected_pose_tags
        if is_single and any(t in POSTURE_CONFLICT_TAGS for t in _posture_src):
            for t in POSTURE_CONFLICT_TAGS:
                if t not in _posture_src:
                    strip_tags.add(t)

        # 2. 場所競合除去（屋内/屋外タグは場所カテゴリから補完する）
        if injected_place_tags:
            if place_category == "in":
                strip_tags.add("outdoors")
                if "indoors" not in injected_place_tags:
                    injected_place_tags.append("indoors")
            elif place_category == "out":
                strip_tags.add("indoors")
                if "outdoors" not in injected_place_tags:
                    injected_place_tags.append("outdoors")
            injected_place_norms = {normalize_tag(t) for t in injected_place_tags}
            if injected_place_norms - {"indoors", "outdoors"}:
                for t in candidate_tags:
                    norm_t = normalize_tag(t)
                    if norm_t in PLACE_SORT_TAGS and norm_t not in ("indoors", "outdoors"):
                        if norm_t in PLACE_NEVER_STRIP:
                            continue
                        if any(frozenset({p, norm_t}) in PLACE_COMPATIBLE for p in injected_place_norms):
                            continue
                        if norm_t in injected_place_norms:
                            continue
                        strip_tags.add(norm_t)

        # 3. カメラ競合除去
        if injected_camera_tags:
            injected_cam_norms = {normalize_tag(t) for t in injected_camera_tags}
            if injected_cam_norms & CAMERA_ANGLE_TAGS:
                for t in CAMERA_ANGLE_TAGS:
                    if t not in injected_cam_norms:
                        strip_tags.add(t)
            if injected_cam_norms & CAMERA_FRAMING_TAGS:
                for t in CAMERA_FRAMING_TAGS:
                    if t not in injected_cam_norms:
                        strip_tags.add(t)

    injected_expr_tags = []
    if turn_expr_tags:
        current_tag_norms = {normalize_tag(t) for t in candidate_tags if normalize_tag(t) not in strip_tags}
        for etag in turn_expr_tags:
            norm_e = normalize_tag(etag)
            grp = None
            for g in EXPRESSION_GROUPS:
                if norm_e in g:
                    grp = g
                    break
            if grp and (grp & current_tag_norms):
                continue
            if norm_e in current_tag_norms:
                continue
            injected_expr_tags.append(etag)
            current_tag_norms.add(norm_e)

    has_penis = any(normalize_tag(t) == "penis" for t in candidate_tags) and penis_present is not False
    has_fellatio = any(normalize_tag(t) in ("fellatio", "deepthroat", "blowjob", "irrumatio") for t in candidate_tags)
    has_nipples = any(normalize_tag(t) in ("nipples", "erect nipples", "puffy nipples") for t in candidate_tags)
    has_explicit_oral_tags = any(normalize_tag(t) in ("fellatio", "deepthroat", "blowjob", "irrumatio") for t in candidate_tags)

    oral_direct_escapes = {
        "hands on user thighs", "hands on users thighs",
        "hands on user legs", "hands on users legs",
        "hand on user thighs", "hand on users thighs",
        "hand on user thigh", "hands on user thigh",
        "hand on users thigh", "hands on users thigh",
        "hands on partner thighs", "hands on partners thighs"
    }

    # 1. 直接的な検閲逃避タグの検出
    has_oral_escape_tags = bool(norm_tag_set_expanded & oral_direct_escapes)

    # 2. state の penetration / equipped_items 解析（list, dict 両対応）
    state_pen_parts = []
    state_item_values = []

    def _collect_items(ch):
        eq = ch.get("equipped_items")
        if isinstance(eq, (list, tuple)):
            state_item_values.extend(str(x).strip() for x in eq if x and str(x).strip())
        elif eq and str(eq).strip():
            state_item_values.append(str(eq).strip())

    if state:
        if isinstance(state, dict):
            if "penetration" in state and state["penetration"]:
                state_pen_parts.append(str(state["penetration"]))
            _collect_items(state)
            items = list(state.values())
        elif isinstance(state, list):
            items = state
        else:
            items = []
        for it in items:
            if isinstance(it, dict):
                pen = it.get("penetration")
                if pen:
                    state_pen_parts.append(str(pen))
                if it is not state:
                    _collect_items(it)
            elif isinstance(it, str) and it:
                state_pen_parts.append(it)
    state_pen_str = " ".join(state_pen_parts)
    _action_tags = state_values(chars, "action") if allow_inject else []
    if _action_tags:
        state_pen_parts.extend(_action_tags)
        state_pen_str = " ".join(state_pen_parts)
        injected_prop_tags.extend(_action_tags)
    state_item_str = " ".join(state_item_values)

    # equipped_items の物は、AI がプロンプトに書き忘れても実在タグで補完する
    injected_item_tags = []
    if allow_inject:
        unmatched_items = []
        for item in state_item_values:
            item_norm = normalize_pose_keyword_text(item)
            matched = False
            for keys, tags in ITEM_TAG_MAP:
                if any(k in item_norm for k in keys):
                    injected_item_tags += list(tags)
                    matched = True
            if matched:
                continue
            ascii_tag = re.sub(r"[^a-zA-Z0-9 _\-]", "", item).strip().lower()
            if ascii_tag and ascii_tag == item.strip().lower():
                injected_item_tags.append(ascii_tag)
            else:
                unmatched_items.append(item.strip())
        if unmatched_items:
            log_item_tag_miss(unmatched_items)

    # 3. 行為の検出（タグと state の penetration 表現の両方を参照）
    has_tag_other_sex_act = any(
        normalize_tag(t) in ("vaginal penetration", "anal penetration", "penetration", "paizuri", "handjob", "cunnilingus", "fingering")
        for t in candidate_tags
    )
    # 「結合中」「挿入中」だけでは肛門/膣を区別できないため、場所＋行為の詳細表現を
    # 優先する。体位名（正常位等）だけでは判定しない。
    state_anal_markers = (
        "アナル結合", "アナル挿入", "アナル性交", "アナルセックス", "アナルファック",
        "肛門挿入", "肛門結合", "尻穴挿入", "アヌス挿入", "直腸挿入",
    )
    state_vaginal_markers = (
        "膣挿入", "膣内挿入", "膣結合", "膣内結合", "膣内射精",
        "おまんこ挿入", "マンコ挿入", "おまんこ中出し",
        # 体位名は膣挿入の明示（アナル体位は anal 側の詳細表現を優先）
        "正常位", "後背位", "騎乗位", "対面座位", "背面座位", "側位", "屈曲位", "立位",
    )
    state_oral_markers = (
        "奉仕中", "フェラ", "口淫", "咥え", "しゃぶ", "お掃除", "口内", "イラマ",
        "喉奥", "のど奥", "口にペニス", "お口にペニス",
    )
    # penetration フィールドが「結合中」のような曖昧表現でも、本文・situation の
    # 「お尻の奥に挿入」「アヌスに注ぎ込む」等から肛門/膣を判定できるようにする
    _sit_parts = []
    for _c in (chars if allow_inject else []):
        _sit_parts.extend(state_values([_c], "situation"))
    pen_context = f"{state_pen_str} {raw_prompt or ''} {context_text or ''} {' '.join(_sit_parts)}"
    _ANAL_PEN_RE = re.compile(
        r"(お尻の奥|ケツの奥|尻の奥|アナル|肛門|尻穴|お尻|ケツ|尻|アヌス|anal)"
        r".{0,16}"
        r"(挿入|貫|突か|突き入|突いて|突く|ピストン|種付け|中出し|注ぎ込|犯|結合|打ち付|射精)"
    )
    _VAG_PEN_RE = re.compile(
        r"(膣内|膣|おまんこ|マンコ|前の穴|vagina)"
        r".{0,16}"
        r"(挿入|貫|突か|突き入|突いて|突く|ピストン|種付け|中出し|注ぎ込|犯|結合|打ち付|射精)"
    )
    _ORAL_PEN_RE = re.compile(
        r"(口内|お口に|口に|喉奥|のど奥)"
        r".{0,12}"
        r"(挿入|咥え|しゃぶ|突っ込|イラマ|射精|出す)"
    )
    # プラグ・バイブ等の「物の挿入」は性交とみなさない（penis を勝手に補完しない）。
    # ただし「お尻を貫かれながらボトル挿入」のように男性器と物体が併存する場合は、
    # 貫く/突く/ピストン等の男性器示唆語があれば肛門/膣の性交判定を維持する。
    _PENIS_CONTEXT_WORDS = (
        "チンポ", "ちんぽ", "ちんこ", "ペニス", "肉棒", "おちんぽ", "おちんちん",
        "男根", "陰茎", "penis", "cock", "dick",
        "貫かれ", "貫いて", "貫く", "突かれ", "突いて", "突く", "ピストン", "結合",
    )
    has_object_penetration = any(k in pen_context for k in OBJECT_INSERTION_MARKERS)
    has_penis_context = any(w in pen_context for w in _PENIS_CONTEXT_WORDS)
    has_state_anal = (
        any(k in state_pen_str for k in state_anal_markers)
        or bool(_ANAL_PEN_RE.search(pen_context))
    ) and (not has_object_penetration or has_penis_context)
    has_state_vaginal = (
        any(k in state_pen_str for k in state_vaginal_markers)
        or bool(_VAG_PEN_RE.search(pen_context))
    ) and not has_state_anal and (not has_object_penetration or has_penis_context)
    has_state_oral = (
        any(k in state_pen_str for k in state_oral_markers)
        or bool(_ORAL_PEN_RE.search(pen_context))
    ) and not has_state_anal and not has_state_vaginal
    _action_norms = {normalize_tag(t) for t in _action_tags}
    if "anal penetration" in _action_norms:
        has_state_anal = True
    if "vaginal penetration" in _action_norms:
        has_state_vaginal = True
    if _action_norms & {"fellatio", "deepthroat", "irrumatio", "oral"}:
        has_state_oral = True
    # 「結合中」「挿入中」は肛門/膣を特定できないが、口淫ではない性行為ではある。
    # 膣/肛門タグの補完には使わず、口淫タグの抑止だけに使う。
    has_state_penetration_generic = (
        any(k in state_pen_str for k in ("結合中", "挿入中", "性交中", "セックス中"))
        and not has_state_oral
    )
    has_other_sex_act = (
        has_tag_other_sex_act or has_state_vaginal or has_state_anal
        or has_state_penetration_generic
    )

    # 4. 会話文脈の口淫キーワード検出
    ctx_str = f"{context_text or ''} {json.dumps(state, ensure_ascii=False) if state else ''}"
    has_context_keywords = any(w in ctx_str for w in ORAL_ACT_KEYWORDS)
    has_ambiguous_keywords = any(w in ctx_str for w in ORAL_AMBIGUOUS_KEYWORDS)

    # 露出（乳首）検出: 本文・state で胸の露出が明示されていれば乳首タグを補完する
    exposure_text = f"{context_text or ''} {state_pen_str} {json.dumps(state, ensure_ascii=False) if state else ''}"
    nipple_markers = ("乳首", "乳輪", "nipple", "トップレス", "topless", "はだけ", "はだけた", "胸が露出", "胸を露出", "ブラなし", "ノーブラ", "全裸", "裸の胸", "露出した胸", "乳を露", "おっぱいを出", "胸を出")
    expose_nipples = allow_inject and any(k in exposure_text for k in nipple_markers)

    # 接吻（kissing）シーンの誤検知保護: 口と口のキス中は口淫シーンとみなさない
    has_kissing = any(normalize_tag(t) in ("kissing", "french kiss", "kiss") for t in candidate_tags)

    is_oral_scene = (
        allow_inject
        and penis_present is not False
        and not has_other_sex_act
        and not has_kissing
        and (
            has_explicit_oral_tags
            or has_state_oral
            or (has_oral_escape_tags and has_context_keywords)
            # 男性器が既に描かれていて、逃げタグ＋曖昧語（咥え等）が揃う場合のみ補完する
            or (has_penis and has_oral_escape_tags and has_ambiguous_keywords)
        )
    )
    # 挿入・結合中は結合タグと penis を補完する（AI が書き忘れても欠落させない）
    is_penetration_scene = allow_inject and (has_state_vaginal or has_state_anal)

    # コアタグ欠落時の自動補完タグリスト
    injected_front_tags = []
    if is_oral_scene and (not has_penis or not has_fellatio):
        if not any(normalize_tag(t) == "pov" for t in candidate_tags):
            injected_front_tags.append("pov")
        if not has_fellatio:
            injected_front_tags.append("fellatio")
        if not has_penis:
            injected_front_tags.append("penis")
    if is_penetration_scene:
        has_vag_pen = any(normalize_tag(t) == "vaginal penetration" for t in candidate_tags)
        has_anal_pen = any(normalize_tag(t) == "anal penetration" for t in candidate_tags)
        if has_state_anal:
            if not has_anal_pen:
                injected_front_tags.append("anal penetration")
            # アナル専用の文脈で AI が書いた膣タグは誤りなので落とす
            if not has_state_vaginal:
                strip_tags.add("vaginal penetration")
                strip_tags.add("cum in pussy")
                injected_prop_tags = [t for t in injected_prop_tags if normalize_tag(t) not in ("vaginal penetration", "cum in pussy")]
                injected_front_tags = [t for t in injected_front_tags if normalize_tag(t) not in ("vaginal penetration", "cum in pussy")]
        elif not has_vag_pen and not has_anal_pen:
            injected_front_tags.append("vaginal penetration")
        if not has_penis:
            injected_front_tags.append("penis")
    # 物の挿入（プラグ・ゼリー等）のシーンでは、物そのもののタグを補完する
    _EXPEL_RE = re.compile(r"(排泄|排出|出す|出し|出した|出し切|押し出|漏ら|放出)")
    has_object_insertion = has_object_penetration and not bool(_EXPEL_RE.search(pen_context))
    if allow_inject and has_object_insertion:
        has_obj_tag = any("object insertion" in normalize_tag(t) for t in candidate_tags)
        anal_context = any(k in f"{state_pen_str} {state_item_str}" for k in ("アナル", "肛門", "尻穴", "anal"))
        if anal_context and not has_obj_tag:
            injected_front_tags.append("anal object insertion")
        elif not anal_context and not has_obj_tag:
            injected_front_tags.append("object insertion")
    if expose_nipples and not has_nipples:
        injected_front_tags.append("erect nipples" if ("乳首" in exposure_text or "乳輪" in exposure_text or "nipple" in exposure_text.lower()) else "nipples")

    # 精液（cum）の自動補完: 射精後のシーンでは大量感のあるタグを自動付与
    cum_text = f"{context_text or ''} {state_pen_str} {json.dumps(state, ensure_ascii=False) if state else ''}"
    cum_markers = ("精液", "ザーメン", "ざーめん", "射精", "中出し", "顔射", "口内射精", "口内発射", "飲精", "ごっくん", "ぶっかけ", "大量の精", "semen", "cum", "ejaculat")
    pre_cum_markers = ("射精前", "射精しそう", "射精寸前", "寸止め", "出さない", "寸前", "出しそう")
    has_cum_evidence = any(k in cum_text for k in cum_markers)
    if has_cum_evidence and any(k in cum_text for k in pre_cum_markers) and "精液" not in cum_text and "semen" not in cum_text.lower():
        has_cum_evidence = False
    has_overflow_tag = any(normalize_tag(t) in ("cum overflow", "excessive cum") for t in candidate_tags)
    if allow_inject and has_cum_evidence and not has_overflow_tag:
        injected_front_tags.append("cum overflow")
        injected_front_tags.append("excessive cum")

    # 愛液（love juice）の自動補完: 濡れ・愛液のシーンでは透明感のあるタグを自動付与
    love_markers = ("愛液", "濡れ", "濡れて", "蜜", "love juice", "pussy juice", "dripping", "びしょびしょ", "あふれ", "溢れ", "潤", "ぬるぬる", "love fluids")
    pre_love_markers = ("乾いて", "濡れていない", "ドライ", "濡れた肌", "濡れ肌", "水濡れ", "濡れた髪", "濡れた髪の毛", "シャワー", "泡")
    explicit_love_markers = ("愛液", "蜜", "love juice", "pussy juice", "びしょびしょ", "ぬるぬる")
    has_explicit_love = any(k in cum_text for k in explicit_love_markers)
    has_love_evidence = has_explicit_love or (
        any(k in cum_text for k in love_markers)
        and not any(k in cum_text for k in pre_love_markers)
    )
    has_love_tag = any(normalize_tag(t) in ("dripping love juice", "transparent love fluids", "love juice", "pussy juice") for t in candidate_tags)
    if allow_inject and has_love_evidence and not has_love_tag:
        injected_front_tags.append("dripping love juice")
        injected_front_tags.append("transparent love fluids")

    # 性行為タグのガード: キス・他の性行為・物体挿入のときは口淫/挿入タグを出さない
    # （prop に追加した性行為の言い換えが、既存の文脈判定を迂回しないようにする）
    if allow_inject and injected_prop_tags:
        _ORAL_TAGS = {"fellatio", "oral", "irrumatio", "deepthroat"}
        _PENETRATION_TAGS = {"anal penetration", "vaginal penetration", "penetration", "penis", "anal sex", "sex"}
        _guarded = []
        for _t in injected_prop_tags:
            _nt = normalize_tag(_t)
            if _nt in _ORAL_TAGS and (has_kissing or has_other_sex_act):
                continue
            if _nt in _PENETRATION_TAGS and has_object_penetration:
                continue
            _guarded.append(_t)
        injected_prop_tags = _guarded

    # 相互作用・性交・口淫シーンでの solo / solo focus タグの排除判定
    drop_solo = is_oral_scene or is_penetration_scene or has_penis or has_fellatio or any(
        normalize_tag(t) in ("vaginal penetration", "anal penetration", "penetration", "paizuri", "handjob", "cunnilingus")
        for t in candidate_tags
    )

    # penis_present=false のときは、可視男性器タグを注入しない
    if penis_present is False:
        injected_front_tags = [t for t in injected_front_tags if normalize_tag(t) not in _PENIS_VISIBLE_TAGS]
        injected_prop_tags = [t for t in injected_prop_tags if normalize_tag(t) not in _PENIS_VISIBLE_TAGS]

    # タグのフィルタリングと重複排除
    cleaned_tags = []
    seen = set()

    # まず補完タグを先頭候補に登録
    for itag in injected_front_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    # ポーズタグ（行為・性器タグの直後に並べる）
    for itag in injected_pose_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    # state の equipped_items 由来の装具タグ
    for itag in injected_item_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    # 小道具・装具・排泄タグ
    for itag in injected_prop_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    for tag in candidate_tags:
        norm = normalize_tag(tag)
        # penis_present=false: 可視男性器タグを落とす
        if penis_present is False and norm in _PENIS_VISIBLE_TAGS:
            continue
        # 禁止逃避タグのストリップ
        if is_prohibited_escape_tag(tag):
            continue
        # state 由来の競合タグ（古いポーズ・場所・構図）のストリップ
        if norm in strip_tags:
            continue
        # 相互作用シーンでの solo 排除
        if drop_solo and norm in ("solo", "solo focus"):
            continue
        # fellatio と oral の重複排除
        if norm == "oral" and ("fellatio" in seen or has_fellatio or "fellatio" in injected_front_tags):
            continue
        # penetration 重複排除
        if norm == "penetration" and any(p in seen or p in norm_tag_set for p in ("vaginal penetration", "anal penetration")):
            continue
        # 擬似詳細タグの標準化
        if norm == "hyper detailed pussy":
            tag = "pussy"
            norm = "pussy"
        elif norm in ("hyper detailed nipples", "hard nipples"):
            tag = "erect nipples"
            norm = "erect nipples"

        if norm in seen:
            continue
        seen.add(norm)
        cleaned_tags.append(tag)

    # 候補ループの後に、seen を見ながら injected_expr_tags → injected_place_tags → injected_camera_tags を追加
    for itag in injected_expr_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    for itag in injected_place_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    for itag in injected_camera_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    # general（外見・小物などの一般タグ）は最後に追加（重複は除外）
    for itag in injected_general_tags:
        itag_norm = normalize_tag(itag)
        if itag_norm not in seen:
            seen.add(itag_norm)
            cleaned_tags.append(itag)

    # 最終ソート：人数タグ → pov → 構図 → コア行為・解剖タグ → ポーズ → 残りのタグ
    count_part = [t for t in cleaned_tags if COUNT_TAG.fullmatch(normalize_tag(t))]
    pov_part = [t for t in cleaned_tags if normalize_tag(t) == "pov" and not COUNT_TAG.fullmatch(normalize_tag(t))]
    camera_part = [
        t for t in cleaned_tags
        if normalize_tag(t) in CAMERA_SORT_TAGS
        and not COUNT_TAG.fullmatch(normalize_tag(t))
        and normalize_tag(t) != "pov"
    ]
    core_part = [
        t for t in cleaned_tags
        if normalize_tag(t) in CORE_ANATOMY_TAGS
        and not COUNT_TAG.fullmatch(normalize_tag(t))
        and normalize_tag(t) != "pov"
        and normalize_tag(t) not in CAMERA_SORT_TAGS
    ]
    pose_part = [
        t for t in cleaned_tags
        if normalize_tag(t) in POSE_SORT_TAGS
        and normalize_tag(t) not in CORE_ANATOMY_TAGS
        and not COUNT_TAG.fullmatch(normalize_tag(t))
        and normalize_tag(t) != "pov"
        and normalize_tag(t) not in CAMERA_SORT_TAGS
    ]
    other_part = [
        t for t in cleaned_tags
        if not COUNT_TAG.fullmatch(normalize_tag(t))
        and normalize_tag(t) != "pov"
        and normalize_tag(t) not in CAMERA_SORT_TAGS
        and normalize_tag(t) not in CORE_ANATOMY_TAGS
        and normalize_tag(t) not in POSE_SORT_TAGS
    ]

    final_order = count_part + pov_part + camera_part + core_part + pose_part + other_part
    deduped = list(dict.fromkeys(final_order))
    return ", ".join(deduped)


def image_refine_output_text(s, m):
    return (
        f"outbox/{s['id']}_<任意>.json に、以下の形式の JSON のみを出力する:\n\n"
        "```json\n"
        "{\n"
        f'  "session": "{s["id"]}",\n'
        f'  "reply_to": "{m["id"]}",\n'
        '  "type": "image_refine",\n'
        '  "prompt": "推敲後のDanbooru英語タグ（カンマ区切り、25〜35個程度）"\n'
        "}\n"
        "```\n\n"
        "※作画仕様および厳守事項:\n"
        "- prompt には英数字・カンマ・アンダースコア・括弧等で構成される Danbooru 英語タグのみをカンマ区切りで記述してください。\n"
        "- 自然言語の解説文（「Here is the prompt:」など）や日本語、思考文、会話文は絶対に含めないでください。\n"
        "- 解剖学的整合性と被写体欠落の防止: 口淫・結合・露出・愛撫等のシーンでは、物理的接触の対象タグ（penis, fellatio, pussy, erect nipples, vaginal penetration 等）を必ず先頭付近（人数・構図タグの直後）に配置してください。\n"
        "- 構図の希釈・破綻防止: off-screen male や submissive, dazed, head grab, hands on user thighs などの曖昧タグや重複タグ（oral, hyper detailed等）は含めず、ガイドラインに沿った明確な解剖・被写体タグで構成してください。\n"
        "- ポーズの実在タグ化: ユーザーが指定したポーズ・体勢は実在の Danbooru ポーズタグで記述してください（I字バランス→standing split, leg up / 四つん這い→all fours / M字開脚→spread legs, knees up 等）。造語タグ（i-balance 等）や日本語タグは画像に反映されないため禁止です。\n"
        "- 日常・非性交シーンの厳守: 日常会話、着衣、ソロ、または直接の男性器接触を伴わない非性交シーン（抱擁、キス、接触のない愛撫等）では、penis, fellatio, 1boy を絶対に追加してはならず、元プロンプトに誤って含まれている場合は確実に削除してください。"
    )


def build_image_refine_context(s, m, base_prompt=None):
    """対象ターンのスナップショットに基づき、画像プロンプトAI推敲・構図および状態整合性の最適化のためのコンテキストを生成する"""
    st = s["setting"]
    msgs = s["messages"]
    mid = m.get("id")
    idx = next((i for i, msg in enumerate(msgs) if msg.get("id") == mid), len(msgs) - 1)

    # タイムスリップ防止: msgs[:idx+1] （その発言までの履歴）のみを参照
    history = msgs[:idx + 1]

    current_prompt = base_prompt if (base_prompt is not None and str(base_prompt).strip()) else ((m.get("image") or {}).get("prompt") or "")
    if not current_prompt and s.get("id") and mid:
        img_meta = load_images_data(s["id"], readonly=True).get(mid, {})
        current_prompt = img_meta.get("prompt", "")
    m_state = m.get("state")
    if not m_state and s.get("id") and mid:
        m_state = load_states_data(s["id"], readonly=True).get(mid)
    current_state = m_state or state_before(history, idx, sid=s.get("id"))

    sections = [
        {
            "label": "今回のタスク",
            "text": (
                "画像プロンプト（Danbooru英語タグ）の構図・被写体・解剖学的整合性の最適化推敲。\n"
                "対象ターンの発言・文脈およびキャラクターステータス（衣装・下着・ポーズ・身体的接触）を分析し、"
                "重複や矛盾を排除して作画崩壊を防ぎ、被写体と接触・解剖要素を的確に反映した英語プロンプトを出力する。\n"
                "※重要: 描画対象はあくまで「対象ターンの最終状態」です。過去のターンで終了した性行為・露出・体勢（前のターンで行っていたフェラや挿入など）は現在の画像には一切描写せず、元プロンプトに残っている場合は必ず削除してください。\n"
                "※本文や会話応答は出力せず、推敲後のプロンプトのみを JSON で outbox に出力すること。"
            )
        },
        {
            "label": "推敲対象の元プロンプト（現在のプロンプト）",
            "text": current_prompt or "（現在プロンプトなし）"
        }
    ]

    ch = st.get("character") or {}
    ch_name = ch.get("name") or "キャラクター"
    chara_lines = []
    if ch_name:
        chara_lines.append(f"名前: {ch_name}")
    if ch.get("speech"):
        chara_lines.append(f"口調: {ch.get('speech')}")
    summary = (st.get("summary") or "").strip()
    if summary:
        chara_lines.append(f"概要・関係: {summary}")
    elif ch.get("profile"):
        p_lines = [l for l in ch["profile"].splitlines() if l.strip()]
        chara_lines.append("設定: " + "\n".join(p_lines[:5]))
    if chara_lines:
        sections.append({"label": "キャラクター情報", "text": "\n".join(chara_lines)})

    # 固定キャラ（登録タグ）
    chars = st.get("chars") or []
    if chars:
        c_lines = []
        for c in chars:
            c_lines.append(f"- {c.get('name')}: {c.get('tags')}")
        sections.append({"label": "登録キャラクターの外見タグ（固定キャラ）", "text": "\n".join(c_lines)})

    # 対象画像に指定されているキャラ名
    im_chars = (m.get("image") or {}).get("chars") or []
    if not im_chars and s.get("id") and mid:
        im_chars = load_images_data(s["id"], readonly=True).get(mid, {}).get("chars") or []
    if not im_chars:
        resolved = resolve_image_chars(st, raw_names=None, msg_text=m.get("text", ""), active_state=current_state, prompt_text=current_prompt)
        im_chars = [c["name"] for c in resolved if c.get("name")]
    else:
        norm_chars = []
        for ic_item in (im_chars if isinstance(im_chars, list) else [im_chars]):
            if isinstance(ic_item, dict) and ic_item.get("name"):
                norm_chars.append(str(ic_item["name"]))
            elif isinstance(ic_item, str) and ic_item.strip():
                norm_chars.append(ic_item.strip())
        im_chars = norm_chars
    if im_chars:
        sections.append({
            "label": "この画像に登場する固定キャラ",
            "text": ", ".join(im_chars) + "（外見タグはComfyUI側で自動付加されます）"
        })

    # 対象ターンのステータス（スナップショット）
    if st.get("rp_state") or current_state:
        sections.append({
            "label": "この発言時点のキャラクターステータス（厳密なスナップショット）",
            "text": state_block(current_state)
        })

    # 会話履歴（直近数ターン、タイムスリップ防止のため msgs[:idx+1] のみ）
    context_msgs = []
    prev_user_idx = -1
    for i in range(idx - 1, -1, -1):
        if history[i].get("role") == "user":
            prev_user_idx = i
            break
    start_i = max(0, (prev_user_idx if prev_user_idx >= 0 else idx) - 2)
    for i in range(start_i, idx + 1):
        prev_m = history[i]
        role_label = st.get("user_name", "プレイヤー") if prev_m.get("role") == "user" else ch_name
        is_target = " ★推敲対象のターン（この最終状態を描画する）" if prev_m.get("id") == mid else ""
        text_snippet = prev_m.get("text", "")
        # 対象ターンおよび直前のユーザー発言は全文、それより古い発言（最大2件）は200文字で省略
        if i != idx and i != prev_user_idx and len(text_snippet) > 200:
            text_snippet = text_snippet[:200] + "…"
        context_msgs.append(f"[{role_label}{is_target}]\n{text_snippet}")
    if context_msgs:
        sections.append({
            "label": "直前の文脈（対象ターンまでの会話ログ）",
            "text": "\n\n".join(context_msgs)
        })

    sections.append({
        "label": "プロンプト推敲・クレンジング規則（AIへの指示・厳守事項）",
        "text": image_prompt_guidelines_text(for_refine=True)
    })

    sections.append({
        "label": "応答の書き方（outbox の JSON）",
        "text": image_refine_output_text(s, m)
    })

    return {
        "mode": "image_refine",
        "reason": "画像プロンプトAI推敲・解剖学的整合性最適化",
        "sections": sections,
        "same": [],
        "files": [],
        "light": True,
        "missing": [],
        "rules": None,
        "title": st.get("title")
    }


# ---------------------------------------------------------------- 固定キャラ


def char_key(name):
    if not isinstance(name, str):
        return ""
    return re.sub(r"[\s_]+", "", name).lower()


def split_tags(text):
    if isinstance(text, list):
        res = []
        for item in text:
            if isinstance(item, str):
                res.extend(t.strip() for t in item.split(",") if t.strip())
        return res
    if not isinstance(text, str):
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


def find_char(chars, name, fuzzy=False):
    k = char_key(name)
    if not k or not chars:
        return None
    # 1. 完全一致
    for c in chars:
        if isinstance(c, dict) and char_key(c.get("name")) == k:
            return c
    if not fuzzy:
        return None

    # 2. fuzzy=True 時のあいまい一致（2文字以上かつ候補が1人に特定できる場合のみ）
    if len(k) < 2:
        return None

    # 人数タグ・構図タグ（1girl, solo 等）はキャラ名ではないため除外
    if COUNT_TAG.fullmatch(k):
        return None

    is_k_alphanumeric = bool(re.fullmatch(r"[a-z0-9]+", k))

    candidates = []
    for c in chars:
        if not isinstance(c, dict):
            continue
        cname = c.get("name") or ""
        ck = char_key(cname)
        matched = False

        # c.get("name") が存在する場合の前後部分一致
        if ck and len(ck) >= 2:
            if k in ck or ck in k:
                matched = True

        # 姓名の区切り（スペース・記号 re.split(r"[\s・,、]+", cname)）による各パーツ一致
        if not matched and cname:
            parts = [p.strip() for p in re.split(r"[\s・,、]+", cname) if p.strip()]
            for p in parts:
                pk = char_key(p)
                if len(pk) >= 2 and (k == pk or k in pk or pk in k):
                    matched = True
                    break

        # 英名・ローマ字照合: k が半角英数字の場合、split_tags(c.get("tags")) 内の各タグと照合
        if not matched and is_k_alphanumeric:
            tags = split_tags(c.get("tags"))
            for t in tags:
                if COUNT_TAG.fullmatch(normalize_tag(t)):
                    continue
                tk = char_key(t)
                if len(tk) >= 2 and (k in tk or tk in k):
                    matched = True
                    break

        if matched:
            candidates.append(c)

    # 重複排除（同じキャラが複数登録されていた場合の対策）
    deduped = []
    seen = set()
    for c in candidates:
        cid = c.get("id") or char_key(c.get("name"))
        if cid not in seen:
            seen.add(cid)
            deduped.append(c)

    if len(deduped) == 1:
        return deduped[0]
    return None


APPEARANCE_WORDS = {
    # Hair
    "hair", "twintails", "ponytail", "braid", "braids", "bun", "buns", "bob", "bobcut", "ahoge", "bangs",
    "curly", "straight", "wavy", "cut", "sidecut", "drill", "drills", "updo",
    # Eyes
    "eyes", "heterochromia", "pupils",
    # Colors
    "red", "blue", "green", "yellow", "black", "white", "purple", "pink", "orange", "brown", "grey", "gray",
    "silver", "gold", "golden", "blonde", "blond", "brunette", "pale", "dark", "light", "aqua", "teal", "cyan",
    # Body
    "breasts", "cleavage", "skin", "tall", "short", "petite", "navel", "collarbone", "wings", "horns", "tail", "ears",
    "legs", "thighs", "feet", "barefoot", "bare", "shoulders", "armpits",
    # Clothing / Accessories
    "dress", "shirt", "skirt", "pants", "uniform", "suit", "plugsuit", "bodysuit", "leotard", "swimsuit", "bikini",
    "jacket", "coat", "hoodie", "sweater", "kimono", "yukata", "boots", "shoes", "socks", "stockings", "gloves",
    "hat", "ribbon", "bow", "tie", "glasses", "panties", "bra", "underwear", "sleeves", "collar", "belt", "cape",
    "cloak", "apron", "maid", "choker", "serafuku", "sailor", "cardigan", "blazer", "vest", "corset",
    # Expressions / Poses
    "smile", "smiling", "smiles", "grin", "open", "mouth", "closed", "looking", "look", "blush", "blushing",
    "sitting", "standing", "lying", "kneeling", "walking", "holding", "tears", "crying", "wink", "winking",
    # Counts / Meta
    "girl", "girls", "boy", "boys", "solo", "simple", "background", "cute", "focus", "detailed", "view", "scenery"
}


def get_char_match_terms(c):
    terms = set()
    if not isinstance(c, dict):
        return terms
    cname = (c.get("name") or "").strip()
    if cname:
        terms.add(cname)
        clean_n = re.sub(r"\s+", "", cname)
        if len(clean_n) >= 2:
            terms.add(clean_n)
        for p in re.split(r"[\s・,、]+", cname):
            p = p.strip()
            if len(p) >= 2:
                terms.add(p)
        m_kana = re.match(r"^([\u4e00-\u9faf\u30a0-\u30ff・]{1,4})([\u3040-\u309f]{2,})$", clean_n)
        if m_kana:
            if len(m_kana.group(1)) >= 2:
                terms.add(m_kana.group(1))
            if len(m_kana.group(2)) >= 2:
                terms.add(m_kana.group(2))
        if re.fullmatch(r"[\u4e00-\u9faf]{4}", clean_n):
            terms.add(clean_n[:2])
            terms.add(clean_n[2:])
        m_kata = re.match(r"^([\u4e00-\u9faf]{1,4})([\u30a0-\u30ff]{2,})$", clean_n)
        if m_kata:
            if len(m_kata.group(1)) >= 2:
                terms.add(m_kata.group(1))
            if len(m_kata.group(2)) >= 2:
                terms.add(m_kata.group(2))

    for a in [c.get("alias"), c.get("aliases")]:
        if isinstance(a, str) and a.strip():
            terms.add(a.strip())
        elif isinstance(a, (list, tuple, set)):
            for item in a:
                if isinstance(item, str) and item.strip():
                    terms.add(item.strip())

    tags = split_tags(c.get("tags"))
    found_first_char_tag = False
    for t in tags:
        nt = normalize_tag(t)
        if not nt or COUNT_TAG.fullmatch(nt) or nt in CORE_ANATOMY_TAGS or is_censor_related_tag(t):
            continue

        parts = [p.strip() for p in re.split(r"[\s_]+", nt) if p.strip()]
        is_appearance = any(p in APPEARANCE_WORDS for p in parts)
        has_paren = "(" in t and ")" in t

        if has_paren:
            m_paren = re.match(r"^([^\(]+)\((.+)\)$", t.strip())
            if m_paren:
                char_part = m_paren.group(1).strip()
                nt_char = normalize_tag(char_part)
                terms.add(t.strip())
                terms.add(nt)
                if nt_char:
                    terms.add(char_part)
                    terms.add(nt_char)
                    for part in re.split(r"[\s_]+", nt_char):
                        part = part.strip()
                        if len(part) >= 3 and part not in APPEARANCE_WORDS:
                            terms.add(part)
        elif not is_appearance and not found_first_char_tag:
            found_first_char_tag = True
            terms.add(t.strip())
            terms.add(nt)
            for part in parts:
                if len(part) >= 3 and part not in APPEARANCE_WORDS:
                    terms.add(part)

    return terms


def char_presets():
    return [p for p in read_json(CHAR_PRESETS, []) if isinstance(p, dict)]


def merge_chars(chars, updates, presets=None):
    """AI が返した characters を一覧に反映する（chars を書き換える）。
    ユーザーが固定したキャラは変えない。初登場のキャラは同名のプリセットがあればそちらを使う"""
    for u in updates or []:
        if not isinstance(u, dict):
            continue
        name = (u.get("name") or "").strip() if isinstance(u.get("name"), str) else ""
        tags = ", ".join(t for t in split_tags(u.get("tags"))
                         if not COUNT_TAG.fullmatch(t))  # 人数タグは場面側で書く
        if not name or not tags:
            continue
        cur = find_char(chars, name, fuzzy=True)
        if cur:
            if char_key(cur.get("name")) == char_key(name) and not cur.get("locked"):
                cur.update(tags=tags, source="ai")
            continue
        p = find_char(presets or [], name, fuzzy=True)
        p_name = (p.get("name") or "").strip() if p else ""
        chars.append({"id": new_id(), "name": p_name if p_name else name, "tags": p["tags"] if p else tags,
                      "locked": bool(p), "source": "preset" if p else "ai"})
    return chars


def chars_block(chars):
    lines = [f"- {c['name']}: {c['tags']}" + ("（ユーザーが固定。characters で変えても変わらない）" if c.get("locked") else "")
             for c in chars if c.get("name")]
    return "\n".join(lines) or "（まだいない）"


def resolve_image_chars(setting, raw_names=None, msg_text="", active_state=None, prompt_text=""):
    """画像に写る固定キャラを判定・補完する。
    1. AIの指定名 (raw_names) の照合
    2. active_state による候補絞り込み
    3. 構図人数 (prompt_text) の判定
    4. 本文 (msg_text) 出現頻度カウント（安全なエイリアスのみ）
    5. メインキャラ等のフォールバック
    """
    setting = setting or {}
    reg_chars = [c for c in (setting.get("chars") or []) if isinstance(c, dict) and c.get("name")]
    if not reg_chars:
        return []

    # (1) AI指定名 raw_names から find_char(reg_chars, name, fuzzy=True) で照合
    name_list = []
    if isinstance(raw_names, str):
        name_list = [n.strip() for n in raw_names.split(",") if n.strip()]
    elif isinstance(raw_names, dict):
        if raw_names.get("name"):
            name_list = [n.strip() for n in str(raw_names["name"]).split(",") if n.strip()]
    elif isinstance(raw_names, (list, tuple, set)):
        for item in raw_names:
            if isinstance(item, str) and item.strip():
                for part in item.split(","):
                    if part.strip():
                        name_list.append(part.strip())
            elif isinstance(item, dict) and item.get("name"):
                for part in str(item["name"]).split(","):
                    if part.strip():
                        name_list.append(part.strip())

    if name_list:
        matched_chars = []
        for n in name_list:
            c = find_char(reg_chars, n, fuzzy=True)
            if c:
                matched_chars.append(c)
        if matched_chars:
            seen = set()
            deduped = []
            for c in matched_chars:
                cid = c.get("id") or char_key(c.get("name"))
                if cid not in seen:
                    seen.add(cid)
                    deduped.append(c)
            if deduped:
                return deduped

    # (2) active_state による候補絞り込み
    state_items = []
    if isinstance(active_state, dict):
        state_items = [dict(v, name=k) if isinstance(v, dict) else v for k, v in active_state.items()]
    elif isinstance(active_state, list):
        state_items = active_state

    state_chars = []
    for st_item in state_items:
        if isinstance(st_item, dict) and not st_item.get("remove") and st_item.get("name"):
            sc = find_char(reg_chars, st_item["name"], fuzzy=True)
            if sc and sc not in state_chars:
                state_chars.append(sc)

    candidates = state_chars if state_chars else list(reg_chars)

    # (3) 構図人数判定
    is_multi_girls = False
    multi_patterns = r"\b([2-6]\+?girls|[2-6]\+?\s+girls|multiple\s+girls|multiple_girls)\b"
    if prompt_text and re.search(multi_patterns, prompt_text, re.I):
        is_multi_girls = True
    else:
        MULTI_GIRLS_TAGS = {"2girls", "3girls", "4girls", "5girls", "6+girls", "multiple girls"}
        for t in split_tags(prompt_text):
            norm_t = normalize_tag(t)
            if norm_t in MULTI_GIRLS_TAGS or norm_t.replace(" ", "") in {"2girls", "3girls", "4girls", "5girls", "6+girls", "multiplegirls"}:
                is_multi_girls = True
                break

    max_chars = len(candidates) if is_multi_girls else 1

    # (4) 本文出現頻度カウント (msg_text)
    msg_str = msg_text or ""
    main_name = ((setting.get("character") or {}).get("name") or "").strip()

    scored = []
    for c in candidates:
        cname = (c.get("name") or "").strip()
        aliases = set()
        clean_n = re.sub(r"\s+", "", cname)
        if len(cname) >= 2:
            aliases.add(cname)
        if len(clean_n) >= 2:
            aliases.add(clean_n)
        for p in re.split(r"[\s・,、]+", cname):
            p = p.strip()
            if len(p) >= 2:
                aliases.add(p)

        # 漢字姓＋ひらがな名（例: 井ノ上たきな -> 井ノ上, たきな）※「きな」等の末尾2文字切り出しは行わない
        m_kana = re.match(r"^([\u4e00-\u9faf\u30a0-\u30ff・]{1,4})([\u3040-\u309f]{2,})$", clean_n)
        if m_kana:
            if len(m_kana.group(1)) >= 2:
                aliases.add(m_kana.group(1))
            if len(m_kana.group(2)) >= 2:
                aliases.add(m_kana.group(2))

        # 4文字漢字姓名（例: 錦木千束 -> 錦木, 千束）
        if re.fullmatch(r"[\u4e00-\u9faf]{4}", clean_n):
            aliases.add(clean_n[:2])
            aliases.add(clean_n[2:])

        # 漢字姓＋カタカナ名（例: 綾波レイ -> 綾波, レイ）
        m_kata = re.match(r"^([\u4e00-\u9faf]{1,4})([\u30a0-\u30ff]{2,})$", clean_n)
        if m_kata:
            if len(m_kata.group(1)) >= 2:
                aliases.add(m_kata.group(1))
            if len(m_kata.group(2)) >= 2:
                aliases.add(m_kata.group(2))

        # メインキャラ設定との一致によるエイリアス補完
        if main_name and len(main_name) >= 2 and find_char([c], main_name, fuzzy=True):
            aliases.add(main_name)
            for p in re.split(r"[\s・,、]+", main_name):
                p = p.strip()
                if len(p) >= 2:
                    aliases.add(p)

        cnt = 0
        if aliases and msg_str:
            msg_lower = msg_str.lower()
            for a in aliases:
                if a.isascii():
                    cnt += msg_lower.count(a.lower())
                else:
                    cnt += msg_str.count(a)
        is_main = bool(main_name and find_char([c], main_name, fuzzy=True))
        scored.append((cnt, 1 if is_main else 0, c))

    # count 降順、同点なら is_main 優先
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chars_with_count = [c for cnt, is_m, c in scored if cnt > 0]
    if chars_with_count:
        return chars_with_count[:max_chars]

    # (5) 最終フォールバック
    if len(candidates) == 1:
        return [candidates[0]]
    if len(reg_chars) == 1:
        return [reg_chars[0]]
    if main_name:
        main_c = find_char(candidates, main_name, fuzzy=True)
        if main_c:
            return [main_c]
        main_c = find_char(reg_chars, main_name, fuzzy=True)
        if main_c:
            return [main_c]

    if candidates:
        return [candidates[0]]
    if reg_chars:
        return [reg_chars[0]]
    return []


# ---------------------------------------------------------------- キャラの状態
# AI が応答の state で書く、キャラごとの今の状態（箇条書き）と内心。応答（assistant の発言）ごとにその時点の全員分を持つので、
# 巻き戻し・作り直し・分岐をすると、残った発言の最後の状態に戻る
def clean_state(v):
    """[{name, outfit?, outerwear?, underwear?, equipped_items?: [str], penetration?: str, body_fluids?: str, situation?: [str], inner?: str, remove?}] に整える（書いてあった項目だけ持つ）。{"名前": {...}} の形も受け付ける"""
    if isinstance(v, dict):
        if isinstance(v.get("name"), str) and v["name"].strip():
            v = [v]
        else:
            v = [dict(x, name=k) if isinstance(x, dict) and "name" not in x else x for k, x in v.items() if isinstance(x, dict)]
    out = []
    CLEAN_IGNORE = {"なし", "無し", "特になし", "none", "None", "NONE", "null", "Null", "NULL", "[]"}
    for c in v if isinstance(v, list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip():
            continue
        u = {"name": c["name"].strip()}
        if c.get("remove") is True:
            u["remove"] = True
        if "outfit" in c:
            if isinstance(c["outfit"], str):
                val = c["outfit"].strip()
                u["outfit"] = "" if val in CLEAN_IGNORE else val
            elif c["outfit"] is None or (isinstance(c["outfit"], (list, dict)) and not c["outfit"]):
                u["outfit"] = ""
        if "outerwear" in c:
            if isinstance(c["outerwear"], str):
                val = c["outerwear"].strip()
                u["outerwear"] = "" if val in CLEAN_IGNORE else val
            elif c["outerwear"] is None or (isinstance(c["outerwear"], (list, dict)) and not c["outerwear"]):
                u["outerwear"] = ""
        if "underwear" in c:
            if isinstance(c["underwear"], str):
                val = c["underwear"].strip()
                u["underwear"] = "" if val in CLEAN_IGNORE else val
            elif c["underwear"] is None or (isinstance(c["underwear"], (list, dict)) and not c["underwear"]):
                u["underwear"] = ""
        if "equipped_items" in c:
            eq = c["equipped_items"]
            if isinstance(eq, str):
                eq = eq.splitlines()
            if isinstance(eq, list):
                u["equipped_items"] = [x for x in (re.sub(r"^\s*[-・*•]\s*", "", y).strip() for y in eq if isinstance(y, str)) if x and x not in CLEAN_IGNORE]
            else:
                u["equipped_items"] = []
        if "penetration" in c:
            if isinstance(c["penetration"], str):
                val = c["penetration"].strip()
                u["penetration"] = "" if val in CLEAN_IGNORE else val
            elif c["penetration"] is None or (isinstance(c["penetration"], (list, dict)) and not c["penetration"]):
                u["penetration"] = ""
        if "body_fluids" in c:
            if isinstance(c["body_fluids"], str):
                val = c["body_fluids"].strip()
                u["body_fluids"] = "" if val in CLEAN_IGNORE else val
            elif c["body_fluids"] is None or (isinstance(c["body_fluids"], (list, dict)) and not c["body_fluids"]):
                u["body_fluids"] = ""
        for key in ("pose", "camera", "background"):
            if key in c:
                raw_v = c[key]
                if isinstance(raw_v, str):
                    val = raw_v.strip()
                    u[key] = "" if val in CLEAN_IGNORE else val
                elif isinstance(raw_v, (list, tuple)):
                    items = [re.sub(r"^\s*[-・*•]\s*", "", str(x)).strip() for x in raw_v if isinstance(x, str)]
                    u[key] = [x for x in items if x and x not in CLEAN_IGNORE]
                elif raw_v is None or (isinstance(raw_v, (list, dict)) and not raw_v):
                    u[key] = []
                else:
                    u[key] = ""
        if "place" in c:
            if isinstance(c["place"], str):
                val = c["place"].strip()
                u["place"] = "" if val in CLEAN_IGNORE else val
            else:
                u["place"] = ""
        if "action" in c:
            raw_a = c["action"]
            if isinstance(raw_a, str):
                items = re.split(r"[\n,、]+", raw_a)
            elif isinstance(raw_a, (list, tuple)):
                items = list(raw_a)
            else:
                items = []
            u["action"] = [x for x in (re.sub(r"^\s*[-・*•]\s*", "", y).strip() for y in items if isinstance(y, str)) if x and x not in CLEAN_IGNORE]
        if "penis_present" in c:
            _v = c["penis_present"]
            if isinstance(_v, bool):
                u["penis_present"] = _v
            elif isinstance(_v, str):
                _sv = _v.strip().lower()
                if _sv in ("true", "yes", "1", "あり", "はい"):
                    u["penis_present"] = True
                elif _sv in ("false", "no", "0", "なし", "いいえ"):
                    u["penis_present"] = False
        if "expression" in c:
            raw_e = c["expression"]
            if isinstance(raw_e, str):
                items = re.split(r"[\n,、]+", raw_e)
            elif isinstance(raw_e, (list, tuple)):
                items = list(raw_e)
            else:
                items = []
            cleaned_expr = []
            for x in items:
                if isinstance(x, str):
                    cleaned_x = re.sub(r"^\s*[-・*•]\s*", "", x).strip()
                    if cleaned_x and cleaned_x not in CLEAN_IGNORE:
                        cleaned_expr.append(cleaned_x)
            u["expression"] = cleaned_expr
        if "situation" in c:
            sit = c["situation"]
            if isinstance(sit, str):
                sit = sit.splitlines()
            if isinstance(sit, list):
                u["situation"] = [x for x in (re.sub(r"^\s*[-・*•]\s*", "", y).strip() for y in sit if isinstance(y, str)) if x and x not in CLEAN_IGNORE]
            else:
                u["situation"] = []
        if "inner" in c:
            if isinstance(c["inner"], str):
                u["inner"] = c["inner"].strip()
            elif c["inner"] is None:
                u["inner"] = ""
        out.append(u)
    return out


def merge_state(cur, updates):
    """updates に入っているキャラの、書いてある項目だけ入れ替える（remove なら消す）。入っていないキャラは前のまま"""
    out = [dict(c) for c in cur]
    for u in updates:
        i = next((j for j, c in enumerate(out) if char_key(c["name"]) == char_key(u["name"])), -1)
        if u.get("remove"):
            if i >= 0:
                out.pop(i)
            continue
        if i >= 0:
            out[i].update(u)
        else:
            out.append(u)
    res = []
    for c in out:
        item = {"name": c["name"]}
        if c.get("outfit"):
            item["outfit"] = c["outfit"]
        if c.get("outerwear"):
            item["outerwear"] = c["outerwear"]
        if c.get("underwear"):
            item["underwear"] = c["underwear"]
        if "equipped_items" in c and isinstance(c["equipped_items"], list):
            item["equipped_items"] = c["equipped_items"]
        if c.get("penetration"):
            item["penetration"] = c["penetration"]
        if c.get("body_fluids"):
            item["body_fluids"] = c["body_fluids"]
        for key in ("pose", "place", "camera", "background"):
            if c.get(key):
                item[key] = c[key]
        if "expression" in c and isinstance(c["expression"], list):
            item["expression"] = c["expression"]
        if "action" in c and isinstance(c["action"], list):
            item["action"] = c["action"]
        if "penis_present" in c and isinstance(c["penis_present"], bool):
            item["penis_present"] = c["penis_present"]
        item["situation"] = c.get("situation") or []
        item["inner"] = c.get("inner") or ""
        res.append(item)
    return res


def state_before(msgs, idx, sid=None):
    """msgs[idx] より前で最後に記録した状態"""
    if not sid and msgs:
        for x in reversed(msgs):
            if x and x.get("id") and x["id"] in msg_to_session:
                sid = msg_to_session[x["id"]]
                break
    sdata = None
    for m in reversed(msgs[:idx]):
        if m.get("role") == "assistant":
            if isinstance(m.get("state"), list):
                return m["state"]
            if sid:
                if sdata is None:
                    sdata = load_states_data(sid, readonly=True)
                mid = m.get("id")
                if mid and mid in sdata:
                    return sdata[mid]
    return []


def state_block(state):
    if not state:
        return "（まだない。場面に出ているキャラの分を、応答の state で書き始める）"
    return json.dumps(state, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- ide
def ws_path(uri):
    """file:///d%3A/x/y -> D:/x/y（表示用。file:///d:/ の書き方もある）"""
    p = urllib.parse.unquote(uri or "").replace("file:///", "")
    return p[:1].upper() + p[1:] if p[1:2] == ":" else p


def ide_sessions():
    """Antigravity の会話を全部（子エージェントの会話を除く）。UI がフォルダごとにまとめて見せる"""
    if not os.path.exists(IDE_DB):
        raise FileNotFoundError("Antigravity の会話一覧が見つかりません: " + IDE_DB)
    db = sqlite3.connect("file:" + IDE_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=3)
    try:
        rows = db.execute(
            "SELECT conversation_id, title, preview, step_count, last_modified_time, workspace_uris"
            " FROM conversation_summaries WHERE nesting_depth = 0 AND killed = 0"
            " ORDER BY last_modified_time LIKE '0001%' DESC, last_modified_time DESC").fetchall()  # まだ何もない新しい会話を先に
    finally:
        db.close()
    out = []
    for cid, title, preview, steps, modified, ws in rows:
        try:  # IDE側はUTCで持っているのでローカル時刻に直す
            updated = datetime.datetime.strptime(modified[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):  # 作ったばかりの会話は 0001-01-01（Windows では変換できない）
            updated = ""
        out.append({
            "id": cid, "title": title or preview or "(無題)", "steps": steps,
            "updated": updated,
            "workspace": ", ".join(ws_path(u) for u in json.loads(ws or "[]")),
            "waiting": time.time() - ide_waiting.get(cid, 0) < 10,
            "poke": poke_log.get(cid),
        })
    return out


def ide_workspaces(cid):
    """IDE会話のワークスペースのフォルダ（合図を送るウィンドウを探すのに使う）"""
    db = sqlite3.connect("file:" + IDE_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=3)
    try:
        row = db.execute("SELECT workspace_uris FROM conversation_summaries WHERE conversation_id = ?", (cid,)).fetchone()
    finally:
        db.close()
    return [urllib.parse.unquote(u).replace("file:///", "") for u in json.loads((row and row[0]) or "[]")]


known_ides = None


def mark_known(ide):
    global known_ides
    if known_ides is None:
        known_ides = set(read_json(IDE_KNOWN, []))
    if ide not in known_ides:
        known_ides.add(ide)
        write_json(IDE_KNOWN, sorted(known_ides))


# ---------------------------------------------------------------- AGENTS.md（チャンネルの作業フォルダに置くルール）
# Antigravity は会話の作業フォルダ（から上）の AGENTS.md を毎ターン読み直し、ルールとして使う（途中で書き換えても次のターンから効く）。
# System ファイルはここに書けば、wait_input.py の表示で見せるより強く・忘れられずに効く
AGENTS_NAME = "AGENTS.md"
AGENTS_MARK = "<!-- rp-image-chat:"  # アプリが作ったしるし。しるしのないファイル（人が書いたもの）は書き換えない
AGENTS_OWNER = re.compile(re.escape(AGENTS_MARK) + r" channel=([\w-]+) -->")
AGENTS_LOG = os.path.join(DATA, "agents_md.json")  # チャンネルID -> 最後に書いた AGENTS.md の場所（フォルダを変えたら古いほうを片付ける）
agents_state = {}  # チャンネルID -> 最後に書いたときの結果 {"path", "ok", "msg", "ts"}


def path_key(p):
    return os.path.normcase(os.path.normpath(p))


def folder_name(title):
    """チャンネル名をフォルダ名に使える形にする"""
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title or "").strip().strip(".")
    return n[:40].strip()


def resolve_folder(f):
    return os.path.normpath(os.path.join(NEW_IDE_DIR, os.path.expanduser(f.strip())))  # 絶対パスならそのまま


def channel_folder(s, fix=False):
    """チャンネルの作業フォルダ（新規会話を作る場所・AGENTS.md を置く場所）。
    ide_folder が空なら NEW_IDE_DIR そのもの（チャンネルごとのフォルダは作らない）。
    fix は前の作り（チャンネル名のフォルダを設定に残す）の名残りで、いまは何もしない"""
    st = s["setting"]
    if (st.get("ide_folder") or "").strip():
        return resolve_folder(st["ide_folder"])
    return NEW_IDE_DIR


def agents_text(s):
    """チャンネルの AGENTS.md の中身。System ファイルが読めなければ OSError / ValueError"""
    st = s["setting"]
    title = st.get("title") or s["id"]
    out = [f"{AGENTS_MARK} channel={s['id']} -->",
           f"<!-- RP Image Chat がチャンネル「{title}」の設定から作るファイル。直接書き換えても次に上書きされる。変えるときはチャット画面の設定から -->",
           "", f"# ロールプレイチャット「{title}」", ""]
    ide = st.get("ide_session") or ""
    if ide:
        me = f'python "{os.path.join(ROOT, "wait_input.py")}" --ide {ide}'
        out.append("この会話はロールプレイチャットアプリのこのチャンネルの担当。チャットの入力は wait_input.py で受け取り、"
                   "応答は wait_input.py が表示した outbox に JSON で書く。")
        out.append(f"入力があると、この会話に半角スペース1文字（合図）が届く。届いたら `{me} --wait 30` を実行し、表示に従って応答する。"
                   "書いたら待機せず、この会話への返事は半角スペース1文字だけにしてターンを終える。")
        out.append("")
    if st.get("system_file"):
        out += [f"## System（{st['system_file']}）", "", read_text(system_file_path(st["system_file"])), ""]
    if (st.get("agents_extra") or "").strip():
        out += ["## 追加のルール", "", st["agents_extra"].strip(), ""]
    return "\n".join(out).rstrip() + "\n"


def agents_owner(path):
    """そのファイルを作ったチャンネルのID。アプリが作ったものでなければ ""、ファイルがなければ None"""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            head = f.read(300)
    except FileNotFoundError:
        return None
    m = AGENTS_OWNER.match(head)
    return m.group(1) if m else ""


def retire_agents(sid, path):
    """このチャンネルが前に書いた AGENTS.md を trash へ移す（人が書いたもの・ほかのチャンネルのものは触らない）"""
    if agents_owner(path) != sid:
        return
    os.makedirs(os.path.join(TRASH, "agents_md"), exist_ok=True)
    n, dst = 0, None
    while not dst or os.path.exists(dst):  # 同じ秒に2回片付けても上書きしない
        n += 1
        dst = os.path.join(TRASH, "agents_md", f"{sid}_{int(time.time())}_{n}_{AGENTS_NAME}")
    try:
        shutil.move(path, dst)
        print(f"[agents] {sid} 片付けた {path}")
    except OSError as e:
        print(f"[agents] {sid} 片付けられなかった {path}: {e}")


def sync_choices_agents(s):
    """選択肢担当の作業フォルダ(session/choices)に専用の AGENTS.md を書き出し、親の AGENTS.md との混線を防ぐ"""
    st = s["setting"]
    c_ide = (st.get("choices_ide_session") or "").strip()
    if not c_ide:
        return
    c_folder = os.path.join(channel_folder(s), "choices")
    try:
        os.makedirs(c_folder, exist_ok=True)
        c_path = os.path.join(c_folder, AGENTS_NAME)
        me = f'python "{os.path.join(ROOT, "wait_input.py")}" --ide {c_ide}'
        title = st.get("title") or s["id"]
        text = (
            f"{AGENTS_MARK} channel={s['id']}_choices -->\n"
            f"# ロールプレイチャット「{title}」（選択肢生成専用セッション）\n\n"
            "この会話は返答候補（choices・8択）を並列生成する専用担当です。\n"
            f"半角スペース1文字（合図）が届いたら `{me} --wait 30` を実行し、表示に従って outbox に JSON を出力してください。\n"
            "書いたら待機せず、この会話への返事は半角スペース1文字だけにしてターンを終えてください。\n"
        )
        cur = None
        if os.path.isfile(c_path):
            with open(c_path, encoding="utf-8", errors="replace", newline="") as f:
                cur = f.read()
        if cur != text:
            with open(c_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    except OSError as e:
        print(f"[agents] choices agents error: {e}")


def sync_agents(s):
    """チャンネルの AGENTS.md を設定に合わせる（中身が変わったときだけ書く）。
    オフにした・フォルダを変えたときは前に書いたものを trash へ。結果は agents_state に残す。
    作業フォルダを新しく決めたら True（呼んだ側で save する）"""
    sync_choices_agents(s)
    st, sid = s["setting"], s["id"]
    log = read_json(AGENTS_LOG, {})
    old, path, fixed = log.get(sid), None, False
    if st.get("agents_md"):
        fixed = not (st.get("ide_folder") or "").strip()
        path = os.path.join(channel_folder(s, fix=True), AGENTS_NAME)
    if old and (not path or path_key(old) != path_key(path)):
        retire_agents(sid, old)
        log.pop(sid, None)
    if not path:
        agents_state.pop(sid, None)
        write_json(AGENTS_LOG, log)
        return fixed
    try:
        text = agents_text(s)
        owner = agents_owner(path)
        if owner == "":
            raise ValueError(f"{path} はアプリが作ったものではないので書き換えない（自分で書いた AGENTS.md なら、作業フォルダを別にする）")
        if owner and owner != sid:
            try:
                o = load(owner)
                taken = o["setting"].get("agents_md") and path_key(channel_folder(o)) == path_key(os.path.dirname(path))
            except (OSError, ValueError):
                taken = False
            if taken:
                raise ValueError(f"このフォルダの AGENTS.md はチャンネル「{o['setting'].get('title')}」のもの。作業フォルダを分ける")
        cur = None
        if owner == sid:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                cur = f.read()
        if cur != text:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print(f"[agents] {sid} 書いた {path}")
        log[sid] = path
        agents_state[sid] = {"path": path, "ok": True, "msg": "書いた" if cur != text else "最新", "ts": now()}
    except (OSError, ValueError) as e:
        agents_state[sid] = {"path": path, "ok": False, "msg": str(e), "ts": now()}
    write_json(AGENTS_LOG, log)
    return fixed


def agents_in_use(s):
    """担当の会話がこのチャンネルの AGENTS.md を読んでいれば（会話の作業フォルダが AGENTS.md のフォルダなら）その場所"""
    st = s["setting"]
    if not st.get("agents_md") or not st.get("ide_session"):
        return None
    folder = channel_folder(s)
    path = os.path.join(folder, AGENTS_NAME)
    if agents_owner(path) != s["id"]:
        return None
    try:
        ws = ide_workspaces(st["ide_session"])
    except (OSError, sqlite3.Error, ValueError):
        return None
    return path if any(path_key(w) == path_key(folder) for w in ws) else None


def agents_info(s):
    """画面に出す AGENTS.md の様子"""
    st = s["setting"]
    folder = channel_folder(s)
    info = {"folder": folder, "fixed": bool((st.get("ide_folder") or "").strip()), "enabled": bool(st.get("agents_md")),
            "path": os.path.join(folder, AGENTS_NAME), "state": agents_state.get(s["id"]), "workspace": None, "in_use": False}
    try:
        info["text"] = agents_text(s)
    except (OSError, ValueError) as e:
        info["text"], info["error"] = "", f"System ファイルが読めない: {e}"
    if st.get("ide_session"):
        try:
            info["workspace"] = ", ".join(ide_workspaces(st["ide_session"]))
        except (OSError, sqlite3.Error, ValueError):
            pass
        info["in_use"] = bool(agents_in_use(s))
    return info


# ---------------------------------------------------------------- 会話の引っ越し（担当の会話の作業フォルダをチャンネルのフォルダへ）
# 履歴を残したまま会話がチャンネルの AGENTS.md を読むようにする。Antigravity の再起動が要るので、予約しておいてまとめて1回で移す
IDE_MOVES = os.path.join(DATA, "ide_moves.json")  # 会話ID -> {"folder", "sid", "title", "ts"}（予約）
move_state = {"running": False}  # 最後に移したときの様子 {"running", "msg", "results", "ts"}


def channels_of(ide):
    """その会話を担当にしているチャンネル [(ID, 名前)]"""
    out = []
    for sid in list_ids():
        try:
            st = load(sid, readonly=True)["setting"]
        except (OSError, ValueError):
            continue
        if st.get("ide_session") == ide or st.get("choices_ide_session") == ide:
            out.append((sid, st.get("title") or sid))
    return out



def branch_fork_point(msgs, idx, turns):
    """チャンネルを msgs[idx] までで分岐するとき、担当の会話をどのステップで分岐するか。
    含まれる最後の AI の返事を書いたターンのあと、次の入力を受け取ったターンの前（そのターンの最初の発言の直前）で切る。
    turns は ide_poke.turns の発言一覧（古い順）。(at, None) か (None, 分岐しない理由)。at = -1 は最後まで"""
    a = next((i for i in range(idx, -1, -1) if msgs[i]["role"] == "assistant"), None)
    if a is None:
        return None, "まだAIの返事がない時点なので、Antigravity の会話は分岐しない（新規会話を使う）"
    lower = _epoch(msgs[a].get("ts"))
    if lower is None:
        return None, "返事の時刻が読めないので、Antigravity の会話は分岐しない"
    upper = None  # 次の入力を受け取った時刻（秒未満は切り捨てなので1秒の余裕）
    u = next((i for i in range(a + 1, len(msgs)) if msgs[i]["role"] == "user"), None)
    if u is not None:
        upper = _epoch(msgs[u].get("claimed_ts"))
        if upper is None:  # 受け取った時刻を残す前の入力は、その返事の時刻まで
            r = next((x for x in msgs[u + 1:] if x["role"] == "assistant"), None)
            upper = _epoch(r.get("ts")) if r else None
        if upper is not None:
            upper += 1
    t = next((t for t in turns if (_epoch(t.get("time")) or 0) > lower), None)
    if t is None or (upper is not None and _epoch(t["time"]) > upper):
        if upper is None:
            return -1, None  # 返事のあとに何も来ていない＝いまの会話の最後まで
        return None, ("その返事のあと、次の入力までの間にターンの切れ目がない（1つのターンの中で続けて受け取っていた）ので、"
                      "ちょうどの位置で分岐できない。Antigravity の会話は分岐しない")
    if t["at"] is None:
        return None, "会話の最初の発言より前になるので、Antigravity の会話は分岐しない"
    return t["at"], None


def plan_revert(s, keep):
    """発言を書き換える前に呼ぶ。担当の会話を msgs[keep] まで（keep = -1 なら最初の発言の前まで）に巻き戻す予定を覚える。
    巻き戻しは時間がかかるので lock の外（/api/ide/revert）でする。予定を作ったら True"""
    st = s["setting"]
    if not st.get("ide_session") or not st.get("ide_revert", True):
        return False
    revert_plan[s["id"]] = {"ide": st["ide_session"], "msgs": copy.deepcopy(s["messages"]), "keep": keep, "t": time.time()}
    return True


def moves_info():
    pend = read_json(IDE_MOVES, {})
    return {"pending": [dict(v, ide=k) for k, v in pend.items()], "state": move_state}


def run_moves(force):
    """予約した引っ越しをまとめて実行する（別スレッドで。Antigravity を閉じて書き換え、起動し直す）"""
    pend = read_json(IDE_MOVES, {})

    def backup(cid, blob, row):  # 書き換える前の値を trash に残す（戻すときに使う）
        os.makedirs(os.path.join(TRASH, "ide_moves"), exist_ok=True)
        write_json(os.path.join(TRASH, "ide_moves", f"{cid}_{int(time.time())}.json"),
                   {"ide": cid, "to": pend[cid]["folder"], "blob": blob.hex(), "summary": list(row) if row else None, "ts": now()})
    try:
        res, msg = ide_poke.move_workspaces({c: v["folder"] for c, v in pend.items()}, force=force, backup=backup)
    except Exception as e:
        res, msg = {}, f"移せなかった: {e}"
    with lock:
        cur = read_json(IDE_MOVES, {})
        for cid, (ok, _) in res.items():
            if not ok:
                continue
            cur.pop(cid, None)
            for sid, _ in channels_of(cid):  # 移った先で AGENTS.md を読むので、中身を最新に
                s = load(sid)
                if s["setting"].get("agents_md") and sync_agents(s):
                    save(s)
        write_json(IDE_MOVES, cur)
        move_state.update(running=False, msg=msg, ts=now(),
                          results=[{"ide": c, "title": pend.get(c, {}).get("title", ""), "ok": ok, "msg": m} for c, (ok, m) in res.items()])
    print(f"[move] {msg}")


def poke_text(ide, full=False, wait=None, prefix=""):
    """合図の文。wait_input.py を実行したことのない会話は、スペースだけでは何をすればいいか分からないのでコマンドを送る。
    full: 実行したことがあっても、コマンドを送る（スペースだけでは動かなかったとき・このチャンネルを初めて受け持つとき）"""
    global known_ides
    if known_ides is None:
        known_ides = set(read_json(IDE_KNOWN, []))
    if ide in known_ides and not full:
        return prefix + POKE_TEXT
    script = os.path.join(ROOT, "wait_input.py")
    w = 30 if wait is None else int(wait)      # 通常の合図は従来どおり 30 秒
    # wait 指定は「新しい会話の準備（世代交代）」専用。その会話はまだ担当ではないので、
    # 同じフォルダの AGENTS.md には**前の会話のID**が書かれている（書き換えると旧会話が壊れるので
    # cutover まで書き換えない）。AGENTS.md 側のコマンドを実行されると、アプリはこの会話を
    # 担当として知らないままになり（mark_known されず）準備完了の確認が通らない。
    if wait is None:
        head = "（ロールプレイチャットアプリからの合図）このチャットに新しい入力が来た。次のコマンドを実行し、表示に従って応答すること:\n"
    else:
        head = ("（ロールプレイチャットアプリからの合図）このチャットに新しい入力が来た。"
                "（重要）このフォルダの AGENTS.md に書いてある wait_input.py の --ide は前の会話のIDなので、"
                "そのコマンドは実行しないこと。次のコマンドをそのまま実行し、表示に従って応答すること:\n")
    return (prefix + head
            + f'python "{script}" --ide {ide} --wait {w}\n'
            + "これ以降、この会話に半角スペース1文字だけが届いたら、それが同じ合図。同じコマンドを実行すること。")


def ide_model_of(ide):
    """その会話を担当にしているチャンネルで選んだモデル（なければ空）"""
    for sid, _ in channels_of(ide):
        try:
            st = load(sid, readonly=True)["setting"]
        except (OSError, ValueError):
            continue
        if st.get("choices_ide_session") == ide:
            return st.get("choices_ide_model") or st.get("ide_model") or ""
        if st.get("ide_session") == ide:
            m = st.get("ide_model")
            if m:
                return m
    return ""


_poke_locks_guard = threading.Lock()
_poke_locks = {}


def get_poke_lock(ide):
    with _poke_locks_guard:
        if ide not in _poke_locks:
            _poke_locks[ide] = threading.Lock()
        return _poke_locks[ide]


PASTE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}  # 合図と一緒に Antigravity のチャット欄に貼る添付
PASTE_MAX = 20 * 1024 * 1024  # これより大きい画像は貼らない（場所だけ渡す）


def paste_media(msgs):
    """入力に添付した画像を、Antigravity の画面で貼り付けたときと同じ形にする"""
    out = []
    for m in msgs:
        for f in m.get("files") or []:
            full = upload_abs(f["path"])
            if f.get("type") in PASTE_TYPES and os.path.isfile(full) and os.path.getsize(full) <= PASTE_MAX:
                with open(full, "rb") as fp:
                    out.append({"mimeType": f["type"], "description": f["name"],
                                "inlineData": base64.b64encode(fp.read()).decode("ascii")})
    return out


def send_poke(ide, full=False, media=None, model=None, wait=None, prefix=""):
    """担当IDE会話に合図を送る。結果は poke_log に残し、UI に出す。
    media: 合図と一緒にチャット欄に貼る画像（paste_media の形）。
    (成功したか, 説明, あとでやり直すべきか) を返す。会話が応答中のときはやり直す"""
    retry = False
    text = poke_text(ide, full, wait, prefix=prefix)
    if not ide_poke:
        ok, msg = False, "この環境では合図を送れない（Windows のみ）"
    elif POKE_MODE != "window":
        with get_poke_lock(ide):
            try:
                ok, msg, retry = ide_poke.send_to_conversation(ide, text, model or ide_model_of(ide), media or None)
                if ok and text != POKE_TEXT:
                    msg += "（スペースだけでは伝わらないかもしれないので、実行するコマンドを送った）"
                if ok and media:
                    msg += f"（画像{len(media)}枚をチャット欄に貼った）"
            except Exception as e:
                ok, msg = False, f"会話に送れなかった: {e}"
    else:
        try:
            ws = ide_workspaces(ide)
        except (OSError, sqlite3.Error, ValueError) as e:
            ws, ok, msg = None, False, f"IDE の会話一覧を読めない: {e}"
        if ws is not None:
            with poke_lock:
                try:
                    ok, msg = ide_poke.poke(ws, text.replace("\n", " "))  # 改行を打つと途中で送信されるので
                except Exception as e:
                    ok, msg = False, f"キー入力に失敗: {e}"
    if not retry:
        poke_log[ide] = {"ts": now(), "ok": ok, "msg": msg}
    print(f"[poke] {'ok' if ok else 'wait' if retry else 'NG'} {ide[:8]} {msg}")
    return ok, msg, retry


def send_poke_text(ide, text, model=None):
    """合図以外の本文を会話へ送る（疎通テスト用）。(ok, msg, retry)"""
    if not ide_poke:
        return False, "この環境では送れない", False
    with get_poke_lock(ide):
        try:
            return ide_poke.send_to_conversation(ide, text, model or ide_model_of(ide))
        except Exception as e:
            return False, f"送れなかった: {e}", False


_new_ide_guard = threading.Lock()
_new_ide_in_progress = set()


def new_ide(sid, auto=False, target="main", assign=True):
    """Antigravity にチャンネルの作業フォルダの新しい会話を作って、このチャンネルの担当にする。作るのに数秒かかるので lock の外で呼ぶ。
    (会話ID, 名前, 説明, フォルダ) か (None, None, 失敗の説明, None)。チャンネルがなければ FileNotFoundError / ValueError。
    auto: ide_auto_worker から。作っている間に担当が決まった・自動をやめたなら、作った会話は担当にしない。
    target: "main" (メイン担当) | "choices" (選択肢担当) | "both" (両方一括作成)
    assign: True なら担当に割り当て、False なら rotation (予備作成) に記録して担当はまだ切り替えない。

    競合防止: 同じ (sid, target) の作成が進行中なら即座に諦める。
    これにより、自動ワーカーとローテーションが同時に走っても会話が増殖しない。"""
    key = (sid, target)
    with _new_ide_guard:
        if key in _new_ide_in_progress:
            return None, None, "既に作成中", None
        _new_ide_in_progress.add(key)
    try:
        if target == "both":
            cid_m, title_m, msg_m, folder_m = new_ide(sid, auto=auto, target="main", assign=assign)
            cid_c, title_c, msg_c, folder_c = new_ide(sid, auto=auto, target="choices", assign=assign)
            if not cid_m and not cid_c:
                return None, None, f"{msg_m} / {msg_c}", None
            return cid_m or cid_c, title_m or title_c, f"{msg_m} / {msg_c}", folder_m or folder_c

        with lock:
            s = load(sid)
            base_folder = channel_folder(s)
            folder = os.path.join(base_folder, "choices") if target == "choices" else base_folder
        try:
            os.makedirs(folder, exist_ok=True)
            cid, msg = ide_poke.new_conversation(folder)
        except Exception as e:
            cid, msg = None, f"会話を作れなかった: {e}"
        if not cid:
            return None, None, msg, None

        title = ("【選択肢】新規会話 " if target == "choices" else "新規会話 ") + now()[5:16].replace("T", " ")
        if target == "choices" and ide_poke and hasattr(ide_poke, "rename"):
            try:
                ide_poke.rename(cid, title)
            except Exception as e:
                print(f"[ide] rename choices conversation error {cid[:8]}: {e}")

        with lock:
            s = load(sid)
            st = s["setting"]
            if not assign:
                # 予備作成: 担当はまだ切り替えない（I3）。AGENTS.md も cutover で書く
                r = rotation_state(s)
                if not r:
                    return None, None, "回転が中止された", None
                key_cid = "new_choices_ide" if target == "choices" else "new_ide"
                key_title = "new_choices_ide_title" if target == "choices" else "new_ide_title"
                r[key_cid] = cid
                r[key_title] = title
                r["updated"] = now()
                st["rotation"] = r
                save(s)
                return cid, title, "予備作成", folder
            if target == "choices":
                if auto and (st.get("choices_ide_session") or not (st.get("choices_ide_auto") or st.get("ide_auto"))):
                    return None, None, "その間に担当が決まった", None
                st.update(choices_ide_session=cid, choices_ide_title=title, choices_ide_auto=False)
                sync_choices_agents(s)
            else:
                if auto and (st.get("ide_session") or not st.get("ide_auto")):
                    return None, None, "その間に担当が決まった", None
                st.update(ide_session=cid, ide_title=title, ide_auto=False)
                if st.get("agents_md"):
                    sync_agents(s)  # 最初の合図より前に AGENTS.md を置いておく
            save(s)
        print(f"[ide] new ({target}) {cid[:8]} {folder} {msg}")
        return cid, title, msg, folder
    finally:
        with _new_ide_guard:
            _new_ide_in_progress.discard(key)


def rotation_worker():
    """世代交代の進行役。1 tick で各セッションの「1小ステップ」だけ進める（RPC で他を止めない）"""
    while True:
        rotation_event.wait(timeout=ROT_TICK)
        rotation_event.clear()
        now_m = time.monotonic()
        with rotation_guard:
            due = [sid for sid, at in list(rotation_queue.items()) if at <= now_m]
        for sid in due:
            try:
                delay = rotation_step(sid)
            except Exception as e:
                print(f"[rot] {sid[:6]} step error: {e}")
                delay = ROT_TICK if os.path.exists(spath(sid)) else None
            with rotation_guard:
                if sid in rotation_queue:      # fail 等で自分で消している場合がある
                    if delay is None:
                        rotation_queue.pop(sid, None)
                    else:
                        rotation_queue[sid] = time.monotonic() + float(delay)
        rotation_resume_scan()


_rot_scan_at = 0.0


def rotation_resume_scan():
    """phase が残っているのに queue に無いセッションを拾う（再起動・取りこぼし対策）。
    poke_worker が既に全走査しているので 5 秒に1回で十分（I/O とロック競合を増やさない）"""
    global _rot_scan_at
    if time.monotonic() - _rot_scan_at < 5.0:
        return
    _rot_scan_at = time.monotonic()
    try:
        sids = list_ids()
    except Exception:
        return
    with rotation_guard:
        queued = set(rotation_queue)
    for sid in sids:
        if sid in queued:
            continue
        try:
            s = load(sid, readonly=True)
        except (OSError, ValueError):
            continue
        ph = rotation_phase(s)
        if ph and ph != "armed":
            rotation_schedule(sid, 0.0)


ROTATION_STEPS = {
    "armed": lambda sid: None,
}   # phase -> handler(sid) -> None(終了/中止) or 次回までの秒数


def rotation_step(sid):
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        phase = r.get("phase")
    fn = ROTATION_STEPS.get(phase)
    if fn is None:
        if phase == "armed":
            return None
        # 未知 phase（typo・旧版の残骸）は回転ごと中止。放置すると resume_scan が拾い続ける
        rotation_fail(sid, f"未知のフェーズ: {phase}")
        return None
    return fn(sid)


def _rot_archiving(sid):
    snap = None
    path = ""
    done = False
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        st = s["setting"]
        r = rotation_state(s)
        if not r:
            return None
        rot_no = int(r.get("rot_no") or 0)
        total = int(st.get("total_turns") or 0)
        path = os.path.join(ARCHIVE_DIR, f"{sid}_rot{rot_no}_turn{total}.json")
        done = os.path.exists(path)
        if not done:
            snap = clone_session(s)
            sdata = load_states_data(sid, readonly=True)
            idata = load_images_data(sid, readonly=True)
            for m in snap.get("messages", []):
                mid = m.get("id")
                if not mid:
                    continue
                if mid in sdata:
                    m["state"] = copy.deepcopy(sdata[mid])
                if mid in idata:
                    im = m.setdefault("image", {})
                    for k, v in idata[mid].items():
                        im[k] = copy.deepcopy(v)
            # I8: 回転前の自己完結スナップショット（進行中の状態を持ち込まない）
            for k in ("rotation", "rotation_notice", "rotation_skip_until", "rotation_reserve"):
                snap["setting"].pop(k, None)
    if snap is not None:
        try:
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            write_json(path, snap)          # ロック外で書く（大きくても API を止めない）
            print(f"[rot] {sid[:6]} archived {os.path.basename(path)}")
        except OSError as e:
            print(f"[rot] {sid[:6]} archive error: {e}")   # 退避失敗は致命的ではない
            path = ""
    rotation_put(sid, phase="settle", archive=path, settle_since=now())
    return 0.0


ROTATION_STEPS["archiving"] = _rot_archiving


def _rot_settle(sid):
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        old_ide = r.get("old_ide") or ""
        settle_since = r.get("settle_since") or r.get("updated") or now()
    if not old_ide or not ide_poke or not hasattr(ide_poke, "progress"):
        rotation_put(sid, phase="spawn_main")
        return 0.0
    try:
        pg, _ = ide_poke.progress(old_ide)
    except Exception:
        pg = None
    if bool(pg and pg.get("running")) and age(settle_since) < ROT_SETTLE_SEC:
        return 1.0
    rotation_put(sid, phase="spawn_main")
    return 0.0


ROTATION_STEPS["settle"] = _rot_settle


def _rot_spawn(sid, target):
    nxt = "spawn_choices" if target == "main" else "bootstrap_main"
    key_cid = "new_ide" if target == "main" else "new_choices_ide"
    key_res = "ide" if target == "main" else "choices_ide"
    key_title = "new_ide_title" if target == "main" else "new_choices_ide_title"
    att = {}
    n = 0
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        st = s["setting"]
        # choices を作る必要が無ければ飛ばす（判断をここに一本化：迷子のフェーズを作らない）
        if target == "choices" and not (r.get("old_choices_ide") or ""):
            rotation_put(sid, phase="bootstrap_main")
            return 0.0
        # 再起動などで「作成済みだが phase が進んでいない」場合は adoption（増殖防止）
        if r.get(key_cid):
            rotation_put(sid, phase=nxt)
            return 0.0
        # 予備会話の再利用（I11: 使ったキーだけ棚から下ろす）
        res = st.get("rotation_reserve")
        res = res if isinstance(res, dict) else {}
        res_cid = (res.get(key_res) or "").strip()
        if res_cid and ide_poke:
            alive = False
            try:
                alive = bool(ide_poke.locate(res_cid)[0])
            except Exception:
                alive = False
            res.pop(key_res, None)                  # 生きていても死んでいても棚から下ろす
            if res.get("ide") or res.get("choices_ide"):
                st["rotation_reserve"] = res
            else:
                st.pop("rotation_reserve", None)
            res_title = res.get("choices_title") if target == "choices" else res.get("title")
            if alive:
                r[key_cid] = res_cid
                r[key_title] = res_title or res.get("title") or ""
                st["rotation"] = r
                save(s)
                rotation_put(sid, phase=nxt)
                print(f"[rot] {sid[:6]} reuse reserve {res_cid[:8]} ({target})")
                return 0.0
            save(s)   # 死んでいた予備を消したので確定
        att = r.get("attempts")
        att = att if isinstance(att, dict) else {}
        key = "spawn" if target == "main" else "spawn_choices"
        n = int(att.get(key) or 0)
        if n >= ROT_SPAWN_TRIES:
            if target == "choices":
                rotation_put(sid, phase="bootstrap_main")   # choices は諦めて続行
                return 0.0
            rotation_fail(sid, "Antigravity の新規会話を作れない")
            return None
        r["attempts"] = dict(att, **{key: n + 1})
        st["rotation"] = r
        save(s)
    cid, title, msg, _folder = new_ide(sid, target=target, assign=False)
    if not cid:
        if msg == "既に作成中":
            # 他（ide_auto_worker / 手動）が作成中。**待つだけで残機は減らさない**（I10）
            with lock:
                try:
                    s2 = load(sid)
                except (OSError, ValueError):
                    return None
                r2 = rotation_state(s2)
                if r2:
                    r2["attempts"] = dict(r2.get("attempts") or {}, **{key: n})
                    s2["setting"]["rotation"] = r2
                    save(s2)
            return 2.0
        print(f"[rot] {sid[:6]} {target} 作成失敗: {msg}")
        return 5.0 * (n + 1)            # 5,10,15秒後に再試行（次回は adoption が先に走る）
    rotation_put(sid, phase=nxt)
    return 0.0


ROTATION_STEPS["spawn_main"] = lambda sid: _rot_spawn(sid, "main")
ROTATION_STEPS["spawn_choices"] = lambda sid: _rot_spawn(sid, "choices")


def _rot_bootstrap(sid, target):
    nxt = "bootstrap_choices" if target == "main" else "verify_ready"
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        cid = r.get("new_choices_ide") if target == "choices" else r.get("new_ide")
        model = r.get("old_choices_model") if target == "choices" else r.get("old_model")
        att = r.get("attempts")
        att = att if isinstance(att, dict) else {}
        key = "bootstrap_" + target
        n = int(att.get(key) or 0)
        waits = int(att.get("bootstrap_wait") or 0)   # 応答中で待った回数（残機とは別勘定）
    if not cid:
        rotation_put(sid, phase=nxt)
        return 0.0
    # 新規会話は status が None のことがある。busy のときは待つ（**残機を減らさない**：I10）
    try:
        ok_idle, _st = ide_poke.idle(cid)
    except Exception:
        ok_idle = True
    if not ok_idle:
        # 待つだけで残機は減らさないが、**永久には待たない**（応答中のまま固まった会話があると、
        # ここで止まったまま回転が「準備中」で永久に進まなくなる）
        if waits * ROT_VERIFY_POLL > ROT_VERIFY_TIMEOUT:
            if target == "choices":
                rotation_put(sid, phase="verify_ready", attempts=dict(att, ready_poll=0, bootstrap_wait=0))
                return 0.0
            rotation_fail(sid, "新しい会話が応答中のまま戻らない（ブートストラップ）")
            return None
        rotation_put(sid, attempts=dict(att, bootstrap_wait=waits + 1))
        return ROT_VERIFY_POLL
    if n >= ROT_BOOTSTRAP_TRIES:
        if target == "choices":
            rotation_put(sid, phase="verify_ready")   # choices は諦めて続行
            return 0.0
        rotation_fail(sid, "新しい会話にブートストラップを送れない")
        return None
    rotation_put(sid, attempts=dict(att, **{key: n + 1, "bootstrap_wait": 0}))   # attempts は丸ごと置換
    ok, msg, retry = send_poke(cid, full=True, model=model or "", wait=ROT_BOOTSTRAP_WAIT)
    if not ok:
        print(f"[rot] {sid[:6]} bootstrap {target} NG: {msg}")
        return 2.0 if retry else 5.0
    rotation_put(sid, phase=nxt)
    return 0.0


ROTATION_STEPS["bootstrap_main"] = lambda sid: _rot_bootstrap(sid, "main")
ROTATION_STEPS["bootstrap_choices"] = lambda sid: _rot_bootstrap(sid, "choices")


ROT_VERIFY_SEND = (
    "【疎通テスト】これはアプリの接続テストです。ここではコマンド（wait_input.py 等）を実行しないでください。"
    "ロールプレイ本文も outbox への JSON も書かず、返答は次の5文字だけにしてください:\n"
    f"{ROT_VERIFY_TEXT}\n"
    "（前置き・説明・コードブロック・記号・後書きは一切書かないこと。このテストは以後の会話の流れとは無関係です）"
)


def _rot_verify_ready(sid):
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        cid = r.get("new_ide") or ""
        att = r.get("attempts")
        att = att if isinstance(att, dict) else {}
        n = int(att.get("ready_poll") or 0)
    if not cid:
        rotation_fail(sid, "新しい会話が無い")
        return None
    try:
        idle_now, _st = ide_poke.idle(cid)
    except Exception:
        idle_now = True
    if not idle_now:
        if n * ROT_VERIFY_POLL > ROT_VERIFY_TIMEOUT:
            rotation_fail(sid, "新しい会話がブートストラップで止まらない")
            return None
        rotation_put(sid, attempts=dict(att, ready_poll=n + 1))
        return ROT_VERIFY_POLL
    # ブートストラップのコマンドが実際に走ったか（/api/pending?ide= 到達で mark_known される）
    if not ide_is_known(cid):
        print(f"[rot] {sid[:6]} bootstrap 未実行 → 送り直し")
        rotation_put(sid, phase="bootstrap_main", attempts=dict(att, ready_poll=0, bootstrap_wait=0))
        return 0.0
    rotation_put(sid, phase="verify_test", attempts=dict(att, ready_poll=0, verify_wait=0))
    return 0.0


ROTATION_STEPS["verify_ready"] = _rot_verify_ready


def _rot_verify_test(sid):
    """合否: 必須 = (1) ide_known (2) テスト送信より後の返事がある (3) エラーが無い。
    加点 = 「準備完了」を含む。I10: 残機（verify）は送信結果が出たときだけ +1。"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        cid = r.get("new_ide") or ""
        model = r.get("old_model") or ""
        att = r.get("attempts")
        att = att if isinstance(att, dict) else {}
        tries = int(att.get("verify") or 0)
        sent = bool(att.get("test_sent"))
        polls = int(att.get("verify_poll") or 0)
        waits = int(att.get("verify_wait") or 0)      # 送信前に応答中で待った回数
        after_step = int(att.get("verify_after_step") or 0)
    if not cid:
        rotation_fail(sid, "新しい会話が無い")
        return None

    # ---- 送信前: ブートストラップのターンが終わるのを待つ ----
    if not sent:
        try:
            idle_now, _ = ide_poke.idle(cid)
        except Exception:
            idle_now = True
        if not idle_now:
            # 待つだけで残機は減らさないが、**永久には待たない**（固まった会話で回転が止まる）
            if waits * ROT_VERIFY_POLL > ROT_VERIFY_TIMEOUT:
                return _rot_verify_retry(sid, "新しい会話が応答中のまま戻らない（疎通テスト前）")
            rotation_put(sid, attempts=dict(att, verify_wait=waits + 1))
            return ROT_VERIFY_POLL
        sm = {}
        try:
            sm = ide_poke.summary(cid) or {}
        except Exception:
            sm = {}
        after_step = int(sm.get("stepCount") or 0)     # ← テスト前のステップ番号を控える
        # テスト本文は「合図」ではなく普通のメッセージとして送る（send_poke は使わない）
        ok, msg, retry = send_poke_text(cid, ROT_VERIFY_SEND, model=model)
        if not ok:
            if retry:
                return ROT_VERIFY_POLL                # 会話が応答中。残機は減らさない
            rotation_put(sid, attempts=dict(att, verify=tries + 1, test_sent=False, verify_poll=0,
                                            verify_wait=0, verify_after_step=after_step))
            return _rot_verify_retry(sid, f"疎通テストを送れない: {msg}")
        rotation_put(sid, attempts=dict(att, verify=tries + 1, test_sent=True, verify_poll=0,
                                        verify_wait=0, verify_after_step=after_step))
        return ROT_VERIFY_POLL

    # ---- 送信後: まずターンが終わるのを待ってから読む ----
    if polls * ROT_VERIFY_POLL > ROT_VERIFY_TIMEOUT:
        return _rot_verify_retry(sid, "疎通テストの応答が終わらない（タイムアウト）")
    try:
        idle_now, _ = ide_poke.idle(cid)
    except Exception:
        idle_now = True
    if not idle_now:
        rotation_put(sid, attempts=dict(att, verify_poll=polls + 1))
        return ROT_VERIFY_POLL

    try:
        text, err, _ = ide_poke.last_reply(cid, after_step=after_step)
    except Exception as e:
        text, err = None, str(e)
    if err:
        return _rot_verify_retry(sid, f"新しい会話でエラー: {err}")
    if text is None:
        return _rot_verify_retry(sid, "新しい会話の返答を読めない")
    if not text:
        # テスト送信より後の返事がまだ無い（生成中 or 無出力）
        rotation_put(sid, attempts=dict(att, verify_poll=polls + 1))
        return ROT_VERIFY_POLL
    # 合格: コマンドが走っていて（known）、テスト後の返事が空でない。
    # 「準備完了」が無くても、AGENTS.md どおりの半角スペース1文字は正常なので合格にする。
    if not ide_is_known(cid):
        return _rot_verify_retry(sid, "ブートストラップのコマンドが実行されていない")
    print(f"[rot] {sid[:6]} verify OK ({'準備完了' if ROT_VERIFY_TEXT in text else 'space'})")
    rotation_put(sid, phase="cutover", attempts=dict(att, verify_poll=0))
    return 0.0


ROTATION_STEPS["verify_test"] = _rot_verify_test


def _rot_verify_retry(sid, why):
    """検証失敗。累積 verify は**据え置き**（残機は減るだけ）。ロック外で rename する。"""
    drop = None
    do_fail = False
    tries = 0
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        att = r.get("attempts")
        att = att if isinstance(att, dict) else {}
        tries = int(att.get("verify") or 0)
        cid = r.get("new_ide") or ""
        if cid:
            drop = (cid, "【失敗】" + (r.get("new_ide_title") or "世代交代の新規会話"))
        r["new_ide"] = ""                 # 失敗会話は予備に回さない（rotation_fail が拾わない）
        r["new_ide_title"] = ""
        # ラウンドの持ち越しだけリセット（verify の累積は据え置き＝残機は減るだけ）。
        # 次の会話は別の会話なので、待ち回数と送信前ステップ番号は必ず 0 から。
        r["attempts"] = dict(att, test_sent=False, verify_poll=0, verify_after_step=0,
                             verify_wait=0, bootstrap_wait=0)
        r["error"] = why
        do_fail = tries >= ROT_VERIFY_ROUNDS
        if not do_fail:
            r["phase"] = "spawn_main"
        s["setting"]["rotation"] = r
        save(s)
    if do_fail:
        rotation_fail(sid, why)           # ロック内処理のみ（RPC なし）
    if drop and ide_poke and hasattr(ide_poke, "rename"):
        try:
            ide_poke.rename(drop[0], drop[1])     # I2: RPC はロック外
        except Exception as e:
            print(f"[rot] {sid[:6]} rename failed conv: {e}")
    print(f"[rot] {sid[:6]} verify retry ({tries}/{ROT_VERIFY_ROUNDS}): {why}")
    return None if do_fail else 0.0


def prune_session_messages(s, keep_turns=ROTATION_KEEP_TURNS):
    """直近 keep_turns ユーザーターンだけ残す。states/images/choices は save() が同期プルーニングする。
    total_turns は**触らない**（I12）。"""
    msgs = s.get("messages", [])
    user_indices = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    if len(user_indices) > keep_turns:
        cut = user_indices[-keep_turns]
        s["messages"] = msgs[cut:]
        # 画像の生成待ちが迷子にならないよう、消える mid をキューから落とす
        remain = {m.get("id") for m in s["messages"] if m.get("id")}
        for _ in range(len(queued_images)):
            try:
                q_sid, q_mid = queued_images.popleft()
            except IndexError:
                break
            if q_sid != s.get("id") or q_mid in remain:
                queued_images.append((q_sid, q_mid))
    return s


def _rot_cutover(sid):
    r = {}
    cid = ""
    sync_agents_needed = False
    sync_choices_needed = False
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        st = s["setting"]
        r = rotation_state(s)
        if not r or r.get("phase") != "cutover":
            return None
        if not rotation_idle(s):
            # I7: 未応答が残っている間は絶対に切り替えない（二重応答の防止）
            ws = r.get("wait_since") or now()
            r["wait_since"] = ws
            st["rotation"] = r
            save(s)
            if age(ws) > ROT_WATCHDOG_SEC:
                rotation_fail(sid, "未応答の発言が残ったまま10分経過")
                return None
            return 1.0
        r.pop("wait_since", None)     # idle に戻ったら待ち開始をクリア（watchdog 誤発火防止）

        cid = r.get("new_ide") or ""
        if not cid:
            r["phase"] = "spawn_main"
            st["rotation"] = r
            save(s)
            return 0.0
        old_ide = st.get("ide_session") or ""
        if old_ide and r.get("old_ide") and old_ide != r.get("old_ide"):
            rotation_fail(sid, "世代交代中に担当の会話が手動で変更された")
            return None
        old_choices = st.get("choices_ide_session") or ""
        if r.get("new_choices_ide") and old_choices and r.get("old_choices_ide") and old_choices != r.get("old_choices_ide"):
            rotation_fail(sid, "世代交代中に選択肢の会話が手動で変更された")
            return None

        st["ide_session"] = cid
        st["ide_title"] = r.get("new_ide_title") or ""
        st["ide_auto"] = False
        if r.get("new_choices_ide"):
            st["choices_ide_session"] = r["new_choices_ide"]
            st["choices_ide_title"] = r.get("new_choices_ide_title") or ""
            st["choices_ide_auto"] = False
            if not (st.get("choices_ide_model") or "").strip() and r.get("old_choices_model"):
                st["choices_ide_model"] = r["old_choices_model"]

        # 通算ターンは触らない（I12）。剪定だけ行う
        st["rotation_count"] = int(r.get("rot_no") or 0)
        st["last_rotation_turn"] = int(st.get("total_turns") or 0)
        st["turns_since_rotation"] = 0
        st["rotation_skip_until"] = 0
        st["rotation_notice"] = (
            f"🔄 セッションを世代交代しました（{now()[11:16]} / "
            f"第{int(r.get('rot_no') or 0) + 1}世代へ移行・通算{int(st.get('total_turns') or 0)}ターン）"
        )
        prune_session_messages(s)
        st.pop("rotation", None)
        st.pop("rotation_reserve", None)
        sync_agents_needed = bool(st.get("agents_md"))
        sync_choices_needed = bool(r.get("new_choices_ide"))
        save(s)
        # 送信履歴は切替と同時にリセット（次は全部送る）
        write_sent(sid, {"ide": "", "msgs": [], "reset": True, "ts": now(),
                         "why": "セッション自動ローテーション（世代交代）"})

    # ---- ロック外 ----
    if sync_agents_needed or sync_choices_needed:
        try:
            with lock:
                s2 = load(sid)
                if sync_agents_needed and s2["setting"].get("agents_md"):
                    sync_agents(s2)      # 新しい ide で AGENTS.md を書き直す
                if sync_choices_needed and s2["setting"].get("choices_ide_session"):
                    sync_choices_agents(s2)
                save(s2)
        except Exception as e:
            print(f"[rot] {sid[:6]} agents error: {e}")
    # 旧会話の rename（実際に差し替えたものだけ。失敗しても運用に影響しない）
    old_roles = [(r.get("old_ide"), r.get("old_ide_title"))]
    if r.get("new_choices_ide"):
        old_roles.append((r.get("old_choices_ide"), r.get("old_choices_ide_title")))
    for oid, otitle in old_roles:
        if oid and ide_poke and hasattr(ide_poke, "rename"):
            try:
                if not (otitle or "").startswith("【旧】"):
                    ide_poke.rename(oid, "【旧】" + (otitle or "旧会話"))
            except Exception as e:
                print(f"[rot] {sid[:6]} old rename error: {e}")
    print(f"[rot] {sid[:6]} cutover -> {cid[:8]} (rot {r.get('rot_no')})")
    poke_event.set()          # 次ターンの合図を即許可
    return None


ROTATION_STEPS["cutover"] = _rot_cutover



AUTO_RETRY = 15  # Antigravity がまだ起動していないとき、新規会話を作り直すまでの秒数


def ide_auto_step(told=False):
    """メイン会話および選択肢専用会話（choices_ide_session）の自動作成を1回実行する"""
    on = ide_poke and hasattr(ide_poke, "new_conversation") and app_settings().get("new_channel_ide") is not False
    want = []
    with lock:
        for sid in list_ids():
            s = load(sid, readonly=True)
            st = s["setting"]
            # メイン会話がある（または作成中）のに選択肢会話が未設定（またはメインと同一）なら自動作成対象にする
            auto_pair_choices = (
                on
                and st.get("suggest_choices", True)
                and (bool(st.get("ide_session")) or bool(st.get("ide_auto")))
                and not has_parallel_choices_ide(st)
                and not st.get("choices_ide_manual_off")
            )
            if not st.get("ide_auto") and not st.get("choices_ide_auto") and not auto_pair_choices:
                continue
            s = load(sid)
            st = s["setting"]
            if not on:
                st["ide_auto"] = False
                st["choices_ide_auto"] = False
                save(s)
                continue
            if auto_pair_choices:
                st["choices_ide_auto"] = True
            need_main = bool(st.get("ide_auto") and not st.get("ide_session"))
            need_choices = bool(st.get("choices_ide_auto") and not has_parallel_choices_ide(st))
            if st.get("ide_auto") and st.get("ide_session"):
                st["ide_auto"] = False
            if st.get("choices_ide_auto") and has_parallel_choices_ide(st):
                st["choices_ide_auto"] = False
            save(s)
            if need_main and need_choices:
                want.append((sid, "both"))
            elif need_main:
                want.append((sid, "main"))
            elif need_choices:
                want.append((sid, "choices"))
    for sid, target in want:
        cid, _, msg, _ = new_ide(sid, auto=True, target=target)
        if cid:
            poke_event.set()
        elif not told:
            print(f"[ide] Antigravity 会話({target})はまだ作れていない: {msg}（Antigravity が起動したら作る）")
            told = True
    return told


def ide_auto_worker():
    """メイン会話および選択肢専用会話（choices_ide_session）を自動作成する常駐ワーカー"""
    told = False
    while True:
        try:
            told = ide_auto_step(told)
        except Exception as e:
            print(f"[ide auto] {e}")
        ide_auto_event.wait(timeout=AUTO_RETRY)
        ide_auto_event.clear()


DIRECT_MAX = int(os.environ.get("RPCHAT_DIRECT_MAX", 40000))


def direct_send_on(st):
    if POKE_MODE != "rpc":
        return False
    env = os.environ.get("RPCHAT_DIRECT_SEND")
    if env == "0":
        return False
    return env == "1" or bool(st.get("direct_send"))


def direct_poke(ide, sid, mid, retry=False):
    """(ok, retry, fallback, msg)
    retry=True: 直送済み(processing/direct)の再送。
    Gemini のフィルターは同じ文面でも通ることがあるので、打ち切り前に再送する"""
    if ide_poke is None:
        return False, False, False, "ide_poke が利用できない"
    with lock:
        s = load(sid)
        m = find_msg(s, mid)
        if not m:
            return False, False, False, "消えている"
        if retry:
            if not (m.get("status") == "processing" and m.get("direct")):
                return False, False, False, "再送の対象ではない"
        elif m.get("status") != "pending":
            return False, False, False, "もう受け取られている"
        ctx = build_context(s, m, record=False)
        text = render_input_text(sid, m, ctx, None, ide, direct=True)
        if retry:  # 前回の拒否文に引きずられないよう、先頭で打ち消す（本文は変えない）
            text = ("（前回の応答は無視する。同じ入力の再送。前回の返事には触れず、"
                    "この指示だけに従って outbox に JSON を書く）\n" + text)
        if len(text) > DIRECT_MAX:
            return False, False, True, f"{len(text)}字（上限 {DIRECT_MAX}）"
        need_media = bool(m.get("files")) and not m.get("pasted")
        m_files_copy = copy.deepcopy(m.get("files") or []) if need_media else []
    media = paste_media([{"files": m_files_copy}]) if need_media else []
    with get_poke_lock(ide):
        try:
            ok, msg, retry_send = ide_poke.send_to_conversation(ide, text, ide_model_of(ide), media or None)
        except Exception as e:
            ok, msg, retry_send = False, f"会話に送れなかった: {e}", False
    if not retry_send:
        poke_log[ide] = {"ts": now(), "ok": ok, "msg": ("（直送）" if ok else "（直送失敗）") + msg}
    if not ok:
        return False, retry_send, False, msg
    with lock:
        s = load(sid)
        m = find_msg(s, mid)
        if not m:
            # 送信中に停止された: IDE には届いているので、次は全部送り直す
            sent = read_sent(sid)
            sent.update({"ide": "", "msgs": [], "reset": True, "ts": now(),
                         "why": "送信中に停止された（その発言と応答は無かったものとして扱う）"})
            write_sent(sid, sent)
            return True, False, False, "送信中に停止された（次は全部送り直す）"
        if not retry:
            if m.get("status") != "pending":
                # 編集・作り直しなどで入力が変わった。安全側で全部送り直す
                sent = read_sent(sid)
                sent.update({"ide": "", "msgs": [], "reset": True, "ts": now(),
                             "why": "送信中に入力が変わった（全部送り直す）"})
                write_sent(sid, sent)
                return True, False, False, "送信中に入力が変わった"
            m["status"] = "processing"
            claimed_at[mid] = time.time()
        else:
            if m.get("status") != "processing":
                # 再送中に既に応答が取り込まれたか、入力が変わった。
                # 応答取り込み済みのメッセージや sent 記録を上書きして壊さないよう早期リターン
                return True, False, False, "再送中に既に応答が取り込まれた"
        m["direct"] = True
        m["direct_ts"] = now()
        if media:
            m["pasted"] = True
        save(s)
        if not retry:
            write_sent(sid, ctx["_sent"])
    return True, False, False, msg


def poke_worker():
    """入力が来て POKE_DELAY 秒たっても応答がなければ、担当IDEに合図する（1つの入力につき1回）。
    応答を取り込んだ直後（返事のスペースを打っているところ）は待つ。
    rpc のときは、会話が応答中かどうかを send_to_conversation が会話の状態で確かめる。受け取り済み（processing）でも
    会話が止まっていれば合図する（裏で動いていた wait_input.py が受け取って、誰も読んでいないことがある。
    合図で実行された wait_input.py が、応答の届いていない入力として出し直す）。
    window のときは会話の状態が分からないので、受け取り済み・wait_input 待機中は応答中とみなす。
    スペースだけでは AI が wait_input.py を実行しないことがある（分岐や別の作業で会話の最後が別の話になっているとき）。
    そのチャンネルをこの会話に送ったことがなければ、最初からコマンドを送る。
    スペースを送って POKE_AGAIN 秒たっても受け取られなければ、コマンドを1回だけ送り直す"""
    rpc = POKE_MODE != "window"
    since, done, quiet = {}, set(), {}
    poked, again = {}, set()  # 合図した入力 -> そのときの時刻 / コマンドを送り直した入力
    direct_watch = {}
    while True:
        poke_event.wait(timeout=0.3)
        poke_event.clear()
        try:
            busy, cands, unclaimed, unseen = set(), {}, {}, set()
            fresh_sid_map = {}
            direct_sids = set()
            direct_live = set()
            with lock:
                for sid in list_ids():
                    cached_ide = session_ide_cache.get(sid)
                    if cached_ide is not None and not any(cached_ide):
                        continue
                    s = load(sid, readonly=True)
                    st, msgs = s["setting"], s["messages"]
                    if direct_send_on(st):
                        direct_sids.add(sid)
                    ide = st.get("ide_session")
                    has_par = has_parallel_choices_ide(st)
                    choices_ide = st.get("choices_ide_session") if has_par else None
                    if not ide and not choices_ide:
                        continue

                    for m in msgs:
                        mid = m.get("id")
                        if mid:
                            fresh_sid_map[mid] = sid
                        if m.get("status") == "processing" and m.get("direct_ts") and not m.get("direct_off"):
                            direct_live.add(m["id"])

                    if not rpc:
                        for m in msgs:
                            mid = m.get("id")
                            if m.get("choices_status") == "processing":
                                ch_target = choices_ide if has_par else ide
                                if ch_target and time.time() - claimed_at.get(f"{mid}:choices", claimed_at.get(mid, 0)) < 300:
                                    busy.add(ch_target)
                            if m.get("choices_updated_ts") and age(m.get("choices_updated_ts")) < 6 and not m.get("choices_status"):
                                ch_target = choices_ide if has_par else ide
                                if ch_target:
                                    busy.add(ch_target)

                            if ide:
                                if m["role"] == "user" and m.get("status") == "processing":
                                    if time.time() - claimed_at.get(mid, 0) < 300:
                                        busy.add(ide)
                                if m.get("image_refine_status") == "processing" and time.time() - claimed_at.get(f"{mid}:image_refine", claimed_at.get(mid, 0)) < 300:
                                    busy.add(ide)
                                if m["role"] == "assistant":
                                    if m.get("memory_status") == "processing" and time.time() - claimed_at.get(f"{mid}:memory", claimed_at.get(mid, 0)) < 300:
                                        busy.add(ide)
                                    if m.get("consolidation_status") == "processing" and time.time() - claimed_at.get(f"{mid}:consolidation", claimed_at.get(mid, 0)) < 300:
                                        busy.add(ide)
                                    if age(m.get("ts")) < 6 and not m.get("status"):
                                        busy.add(ide)

                    # 1. メインIDE向けタスク
                    cand_main = None
                    if ide:
                        p_user = next((m for m in msgs if m["role"] == "user" and m.get("status") in ("pending", "processing")), None)
                        p_img_refine = None
                        p_choices_seq = None
                        p_mem = None
                        p_consolidation = None
                        if not p_user:
                            p_img_refine = next((m for m in msgs if m.get("image_refine_status") in ("pending", "processing")), None)
                        if not p_user and not p_img_refine and not has_par:
                            p_choices_seq = next((m for m in msgs if m["role"] == "assistant" and m.get("choices_status") in ("pending", "processing")), None)
                        if not p_user and not p_img_refine and not p_choices_seq:
                            p_mem = next((m for m in msgs if m["role"] == "assistant" and m.get("memory_status") in ("pending", "processing")), None)
                        if not p_user and not p_img_refine and not p_choices_seq and not p_mem:
                            p_consolidation = next((m for m in msgs if m["role"] == "assistant" and m.get("consolidation_status") in ("pending", "processing")), None)

                        if p_user:
                            task_key = p_user["id"]
                            cand_main = (task_key, p_user, "user", sid)
                            if p_user.get("status") == "pending" and task_key in poked and task_key not in again:
                                unclaimed.setdefault(ide, []).append(task_key)
                        elif p_img_refine:
                            task_key = f"{p_img_refine['id']}:image_refine:{p_img_refine.get('image_refine_ts')}"
                            fresh_sid_map[task_key] = sid
                            cand_main = (task_key, p_img_refine, "image_refine", sid)
                            if p_img_refine.get("image_refine_status") == "pending" and task_key in poked and task_key not in again:
                                unclaimed.setdefault(ide, []).append(task_key)
                        elif p_choices_seq:
                            task_key = f"{p_choices_seq['id']}:choices:{p_choices_seq.get('choices_ts')}"
                            fresh_sid_map[task_key] = sid
                            cand_main = (task_key, p_choices_seq, "choices", sid)
                            if p_choices_seq.get("choices_status") == "pending" and task_key in poked and task_key not in again:
                                unclaimed.setdefault(ide, []).append(task_key)
                        elif p_mem:
                            task_key = f"{p_mem['id']}:memory:{p_mem.get('memory_ts')}"
                            fresh_sid_map[task_key] = sid
                            cand_main = (task_key, p_mem, "memory_extract", sid)
                            if p_mem.get("memory_status") == "pending" and task_key in poked and task_key not in again:
                                unclaimed.setdefault(ide, []).append(task_key)
                        elif p_consolidation:
                            task_key = f"{p_consolidation['id']}:consolidation:{p_consolidation.get('consolidation_ts')}"
                            fresh_sid_map[task_key] = sid
                            cand_main = (task_key, p_consolidation, "consolidation", sid)
                            if p_consolidation.get("consolidation_status") == "pending" and task_key in poked and task_key not in again:
                                unclaimed.setdefault(ide, []).append(task_key)

                    # 2. 選択肢IDE向けタスク（パラレルモード）
                    cand_choices = None
                    if choices_ide:
                        p_ch = next((m for m in msgs if m.get("choices_status") in ("pending", "processing")), None)
                        if p_ch:
                            orig_id = p_ch.get("choices_orig_id") or p_ch["id"]
                            task_key = f"{orig_id}:choices:{p_ch.get('choices_ts')}"
                            fresh_sid_map[task_key] = sid
                            cand_choices = (task_key, p_ch, "choices", sid)
                            if p_ch.get("choices_status") == "pending" and task_key in poked and task_key not in again:
                                unclaimed.setdefault(choices_ide, []).append(task_key)

                    def pick_cand(target_ide, cand_item, is_choices_target=False):
                        if not target_ide or not cand_item or cand_item[0] in done:
                            return
                        sent_key = "choices_ide" if is_choices_target else "ide"
                        cur = cands.get(target_ide)
                        if not cur:
                            cands[target_ide] = cand_item
                            if read_sent(sid).get(sent_key) != target_ide:
                                unseen.add(target_ide)
                        else:
                            type_prio = {"user": 1, "image_refine": 2, "choices": 3, "memory_extract": 4, "consolidation": 5}
                            cur_prio = type_prio.get(cur[2], 99)
                            new_prio = type_prio.get(cand_item[2], 99)
                            if new_prio < cur_prio:
                                cands[target_ide] = cand_item
                                if read_sent(sid).get(sent_key) != target_ide:
                                    unseen.add(target_ide)
                            elif new_prio == cur_prio:
                                cur_ts = cur[1].get("image_refine_ts") or cur[1].get("choices_ts") or cur[1].get("consolidation_ts") or cur[1].get("memory_ts") or cur[1].get("ts", "")
                                new_ts = cand_item[1].get("image_refine_ts") or cand_item[1].get("choices_ts") or cand_item[1].get("consolidation_ts") or cand_item[1].get("memory_ts") or cand_item[1].get("ts", "")
                                if new_ts < cur_ts:
                                    cands[target_ide] = cand_item
                                    if read_sent(sid).get(sent_key) != target_ide:
                                        unseen.add(target_ide)

                    pick_cand(ide, cand_main, is_choices_target=False)
                    pick_cand(choices_ide, cand_choices, is_choices_target=True)

                    # 会話の自動ローテーション（世代交代）チェック（保険）:
                    if rotation_state(s):
                        pass
                    elif user_turns_of(msgs) >= ROTATION_TURNS and not has_pending:
                        if rotation_maybe_arm(s):
                            save(s)
            t = time.time()
            for k in list(direct_watch):
                if k not in direct_live:
                    direct_watch.pop(k, None)
            busy |= reverting | {v["ide"] for v in list(revert_plan.values()) if t - v["t"] < REVERT_HOLD}
            for ide_k, ids in unclaimed.items():  # スペースは届いたのに wait_input.py が実行されていない
                if ide_k in busy or t < quiet.get(ide_k, 0) or t - max(poked[i] for i in ids) < POKE_AGAIN:
                    continue
                ok, msg, retry = send_poke(ide_k, full=True)
                if retry:
                    quiet[ide_k] = time.time() + 0.5  # 3秒ではなく0.5秒後にスマートリトライ
                    continue
                again.update(ids)
                quiet[ide_k] = time.time() + (1.5 if rpc else 15)

            to_poke = []
            for ide_k, cand in list(cands.items()):
                task_key, m, task_type, sid = cand

                if (rpc and task_type == "user" and sid in direct_sids
                        and m.get("direct_ts") and not m.get("direct_off") and ide_k not in busy):
                    w = direct_watch.setdefault(m["id"], {"tries": 0, "idle_since": 0,
                                                          "seen_running": False, "born": time.time(), "next": 0})
                    if time.time() >= w["next"]:
                        is_idle = ide_poke.idle(ide_k)[0] if ide_poke else True
                        if not is_idle:
                            w["seen_running"] = True          # 生成が始まったのを確認
                            w["idle_since"] = 0
                        else:
                            if not w["idle_since"]:
                                w["idle_since"] = time.time()
                            idle_for = time.time() - w["idle_since"]
                            # 起動ラグ対策: RUNNING を一度見た、または送信から20秒経った
                            started = w["seen_running"] or (time.time() - w["born"] >= 20)
                            if idle_for >= 5 and started:     # ターンは終わったのに応答が無い
                                w["idle_since"] = 0
                                give_up = False
                                if w["tries"] < 2:
                                    # フィルターは確率的。同じ文面の再送で通ることがある
                                    ok, ret, fb, msg = direct_poke(ide_k, sid, m["id"], retry=True)
                                    if ok:
                                        w["tries"] += 1
                                        w["seen_running"] = False
                                        w["born"] = time.time()
                                    elif fb:
                                        # サイズ超過などで再送不可 -> 即座に従来へフォールバック
                                        give_up = True
                                    elif not ret:
                                        # 一時的な話中(ret=True)ではなく送信エラー -> 試行回数としてカウント
                                        w["tries"] += 1
                                        if w["tries"] >= 2:
                                            give_up = True
                                    print(f"[direct] retry {w['tries']} {m['id'][:8]} ok={ok} fb={fb} {msg}")
                                else:
                                    give_up = True

                                if give_up:
                                    # 2回ダメ → direct を外して「left」の対象に戻し、wait_input に全文で書き直させる
                                    with lock:
                                        s2 = load(sid)
                                        m2 = find_msg(s2, m["id"])
                                        if m2 and m2.get("status") == "processing":
                                            m2.pop("direct", None)
                                            m2["direct_off"] = True
                                            save(s2)
                                    old = read_sent(sid)
                                    old["reset"] = True
                                    old["why"] = "直送に応答が無かったので、全部送り直す"
                                    write_sent(sid, old)
                                    send_poke(ide_k, full=True,
                                              prefix="（直送に応答がなかった。ここから従来方式に戻す。"
                                                     "前の『wait_input.py は実行しない』は無効。次のコマンドを実行すること）\n")
                                    print(f"[direct] give up {m['id'][:8]} -> legacy(left)")
                                    direct_watch.pop(m["id"], None)
                                    done.add(m["id"])
                                    quiet[ide_k] = time.time() + 15
                        w["next"] = time.time() + 3
                    since.pop(task_key, None)
                    continue

                if ide_k in busy or t < quiet.get(ide_k, 0) or (not rpc and t - ide_waiting.get(ide_k, 0) < 4):
                    since.pop(task_key, None)
                    continue
                t_ref = m.get("image_refine_ts") if task_type == "image_refine" else (m.get("choices_ts") if task_type == "choices" else (m.get("consolidation_ts") if task_type == "consolidation" else (m.get("memory_ts") if task_type == "memory_extract" else m.get("ts"))))
                if age(t_ref) >= 600:
                    done.add(task_key)
                    continue
                since.setdefault(task_key, t)
                if t - since[task_key] >= POKE_DELAY:
                    to_poke.append((ide_k, cand))
                    # （以前はここで発言に「合図した会話」を刻んでいたが廃止した。理由は /api/pending の
                    #   コメントを参照。ここは lock の外なので、load して save すると、その間に
                    #   /api/send が足した発言を巻き戻して消してしまう事故も起きていた）

            def dispatch_poke(target_ide, item):
                task_k, m_item, ttype, item_sid = item
                full = target_ide in unseen
                media = paste_media([m_item]) if (rpc and ttype == "user" and m_item.get("files") and not m_item.get("pasted")) else []

                if rpc and ttype == "user" and item_sid in direct_sids and not m_item.get("direct_off"):
                    ok, retry, fb, msg = direct_poke(target_ide, item_sid, m_item["id"])
                    if not fb:
                        if retry:
                            quiet[target_ide] = time.time() + 0.5
                            return
                        print(f"[direct] {'ok' if ok else 'NG'} {item_sid[:8]} {msg}")
                        quiet[target_ide] = time.time() + 1.5
                        return
                    # サイズ超過だけ従来へ（media は既に作っている）

                ok, msg, retry = send_poke(target_ide, full, media)
                if retry:  # 会話が応答中。終わるのを待って送り直す
                    quiet[target_ide] = time.time() + 0.5  # 0.5秒後にリトライ
                    return
                done.add(task_k)
                if ok and media:
                    with lock:
                        x = fresh_sid_map.get(m_item["id"]) or session_of_msg(m_item["id"])
                        s = load(x) if x else None
                        target_m = s and find_msg(s, m_item["id"])
                        if target_m and target_m.get("files"):
                            target_m["pasted"] = True  # wait_input.py の表示で「チャット欄にも貼った」と伝える
                            save(s)
                if ok:
                    poked[task_k] = time.time()
                    if full:
                        again.add(task_k)
                quiet[target_ide] = time.time() + (1.5 if rpc else 15)

            if to_poke:
                if POKE_MODE == "window" or len(to_poke) == 1:
                    for target_ide, item in to_poke:
                        dispatch_poke(target_ide, item)
                else:
                    poke_threads = [threading.Thread(target=dispatch_poke, args=(target_ide, item)) for target_ide, item in to_poke]
                    for th in poke_threads:
                        th.start()
                    for th in poke_threads:
                        th.join()
        except Exception as e:
            print(f"[poke worker] {e}")


def normalize_choices(raw_choices):
    if isinstance(raw_choices, str):
        try:
            raw_choices = json.loads(raw_choices)
        except Exception:
            raw_choices = None
    if not isinstance(raw_choices, list):
        return []
    normalized = []
    for item in raw_choices:
        if isinstance(item, dict):
            raw_text = item.get("text")
            if isinstance(raw_text, (dict, list)):
                raw_text = ""
            text_val = str(raw_text or "").strip()
            raw_label = item.get("label")
            if isinstance(raw_label, (dict, list)):
                raw_label = ""
            label_val = str(raw_label or "候補").strip() or "候補"
            if text_val:
                normalized.append({
                    "label": label_val,
                    "text": text_val,
                })
        elif isinstance(item, str) and item.strip():
            normalized.append({
                "label": "候補",
                "text": item.strip(),
            })
        if len(normalized) == 8:
            break
    return normalized


# ---------------------------------------------------------------- outbox
def safe_move(src, dst):
    if not os.path.exists(src):
        return
    try:
        shutil.move(src, dst)
    except OSError:
        pass


def cleanup_done_dir(keep=500):
    """長期運用で outbox/done/ に数万ファイルが溜まり os.scandir が劣化するのを防ぐため、古い完了ファイルを整理する"""
    if not os.path.isdir(DONE):
        return
    try:
        with os.scandir(DONE) as it:
            files = [e for e in it if e.is_file()]
        if len(files) <= keep:
            return
        files.sort(key=lambda e: e.name)
        for e in files[:-keep]:
            try:
                os.remove(e.path)
            except OSError:
                pass
    except OSError:
        pass


_OUTBOX_KEY_COLON = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"')


def repair_outbox_text(txt):
    """AI が書き損じた JSON を最小限だけ直す。直せなければ None。
    - "key: "value"  → "key": "value"    (閉じ引用符の欠落)
    - 末尾カンマ
    生の改行/タブは json.loads(strict=False) 側で吸収する。"""
    fixed, notes = txt, []
    f2 = _OUTBOX_KEY_COLON.sub(r'"\1": "', fixed)
    if f2 != fixed:
        notes.append("key: の閉じ引用符欠落")
        fixed = f2
    f3 = re.sub(r",(\s*[}\]])", r"\1", fixed)
    if f3 != fixed:
        notes.append("末尾カンマ")
        fixed = f3
    if not notes:
        return None
    try:
        obj = json.loads(fixed, strict=False)
    except ValueError:
        return None
    return (obj, notes) if isinstance(obj, dict) else None


def process_outbox():
    if not os.path.exists(DONE):
        os.makedirs(DONE, exist_ok=True)
    imported = False
    try:
        entries = [e.name for e in os.scandir(OUTBOX) if e.is_file() and e.name.lower().endswith(".json")]
    except OSError:
        return False
    if not entries:
        return False
    for name in sorted(entries):
        ready_sid = ""
        path = os.path.join(OUTBOX, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                raw = f.read()
            try:
                rep = json.loads(raw, strict=False)      # 文字列内の生改行を許す
                if not isinstance(rep, dict):
                    raise ValueError("JSON がオブジェクトではない")
            except ValueError:
                repaired = repair_outbox_text(raw)
                if not repaired:
                    raise
                rep, notes = repaired
                print(f"[outbox] repaired {name}: {', '.join(notes)}")
        except Exception as e:
            if not os.path.exists(path):
                continue
            if time.time() - os.path.getmtime(path) > 10:
                print(f"[outbox] invalid json {name}: {e}")
                safe_move(path, os.path.join(DONE, name + ".bad"))
            continue

        with lock:
            sid = rep.get("session") or rep.get("session_id") or (rep.get("reply_to") and session_of_msg(rep["reply_to"]))
            if not sid or not os.path.exists(spath(sid)):
                print(f"[outbox] no session for {name}")
                safe_move(path, os.path.join(DONE, name + ".nosession"))
                continue
            s = load(sid)
            reply_to = rep.get("reply_to")
            # consolidation（世代交代の睡眠プロセス）タスクの応答
            if rep.get("type") == "consolidation":
                target_asst = None
                if reply_to:
                    for m in s["messages"]:
                        if m["id"] == reply_to and m["role"] == "assistant":
                            target_asst = m
                            break
                if not target_asst:
                    for m in reversed(s["messages"]):
                        if m["role"] == "assistant":
                            target_asst = m
                            break
                if isinstance(rep.get("memo"), str) and rep["memo"].strip():
                    s["setting"]["memo"] = rep["memo"].strip()
                if isinstance(rep.get("elapsed_time"), dict):
                    raw_period = rep["elapsed_time"].get("period", s["setting"].get("elapsed_time", {}).get("period", "早朝"))
                    s["setting"]["elapsed_time"] = {
                        "day": int(rep["elapsed_time"].get("day", s["setting"].get("elapsed_time", {}).get("day", 1))),
                        "period": normalize_period(raw_period)
                    }
                    s["setting"]["turns_in_period"] = 0
                if rep.get("state"):
                    clean_rep_st = clean_state(rep["state"])
                    new_state = merge_state(state_before(s["messages"], len(s["messages"]), sid=sid), clean_rep_st)
                    if target_asst:
                        target_asst["state"] = new_state
                        turn_st_cons = [
                            {
                                "name": c.get("name", ""),
                                "pose": c.get("pose", ""),
                                "expression": list(c.get("expression") or [])
                            }
                            for c in clean_rep_st if isinstance(c, dict) and ("pose" in c or "expression" in c)
                        ]
                        if turn_st_cons:
                            target_asst["state_turn"] = turn_st_cons
                if target_asst:
                    target_asst.pop("consolidation_status", None)
                    target_asst.pop("consolidation_ts", None)
                    target_asst.pop("consolidation_claimed_ts", None)
                save(s)
                rotation_note_body(sid)
                safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                print(f"[outbox] processed consolidation {name} -> {sid}")
                imported = True
                continue

            # memory extraction for assistant message
            target_mem_asst = None
            if rep.get("type") == "memory_extract" or (rep.get("memories") is not None and not rep.get("text") and not rep.get("image")):
                if reply_to:
                    for m in s["messages"]:
                        if m["id"] == reply_to and m["role"] == "assistant":
                            target_mem_asst = m
                            break
                if not target_mem_asst and (rep.get("target_id") or rep.get("message_id")):
                    tid = rep.get("target_id") or rep.get("message_id")
                    for m in s["messages"]:
                        if m["id"] == tid and m["role"] == "assistant":
                            target_mem_asst = m
                            break
                if not target_mem_asst and reply_to:
                    for idx, m in enumerate(s["messages"]):
                        if m["id"] == reply_to and m["role"] == "user":
                            if idx + 1 < len(s["messages"]):
                                next_m = s["messages"][idx + 1]
                                if next_m["role"] == "assistant":
                                    target_mem_asst = next_m
                            break
                if not target_mem_asst:
                    for m in reversed(s["messages"]):
                        if m["role"] == "assistant" and m.get("memory_status") in ("pending", "processing"):
                            target_mem_asst = m
                            break
                if not target_mem_asst:
                    for m in reversed(s["messages"]):
                        if m["role"] == "assistant":
                            target_mem_asst = m
                            break
                if target_mem_asst:
                    turn_idx = calc_turn_idx(s["messages"], s["messages"].index(target_mem_asst), s["setting"].get("total_turns"))
                    ch_name = (s["setting"].get("character") or {}).get("name", "")
                    raw_el = target_mem_asst.get("elapsed_time") or s["setting"].get("elapsed_time") or {"day": 1, "period": "午前"}
                    el_snap = copy.deepcopy(raw_el) if isinstance(raw_el, dict) else {"day": 1, "period": "午前"}
                    target_mem_asst.pop("memory_status", None)
                    target_mem_asst.pop("memory_ts", None)
                    if not target_mem_asst.get("id"):
                        target_mem_asst["id"] = f"m_{int(time.time()*1000)}_asst"
                    asst_mid = target_mem_asst["id"]
                    save(s)
                    queued_memories.append(("explicit", sid, ch_name, rep.get("memories", []) or [], rep.get("resolved_ids", []) or [], turn_idx, asst_mid, el_snap))
                    memory_event.set()
                    poke_event.set()
                    safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                    print(f"[outbox] queued extracted memories {name} -> {sid}")
                    imported = True
                    continue
                else:
                    print(f"[outbox] stale memory reply {name}")
            # image prompt refine task response
            is_img_refine = (
                rep.get("type") == "image_refine"
                or rep.get("refined_prompt") is not None
                or (
                    reply_to
                    and not rep.get("choices")
                    and not rep.get("memories")
                    and not rep.get("memo")
                    and (rep.get("prompt") or (isinstance(rep.get("image"), dict) and rep["image"].get("prompt")))
                    and any(m["id"] == reply_to and m.get("image_refine_status") in ("pending", "processing") for m in s["messages"])
                )
            )
            if is_img_refine:
                target_img_m = None
                if reply_to:
                    for m in s["messages"]:
                        if m["id"] == reply_to:
                            target_img_m = m
                            break
                if not target_img_m and (rep.get("target_id") or rep.get("message_id")):
                    tid = rep.get("target_id") or rep.get("message_id")
                    for m in s["messages"]:
                        if m["id"] == tid:
                            target_img_m = m
                            break
                if not target_img_m:
                    for m in reversed(s["messages"]):
                        if m.get("image_refine_status") in ("pending", "processing"):
                            target_img_m = m
                            break
                if target_img_m:
                    if rep.get("error"):
                        target_img_m["image_refine_status"] = "error"
                        target_img_m["image_refine_error"] = str(rep["error"])
                        target_img_m.pop("image_refine_mode", None)
                        target_img_m.pop("image_refine_prompt", None)
                        target_img_m.pop("image_refine_ts", None)
                        target_img_m.pop("image_refine_claimed_ts", None)
                        save(s)
                        poke_event.set()
                        safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                        print(f"[outbox] image refine error {name} -> {sid}: {rep.get('error')}")
                        imported = True
                        continue

                    mid_target = target_img_m.get("id")
                    img_meta = load_images_data(sid, readonly=True).get(mid_target, {}) if mid_target else {}
                    m_state = target_img_m.get("state") or (load_states_data(sid, readonly=True).get(mid_target) if mid_target else None)
                    m_state_turn = target_img_m.get("state_turn")
                    raw_p = (
                        rep.get("refined_prompt")
                        or rep.get("prompt")
                        or (rep.get("image", {}).get("prompt") if isinstance(rep.get("image"), dict) else None)
                        or rep.get("text")
                        or ""
                    )
                    refined_prompt = sanitize_prompt(
                        raw_p,
                        context_text=image_tag_context(s, target_img_m, target_img_m.get("text") or ""),
                        state=m_state,
                        allow_inject=True,
                        state_turn=m_state_turn,
                    )
                    if not refined_prompt:
                        target_img_m["image_refine_status"] = "error"
                        target_img_m["image_refine_error"] = "有効なプロンプトタグが得られませんでした"
                        target_img_m.pop("image_refine_mode", None)
                        target_img_m.pop("image_refine_prompt", None)
                        target_img_m.pop("image_refine_ts", None)
                        target_img_m.pop("image_refine_claimed_ts", None)
                        save(s)
                        poke_event.set()
                        safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                        print(f"[outbox] empty image refine prompt {name} -> {sid}")
                        imported = True
                        continue

                    refine_mode = rep.get("image_refine_mode") or target_img_m.get("image_refine_mode") or "card"
                    if not isinstance(target_img_m.get("image"), dict):
                        target_img_m["image"] = {}

                    old_prompt = target_img_m["image"].get("prompt") or img_meta.get("prompt", "")
                    cur_chars = target_img_m["image"].get("chars") or img_meta.get("chars") or []
                    if not cur_chars:
                        resolved = resolve_image_chars(
                            s.get("setting") or {},
                            msg_text=target_img_m.get("text", ""),
                            active_state=m_state,
                            prompt_text=refined_prompt or old_prompt
                        )
                        cur_chars = [c["name"] for c in resolved if c.get("name")]
                    else:
                        norm_chars = []
                        for cc in (cur_chars if isinstance(cur_chars, list) else [cur_chars]):
                            if isinstance(cc, dict) and cc.get("name"):
                                norm_chars.append(str(cc["name"]))
                            elif isinstance(cc, str) and cc.strip():
                                norm_chars.append(cc.strip())
                        cur_chars = norm_chars
                    if cur_chars:
                        target_img_m["image"]["chars"] = cur_chars
                    target_img_m["image"]["refined"] = True
                    if refine_mode == "card":
                        target_img_m["image"]["prev_prompt"] = old_prompt
                        target_img_m["image"]["prompt"] = refined_prompt
                        target_img_m["image"]["manual"] = None
                        target_img_m["image"]["refined_prompt"] = None
                        target_img_m["image"]["seed"] = random.randint(0, 2**32 - 1)
                        target_img_m["image"]["status"] = "queued"
                        target_img_m["image"].pop("error", None)
                        queued_images.append((sid, target_img_m["id"]))
                        image_event.set()
                    else:  # modal
                        target_img_m["image"]["prev_prompt"] = target_img_m.get("image_refine_prompt") or old_prompt
                        target_img_m["image"]["refined_prompt"] = refined_prompt

                    target_img_m.pop("image_refine_status", None)
                    target_img_m.pop("image_refine_mode", None)
                    target_img_m.pop("image_refine_prompt", None)
                    target_img_m.pop("image_refine_ts", None)
                    target_img_m.pop("image_refine_claimed_ts", None)
                    target_img_m.pop("image_refine_error", None)
                    save(s)
                    poke_event.set()
                    safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                    print(f"[outbox] refined image prompt ({refine_mode}) {name} -> {sid}")
                    imported = True
                    continue
                else:
                    print(f"[outbox] stale image refine reply {name}")
                    safe_move(path, os.path.join(DONE, name + ".stale"))
                    continue

            # choices-only regeneration for assistant message or parallel pre-generation for user message
            is_choices_rep = rep.get("type") == "choices" or (rep.get("choices") is not None and not rep.get("text"))
            if is_choices_rep:
                target_asst = None
                target_user = None
                if reply_to:
                    for m in s["messages"]:
                        if m["id"] == reply_to and m["role"] == "assistant":
                            target_asst = m
                            break
                        elif m.get("choices_orig_id") == reply_to and m["role"] == "assistant":
                            target_asst = m
                            break
                if not target_asst and (rep.get("target_id") or rep.get("message_id")):
                    tid = rep.get("target_id") or rep.get("message_id")
                    for m in s["messages"]:
                        if m["id"] == tid and m["role"] == "assistant":
                            target_asst = m
                            break
                if not target_asst and reply_to:
                    for idx, m in enumerate(s["messages"]):
                        if m["id"] == reply_to and m["role"] == "user":
                            if idx + 1 < len(s["messages"]):
                                next_m = s["messages"][idx + 1]
                                if next_m["role"] == "assistant":
                                    target_asst = next_m
                            else:
                                if m.get("status") in ("pending", "processing") or m.get("choices_status") in ("pending", "processing"):
                                    target_user = m
                            break
                if not target_asst and not target_user:
                    for m in reversed(s["messages"]):
                        if m["role"] == "assistant" and m.get("choices_status") in ("pending", "processing"):
                            target_asst = m
                            break
                if not target_asst and not target_user:
                    for m in reversed(s["messages"]):
                        if m["role"] == "user" and (m.get("choices_status") in ("pending", "processing") or m.get("status") in ("pending", "processing")):
                            target_user = m
                            break

                if target_asst:
                    normalized_choices = normalize_choices(rep.get("choices"))
                    if normalized_choices:
                        target_asst["choices"] = normalized_choices
                    target_asst.pop("choices_status", None)
                    target_asst.pop("choices_type", None)
                    target_asst.pop("choices_ts", None)
                    target_asst.pop("choices_claimed_ts", None)
                    target_asst.pop("choices_orig_id", None)
                    target_asst.pop("staged_choices", None)
                    if reply_to:
                        u_prev = find_msg(s, reply_to)
                        if u_prev and u_prev.get("role") == "user":
                            u_prev.pop("choices_status", None)
                            u_prev.pop("choices_type", None)
                            u_prev.pop("choices_ts", None)
                            u_prev.pop("choices_claimed_ts", None)
                            u_prev.pop("choices_orig_id", None)
                            u_prev.pop("staged_choices", None)
                    target_asst["choices_updated_ts"] = now()
                    save(s)
                    poke_event.set()
                    safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                    print(f"[outbox] updated choices {name} -> {sid}")
                    imported = True
                    continue
                elif target_user:
                    normalized_choices = normalize_choices(rep.get("choices"))
                    if normalized_choices:
                        target_user["staged_choices"] = normalized_choices
                    target_user.pop("choices_status", None)
                    target_user.pop("choices_type", None)
                    target_user.pop("choices_ts", None)
                    target_user.pop("choices_claimed_ts", None)
                    target_user.pop("choices_orig_id", None)
                    save(s)
                    poke_event.set()
                    safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
                    print(f"[outbox] staged choices for user {target_user['id']} {name} -> {sid}")
                    imported = True
                    continue

                if not target_asst and not target_user and rep.get("choices") and not rep.get("text") and not rep.get("image"):
                    print(f"[outbox] stale choices reply {name}")
                    safe_move(path, os.path.join(DONE, name + ".stale"))
                    continue

            # 宛先の無い本文は捨てる。(1) 未応答のユーザー発言が1つも無い (2) reply_to があるのに
            # その発言が未応答でない（既に答えた・巻き戻した）。
            # (1) が無いと、reply_to を書かなかった本文が宛先不明のまま返事だけ会話に
            # 紛れ込む（孤児の発言。世代交代の疎通テスト中に起きやすい）。
            pending_users = [m for m in s["messages"] if m.get("role") == "user" and m.get("status") in ("pending", "processing")]
            if not pending_users or (reply_to and not any(m["id"] == reply_to for m in pending_users)):
                print(f"[outbox] stale reply {name}")
                safe_move(path, os.path.join(DONE, name + ".stale"))
                continue
            target_u = None
            for m in s["messages"]:
                if m["role"] == "user" and m.get("status") in ("pending", "processing") and (not reply_to or m["id"] == reply_to):
                    m["status"] = "answered"
                    m.pop("ide_pending_for", None)
                    m.pop("direct", None)
                    m.pop("direct_ts", None)
                    m.pop("direct_off", None)
                    target_u = m
            st = s["setting"]
            drawing = st.get("image_mode") != "off"
            updates = []
            if isinstance(rep.get("characters"), dict):
                updates = [rep["characters"]]
            elif isinstance(rep.get("characters"), list):
                updates = rep["characters"]
            if updates and drawing:
                st["chars"] = merge_chars(st.get("chars") or [], updates, char_presets())
            msg = {"id": new_id(), "role": "assistant", "text": rep.get("text", ""), "ts": now()}
            clean_st = None
            merged_st = None
            turn_st = None
            if st.get("rp_state") and isinstance(rep.get("state"), (list, dict)):
                clean_st = clean_state(rep["state"])
                merged_st = merge_state(state_before(s["messages"], len(s["messages"]), sid=sid), clean_st)
                turn_st = [
                    {
                        "name": c.get("name", ""),
                        "pose": c.get("pose", ""),
                        "expression": list(c.get("expression") or [])
                    }
                    for c in clean_st if isinstance(c, dict) and ("pose" in c or "expression" in c)
                ]
            img = rep.get("image")
            if not isinstance(img, dict):
                img = {"prompt": rep["image_prompt"]} if isinstance(rep.get("image_prompt"), str) else {}
            if drawing:
                names = (img.get("characters") or img.get("chars") or img.get("character") or rep.get("image_characters"))
                if not names:
                    names = rep.get("characters")
                ctx_text = image_tag_context(s, target_u, rep.get("text", ""))
                if img.get("prompt"):
                    # 旧経路 / 手動編集 / 推敲モード: AIが書いたプロンプトを使う
                    cleaned_p = sanitize_prompt(
                        img["prompt"],
                        context_text=ctx_text,
                        state=merged_st,
                        state_turn=turn_st,
                        allow_inject=True,
                    )
                else:
                    # image.prompt 廃止: state からサーバー生成
                    cleaned_p = generate_prompt_from_state(merged_st, turn_st, ctx_text, names)
                if cleaned_p:
                    resolved_chars = resolve_image_chars(
                        st,
                        raw_names=names,
                        msg_text=rep.get("text", ""),
                        active_state=merged_st,
                        prompt_text=cleaned_p
                    )
                    msg["image"] = {
                        "prompt": cleaned_p, "negative": img.get("negative"),
                        "width": img.get("width"), "height": img.get("height"),
                        "raw": bool(img.get("raw", False)), "seed": img.get("seed"), "status": "queued",
                        "chars": list(dict.fromkeys(c["name"] for c in resolved_chars if c and c.get("name"))),
                    }
                    queued_images.append((sid, msg["id"]))
                    image_event.set()  # 0.01秒以内に画像生成ワーカを起床
            # 作中経過時間（elapsed_time）の受取
            if isinstance(rep.get("elapsed_time"), dict):
                new_day = int(rep["elapsed_time"].get("day", st.get("elapsed_time", {}).get("day", 1)))
                raw_period = rep["elapsed_time"].get("period", st.get("elapsed_time", {}).get("period", "午前"))
                new_period = normalize_period(raw_period)
                cur_el = st.get("elapsed_time") or {}
                if new_day != cur_el.get("day") or new_period != cur_el.get("period"):
                    st["elapsed_time"] = {"day": new_day, "period": new_period}
                    st["turns_in_period"] = 0
                else:
                    st["turns_in_period"] = st.get("turns_in_period", 0) + 1
            else:
                st["turns_in_period"] = st.get("turns_in_period", 0) + 1

            if isinstance(rep.get("memo"), str) and rep["memo"].strip():
                st["memo"] = rep["memo"].strip()
                st["last_memo_mid"] = msg["id"]

            if rep.get("memory_query"):
                msg["memory_query"] = str(rep["memory_query"]).strip()

            msg.pop("memory_status", None)
            msg.pop("memory_ts", None)
            msg.pop("memory_claimed_ts", None)

            # 返答候補（8択）の反映または並列生成タスクの引き継ぎ・起票
            normalized_choices = normalize_choices(rep.get("choices"))
            staged = target_u.pop("staged_choices", None) if target_u else None
            if normalized_choices:
                msg["choices"] = normalized_choices
                msg.pop("choices_status", None)
                msg.pop("choices_type", None)
                msg.pop("choices_ts", None)
                msg.pop("choices_claimed_ts", None)
                msg.pop("choices_orig_id", None)
            elif staged:
                # ケースA: 選択肢セッションが先に完了していた場合、即座に結合
                msg["choices"] = staged
                msg.pop("choices_status", None)
                msg.pop("choices_type", None)
                msg.pop("choices_ts", None)
                msg.pop("choices_claimed_ts", None)
                msg.pop("choices_orig_id", None)
            elif target_u and target_u.get("choices_status") in ("pending", "processing"):
                # ケースB: 選択肢セッションが現在並列生成中の場合、ステータスを引き継ぐ
                msg["choices_status"] = target_u.pop("choices_status")
                msg["choices_type"] = target_u.pop("choices_type", "normal")
                msg["choices_ts"] = target_u.pop("choices_ts", now())
                if target_u.get("choices_claimed_ts"):
                    msg["choices_claimed_ts"] = target_u.pop("choices_claimed_ts")
                msg["choices_orig_id"] = target_u["id"]

            if target_u:
                target_u.pop("choices_status", None)
                target_u.pop("choices_type", None)
                target_u.pop("choices_ts", None)
                target_u.pop("choices_claimed_ts", None)
                target_u.pop("choices_orig_id", None)
                target_u.pop("staged_choices", None)

            appended = False
            if msg["text"] or msg.get("image") or msg.get("choices") or msg.get("choices_status"):
                if merged_st is not None:
                    msg["state"] = merged_st
                if turn_st is not None:
                    msg["state_turn"] = turn_st
                s["messages"].append(msg)
                appended = True
            save(s)

            # チャット本文を save(s) で即座に確定させた後、長期記憶の抽出・保存を専用ワーカーへキューイングする
            if appended:
                turn_idx = st.get("total_turns") or ((st.get("rotation_count", 0) * ROTATION_PERIOD) + len([x for x in s["messages"] if x.get("role") == "user"]))
                ch_name = (st.get("character") or {}).get("name", "")
                raw_el = msg.get("elapsed_time") or st.get("elapsed_time") or {"day": 1, "period": "午前"}
                el_snapshot = copy.deepcopy(raw_el) if isinstance(raw_el, dict) else {"day": 1, "period": "午前"}
                if rep.get("memories") or rep.get("resolved_ids"):
                    queued_memories.append(("explicit", sid, ch_name, rep.get("memories", []) or [], rep.get("resolved_ids", []) or [], turn_idx, msg["id"], el_snapshot))
                else:
                    # フォールバック抽出に必要な最小限の軽量スナップショットのみを渡す
                    s_light = {
                        "setting": {
                            "character": copy.deepcopy(st.get("character") or {}),
                            "user_name": st.get("user_name", ""),
                            "chars": copy.deepcopy(st.get("chars") or []),
                            "total_turns": turn_idx,
                            "rotation_count": st.get("rotation_count", 0),
                            "elapsed_time": el_snapshot,
                        },
                        "messages": [{"id": x.get("id"), "role": x.get("role"), "text": x.get("text", ""), "elapsed_time": copy.deepcopy(x.get("elapsed_time"))} for x in s["messages"]],
                    }
                    msg_light = {"id": msg["id"], "role": "assistant", "text": msg.get("text", ""), "elapsed_time": el_snapshot}
                    queued_memories.append(("fallback", sid, s_light, msg_light, msg["id"]))
                memory_event.set()
            note_reply(s, reply_to, msg)
            ready_sid = sid          # ロックを出てから回転を進める
        # ---- ロック外 ----
        if ready_sid:
            rotation_note_body(ready_sid)   # 数ミリ秒・RPC なし（I1）
        poke_event.set()
        safe_move(path, os.path.join(DONE, f"{int(time.time())}_{name}"))
        print(f"[outbox] imported {name} -> {sid}")
        imported = True
    return imported


# ---------------------------------------------------------------- comfy
def _count_tag_from_names(names, state=None):
    """画像に写る人数タグを推定する（image.prompt 廃止時の人数補完）"""
    n = 0
    if isinstance(names, str):
        n = len([x for x in names.split(",") if x.strip()])
    elif isinstance(names, (list, tuple, set)):
        n = len([x for x in names if isinstance(x, str) and x.strip()])
    if n == 0:
        if isinstance(state, list):
            n = len([c for c in state if isinstance(c, dict) and c.get("name")])
        elif isinstance(state, dict):
            n = 1
    if n >= 2:
        return f"{n}girls"
    return "1girl"


def generate_prompt_from_state(state, state_turn=None, context_text="", names=None):
    """image.prompt 廃止用: state + 本文 + 辞書から画像プロンプトのベースを生成する。
    AI が書いたタグは使わず、pose / expression / place / camera / equipped_items /
    penetration / outfit / body_fluids / situation をサーバー側でタグ化する。
    """
    count = _count_tag_from_names(names, state)
    return sanitize_prompt(
        count,
        context_text=context_text,
        state=state,
        state_turn=state_turn,
        allow_inject=True,
    )


def build_prompt(setting, img, msg=None):
    ic = setting.get("image", {})
    if img.get("raw"):
        pos = img["prompt"]
    elif img.get("manual"):
        ctx_text = (msg.get("text") if msg else None) or img.get("context_text")
        state = (msg.get("state") if msg else None) or img.get("state")
        raw_scene = img.get("prompt", "")

        scene_tags = split_tags(raw_scene)
        norm_scene_tags = [normalize_tag(t) for t in scene_tags]
        is_no_char = any(t in ("no characters", "scenery", "nobody") for t in norm_scene_tags) or bool(
            re.search(r"\b(no characters|no_characters|scenery|nobody)\b", raw_scene, re.IGNORECASE)
        )

        reg_chars = [c for c in (setting.get("chars") or []) if isinstance(c, dict) and c.get("name")]
        if is_no_char:
            target_chars = []
        else:
            detected_chars = []
            for c in reg_chars:
                terms = get_char_match_terms(c)
                matched = False
                for term in terms:
                    term_norm = normalize_tag(term)
                    if term_norm and term_norm in norm_scene_tags:
                        matched = True
                        break
                    if term.isascii() and len(term) >= 3:
                        if re.search(r"\b" + re.escape(term) + r"\b", raw_scene, re.IGNORECASE):
                            matched = True
                            break
                    elif not term.isascii() and len(term) >= 2:
                        if term in raw_scene:
                            matched = True
                            break
                if matched:
                    detected_chars.append(c)

            if detected_chars:
                target_chars = detected_chars
            else:
                target_chars = resolve_image_chars(
                    setting,
                    raw_names=img.get("chars"),
                    msg_text=ctx_text or "",
                    active_state=state,
                    prompt_text=raw_scene
                )

        if target_chars:
            img["chars"] = list(dict.fromkeys(c["name"] for c in target_chars if c.get("name")))
            char_tags = []
            for c in target_chars:
                char_tags += split_tags(c.get("tags"))
            if not char_tags and ic.get("character_tags"):
                char_tags = split_tags(ic["character_tags"])
            if any(COUNT_TAG.fullmatch(t) for t in norm_scene_tags):
                char_tags = [t for t in char_tags if not COUNT_TAG.fullmatch(normalize_tag(t))]
            if any(t in CORE_ANATOMY_TAGS for t in norm_scene_tags):
                char_tags = [t for t in char_tags if normalize_tag(t) not in ("solo", "solo focus")]
        else:
            char_tags = []

        raw_norm_set = {normalize_tag(t) for t in scene_tags if normalize_tag(t)}
        base_pos_tags = [t for t in split_tags(ic.get("base_positive")) if normalize_tag(t) not in raw_norm_set and not is_censor_related_tag(t)]
        char_prefix_tags = [t for t in char_tags if normalize_tag(t) not in raw_norm_set and not is_censor_related_tag(t)]
        prefix_tags = list(dict.fromkeys(base_pos_tags + char_prefix_tags))

        clean_raw_scene = re.sub(r"^[\s,]+|[\s,]+$", "", raw_scene)
        if prefix_tags and clean_raw_scene:
            pos = f"{', '.join(prefix_tags)}, {clean_raw_scene}"
        elif prefix_tags:
            pos = ", ".join(prefix_tags)
        else:
            pos = clean_raw_scene
    else:
        ctx_text = (msg.get("text") if msg else None) or img.get("context_text")
        state = (msg.get("state") if msg else None) or img.get("state")
        state_turn = (msg.get("state_turn") if msg else None) or img.get("state_turn")
        raw_scene = img.get("prompt", "")
        if not raw_scene or not str(raw_scene).strip():
            raw_scene = generate_prompt_from_state(state, state_turn, ctx_text, img.get("chars"))
        # 自動セーフティネット補完＆サニタイズ（禁止逃避タグの排除およびコア行為タグの補完）
        cleaned_scene = sanitize_prompt(raw_scene, context_text=ctx_text, state=state, allow_inject=True, state_turn=state_turn)
        if cleaned_scene:
            img["prompt"] = cleaned_scene
            raw_scene = cleaned_scene

        # 品質タグ → 人数タグ → 写っている固定キャラの外見 → 場面。同じタグは最初の1つだけ残す
        # 人数タグは場面（AI）のものを優先する
        rest = []
        # 外見タグは chars（固定キャラ）からのみ取得（空なら resolve_image_chars で補完・再生成）
        target_chars = resolve_image_chars(
            setting,
            raw_names=img.get("chars"),
            msg_text=ctx_text or "",
            active_state=state,
            prompt_text=raw_scene
        )
        if target_chars:
            img["chars"] = list(dict.fromkeys(c["name"] for c in target_chars if c.get("name")))
        for c in target_chars:
            rest += split_tags(c.get("tags"))
        # （※互換性保護：もし chars が空で古い character_tags が残っている場合のみフォールバック）
        if not rest and ic.get("character_tags"):
            rest = split_tags(ic["character_tags"])
        scene = split_tags(raw_scene)
        if any(COUNT_TAG.fullmatch(normalize_tag(t)) for t in scene):
            rest = [t for t in rest if not COUNT_TAG.fullmatch(normalize_tag(t))]
        rest += scene

        # 性器・行為タグが含まれる場合、キャラ固定タグ由来の solo / solo focus を排除
        has_intimacy = any(normalize_tag(t) in CORE_ANATOMY_TAGS for t in scene)
        if has_intimacy:
            rest = [t for t in rest if normalize_tag(t) not in ("solo", "solo focus")]

        # タグの順序整理：品質タグ → 人数タグ → pov → 構図 → コア行為・解剖タグ → 外見・ポーズ・表情・背景
        counts = [t for t in rest if COUNT_TAG.fullmatch(normalize_tag(t))]
        pov_tags = [t for t in rest if normalize_tag(t) == "pov" and not COUNT_TAG.fullmatch(normalize_tag(t))]
        camera_tags = [
            t for t in rest
            if normalize_tag(t) in CAMERA_SORT_TAGS
            and not COUNT_TAG.fullmatch(normalize_tag(t))
            and normalize_tag(t) != "pov"
        ]
        core_tags = [
            t for t in rest
            if normalize_tag(t) in CORE_ANATOMY_TAGS
            and not COUNT_TAG.fullmatch(normalize_tag(t))
            and normalize_tag(t) != "pov"
            and normalize_tag(t) not in CAMERA_SORT_TAGS
        ]
        other_tags = [
            t for t in rest
            if not COUNT_TAG.fullmatch(normalize_tag(t))
            and normalize_tag(t) != "pov"
            and normalize_tag(t) not in CAMERA_SORT_TAGS
            and normalize_tag(t) not in CORE_ANATOMY_TAGS
            and not is_prohibited_escape_tag(t)
        ]

        tags = [t for t in split_tags(ic.get("base_positive")) if not is_censor_related_tag(t)] + counts + pov_tags + camera_tags + core_tags + other_tags
        pos = ", ".join(dict.fromkeys(tags))
    raw_neg = img.get("negative") or ic.get("base_negative", "")
    neg_tags = [t for t in split_tags(raw_neg) if not is_censor_related_tag(t)]
    neg = ", ".join(dict.fromkeys(neg_tags))
    return pos, neg


def build_workflow(setting, img, prefix, msg=None):
    ic = setting.get("image", {})
    pos, neg = build_prompt(setting, img, msg=msg)

    # --- 出力の参照先を追跡する変数 ---
    model_src = ["4", 0]   # MODEL の出元（チェックポイント）
    clip_src  = ["4", 1]   # CLIP の出元（チェックポイント）

    ckpt_name = (ic.get("checkpoint") or "waiIllustriousSDXL_v170.safetensors").strip()
    if not ckpt_name:
        ckpt_name = "waiIllustriousSDXL_v170.safetensors"

    w = int(img.get("width") or ic.get("width") or 768)
    h = int(img.get("height") or ic.get("height") or 1344)

    try:
        seed_val = int(img.get("seed", 0) if img.get("seed") is not None else 0)
    except (ValueError, TypeError):
        seed_val = 0

    wf = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
    }

    # --- 高速化 LoRA（Hyper-SD 等）---
    raw_speed_lora = ic.get("speed_lora")
    speed_lora = str(raw_speed_lora).strip() if raw_speed_lora is not None else ""
    if speed_lora.lower() in ("", "none", "null", "false", "off", "disable", "disabled"):
        speed_lora = ""

    if speed_lora:
        try:
            lora_strength = float(ic.get("speed_lora_strength", 0.8) if ic.get("speed_lora_strength") is not None else 0.8)
        except (ValueError, TypeError):
            lora_strength = 0.8
        if lora_strength > 0.0:
            wf["20"] = {"class_type": "LoraLoader", "inputs": {
                "lora_name": speed_lora,
                "strength_model": lora_strength,
                "strength_clip": lora_strength,
                "model": model_src, "clip": clip_src}}
            model_src = ["20", 0]
            clip_src  = ["20", 1]

    # --- Clip Skip（アニメ系推奨: 2）---
    try:
        clip_skip = abs(int(ic.get("clip_skip", 0) if ic.get("clip_skip") is not None else 0))
    except (ValueError, TypeError):
        clip_skip = 0
    if 2 <= clip_skip <= 24:
        wf["21"] = {"class_type": "CLIPSetLastLayer", "inputs": {
            "stop_at_clip_layer": -clip_skip, "clip": clip_src}}
        clip_src = ["21", 0]

    # --- プロンプトエンコード・サンプラー ---
    try:
        steps_val = int(ic.get("steps", 8) if ic.get("steps") is not None else 8)
    except (ValueError, TypeError):
        steps_val = 8
    steps_val = max(1, steps_val)

    try:
        cfg_val = float(ic.get("cfg", 4.0) if ic.get("cfg") is not None else 4.0)
    except (ValueError, TypeError):
        cfg_val = 4.0
    cfg_val = max(0.0, cfg_val)

    sampler_name = (ic.get("sampler") or "euler").strip() or "euler"
    scheduler_name = (ic.get("scheduler") or "normal").strip() or "normal"

    wf["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": pos, "clip": clip_src}}
    wf["7"] = {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": clip_src}}
    wf["3"] = {"class_type": "KSampler", "inputs": {
        "seed": seed_val, "steps": steps_val, "cfg": cfg_val,
        "sampler_name": sampler_name, "scheduler": scheduler_name,
        "denoise": 1.0, "model": model_src, "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}}
    wf["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}}
    wf["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}}

    # --- IP-Adapter（設定されていれば）---
    if ic.get("ipadapter_image"):
        try:
            ipa_w = float(ic.get("ipadapter_weight", 0.85) if ic.get("ipadapter_weight") is not None else 0.85)
        except (ValueError, TypeError):
            ipa_w = 0.85
        wf["10"] = {"class_type": "LoadImage", "inputs": {"image": ic["ipadapter_image"]}}
        wf["12"] = {"class_type": "IPAdapterUnifiedLoader", "inputs": {"model": model_src, "preset": "PLUS FACE (portraits)"}}
        wf["13"] = {"class_type": "IPAdapter", "inputs": {
            "model": ["12", 0], "ipadapter": ["12", 1], "image": ["10", 0],
            "weight": ipa_w, "start_at": 0.0, "end_at": 1.0, "weight_type": "standard"}}
        wf["3"]["inputs"]["model"] = ["13", 0]
    return wf, pos, neg


def comfy_run(workflow, timeout=900):
    payload = json.dumps({"prompt": workflow, "client_id": str(uuid.uuid4())}).encode("utf-8")
    req = urllib.request.Request(f"{COMFY}/prompt", data=payload, headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=15).read())["prompt_id"]
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(0.1)  # 0.3秒から0.1秒へ短縮し、画像完了検知を高速化
        try:
            h = json.loads(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10).read())
        except Exception:
            continue
        if pid not in h:
            continue
        entry = h[pid]
        st = entry.get("status", {})
        if st.get("status_str") == "error":
            for kind, info in st.get("messages", []):
                if kind == "execution_error":
                    raise RuntimeError(info.get("exception_message", "ComfyUI error").strip())
            raise RuntimeError("ComfyUI error")
        for out in entry.get("outputs", {}).values():
            for im in out.get("images", []):
                sub = im.get("subfolder", "")
                return (sub + "/" if sub else "") + im["filename"]
        if st.get("completed"):
            raise RuntimeError("no image output")
    raise RuntimeError("timeout")


def process_images():
    with lock:
        target = None
        while queued_images:
            q_sid, q_mid = queued_images.popleft()
            if os.path.exists(spath(q_sid)):
                s = load(q_sid)
                m = find_msg(s, q_mid)
                if m and (m.get("image") or {}).get("status") == "queued":
                    target = (s, m)
                    break
        if not target:
            for sid in sorted(list_ids()):
                s = load(sid, readonly=True)
                for m in reversed(s["messages"]):
                    im = m.get("image")
                    if im and im.get("status") == "queued":
                        s = load(sid)
                        target = (s, find_msg(s, m["id"]))
                        break
                if target:
                    break
        if not target:
            return
        s, m = target
        im = m["image"]
        img_meta = load_images_data(s["id"], readonly=True).get(m["id"])
        if img_meta:
            for k, v in img_meta.items():
                if k not in im or im[k] is None:
                    im[k] = copy.deepcopy(v)
        if not m.get("state") and s.get("id") and m.get("id"):
            m_state = load_states_data(s["id"], readonly=True).get(m["id"])
            if m_state:
                m["state"] = copy.deepcopy(m_state)
        if not im.get("seed"):
            im["seed"] = random.randint(0, 2**32 - 1)
        im["status"] = "generating"
        im.pop("error", None)
        try:
            wf, pos, neg = build_workflow(s["setting"], im, f"rpchat/{s['id']}/rp_{m['id']}", msg=m)
            im["final_positive"] = pos
            im["final_negative"] = neg
            sid, mid, seed = s["id"], m["id"], im["seed"]
            save(s)
            build_err = None
        except Exception as e:
            build_err = str(e)
            im["status"] = "error"
            im["error"] = f"ワークフロー構築エラー: {build_err}"
            sid, mid = s["id"], m["id"]
            save(s)

    if build_err:
        print(f"[comfy] workflow error {sid}/{mid}: {build_err}")
        ch_title = s["setting"].get("title") or s["id"]
        push_payload = {
            "title": "⚠️ 画像生成エラー",
            "body": f"【{ch_title}】ワークフロー構築エラー: {build_err}",
            "icon": "/icon.png",
            "tag": f"img-err-{mid}",
            "data": {
                "sessionId": sid,
                "messageId": mid,
                "url": f"/?sid={quote(sid)}&mid={quote(mid)}"
            }
        }
        threading.Thread(target=send_push_notification, args=(push_payload,), daemon=True).start()
        return

    print(f"[comfy] generating {sid}/{mid} seed={seed}")
    try:
        rel, err = comfy_run(wf), None
    except Exception as e:
        rel, err = None, str(e)
        print(f"[comfy] error {sid}/{mid}: {err}")

    with lock:
        if not os.path.exists(spath(sid)):
            return
        s = load(sid)
        m = find_msg(s, mid)
        if m and m.get("image"):
            if rel:
                m["image"]["file"] = rel
                m["image"]["status"] = "done"
                m["image"]["raw"] = None
                m["image"]["history"] = None
            else:
                m["image"]["status"] = "error"
                m["image"]["error"] = err
            save(s)

            ch_title = s["setting"].get("title") or s["id"]
            if rel:
                rel_clean = rel.replace("\\", "/")
                p_text = (m["image"].get("prompt") or "").strip()
                if len(p_text) > 60:
                    p_text = p_text[:60] + "…"
                push_payload = {
                    "title": "🎨 画像が完成しました",
                    "body": f"【{ch_title}】{p_text}" if p_text else f"【{ch_title}】画像が完成しました",
                    "icon": "/icon.png",
                    "image": f"/img?f={quote(rel_clean)}",
                    "tag": f"img-{mid}",
                    "data": {
                        "sessionId": sid,
                        "messageId": mid,
                        "url": f"/?sid={quote(sid)}&mid={quote(mid)}"
                    }
                }
            else:
                push_payload = {
                    "title": "⚠️ 画像生成エラー",
                    "body": f"【{ch_title}】{err or 'ComfyUI での生成に失敗しました'}",
                    "icon": "/icon.png",
                    "tag": f"img-err-{mid}",
                    "data": {
                        "sessionId": sid,
                        "messageId": mid,
                        "url": f"/?sid={quote(sid)}&mid={quote(mid)}"
                    }
                }
            threading.Thread(target=send_push_notification, args=(push_payload,), daemon=True).start()


def outbox_worker():
    while True:
        try:
            had_files = process_outbox()
        except Exception as e:
            print(f"[outbox worker] {e}")
            had_files = False
        if not had_files:
            time.sleep(0.05)  # 0.2秒から0.05秒へ短縮し取り込みを高速化


def _memory_task_alive(sid, mid):
    """対象メッセージが削除・再生成・編集で消えていないか確認する（msg_to_session キャッシュは使わず実在確認のみ）"""
    if not sid or not mid:
        return False
    sid = str(sid).strip()
    mid = str(mid).strip()
    if not sid or not mid:
        return False
    with lock:
        try:
            return find_msg(load(sid, readonly=True), mid) is not None
        except (FileNotFoundError, ValueError):
            return False
        except Exception:
            return False


_memory_worker_stop = False


def memory_worker():
    """チャット応答の表示を妨げずにバックグラウンドで長期記憶の形態素解析・ベクトル化・保存を行う"""
    while not _memory_worker_stop:
        while True:
            try:
                task = queued_memories.popleft()
            except IndexError:
                break
            try:
                if not isinstance(task, (list, tuple)) or len(task) < 2:
                    continue
                kind = task[0]
                t_sid = task[1]
                if kind == "explicit" and len(task) >= 8:
                    t_mid = task[6]
                elif kind == "fallback" and len(task) >= 5:
                    t_mid = task[4]
                else:
                    continue

                # 1. 保存前の存在確認
                if not _memory_task_alive(t_sid, t_mid):
                    continue
                # 2. ロック外での推論＆保存実行
                if kind == "explicit":
                    _, m_sid, m_ch, m_list, m_res, m_turn, m_id, m_el = task[:8]
                    memory_store.save_extracted_memories(
                        m_sid, m_ch, m_list, resolved_ids=m_res, turn_idx=m_turn, mid=m_id, elapsed_time=m_el
                    )
                elif kind == "fallback":
                    _, m_sid, m_s_snap, m_msg_snap, _ = task[:5]
                    extract_memories_fallback(m_sid, m_s_snap, m_msg_snap)
                # 3. 保存中にメッセージが削除・巻き戻しされた場合の補償削除（ABBAデッドロックを起こさずに競合窓を完全に塞ぐ）
                if not _memory_task_alive(t_sid, t_mid):
                    memory_store.delete_by_mid(t_sid, t_mid)
            except Exception as e:
                print(f"[memory worker] {e}")
        memory_event.wait(timeout=1.0)
        memory_event.clear()


def image_worker():
    # 画像の生成待ちで応答の取り込みが止まらないよう、別のスレッドで回す
    while True:
        try:
            process_images()
        except Exception as e:
            print(f"[image worker] {e}")
        image_event.wait(timeout=0.5)
        image_event.clear()


def summary_of(s):
    sid = s.get("id")
    rev = s.get("rev")
    if sid and rev is not None:
        cached = summary_cache.get(sid)
        if cached is not None and cached[0] == rev:
            return copy.copy(cached[1])
    st = s["setting"]
    msgs = s["messages"]
    sm = {
        "id": s["id"], "title": st.get("title"), "summary": st.get("summary", ""), "project": st.get("project", ""),
        "rev": s.get("rev"), "updated": s.get("updated"), "created": s.get("created"),
        "count": len(msgs),
        "waiting": any((m["role"] == "user" and m.get("status") in ("pending", "processing")) or (m.get("image_refine_status") in ("pending", "processing")) or (m.get("choices_status") in ("pending", "processing")) or (m["role"] == "assistant" and (m.get("memory_status") in ("pending", "processing") or m.get("consolidation_status") in ("pending", "processing"))) for m in msgs),
        "generating": any((m.get("image") or {}).get("status") in ("queued", "generating") for m in msgs),
        "last": (msgs[-1]["text"][:60] if msgs else ""),
        "ide_title": st.get("ide_title", "") if st.get("ide_session") else "",
        "choices_ide_title": st.get("choices_ide_title", "") if st.get("choices_ide_session") else "",
    }
    if sid and rev is not None:
        summary_cache[sid] = (rev, sm)
    return sm


def clean_import(obj):
    """書き出した JSON（または messages だけの配列）を取り込める形に整える。
    agy 版で書き出したものも読める（agy の会話の記録は捨てる）"""
    if isinstance(obj, list):
        obj = {"messages": obj}
    if (not isinstance(obj, dict) or not ("messages" in obj or "setting" in obj)
            or not isinstance(obj.get("messages", []), list) or not isinstance(obj.get("setting") or {}, dict)):
        raise ValueError("チャンネルの JSON ではありません")
    msgs = []
    for m in obj.get("messages", []):
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("text", ""), str):
            raise ValueError("messages の中身が不正です")
        m = dict(m, id=new_id())
        m.pop("agy", None)
        if m["role"] == "user":
            m["status"] = "answered"
            m.pop("error", None)
        if m.get("image") and m["image"].get("status") in ("queued", "generating"):
            m["image"]["status"] = "error"
            m["image"]["error"] = "取り込み時に未完成"
        if "choices" in m:
            norm = normalize_choices(m["choices"])
            if norm:
                m["choices"] = norm
            else:
                m.pop("choices", None)
        msgs.append(m)
    st = {k: v for k, v in (obj.get("setting") or {}).items() if not k.startswith("agy_") and k != "model" and k not in DEPRECATED_SETTING_KEYS}
    return dict(st, **NO_IDE, **NO_FOLDER), msgs


# ---------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        # セッション等の共有辞書を別スレッドが書き換えている最中の dumps を避ける（RLock なので再入可）
        with lock:
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self.send_error(404)
        ext = os.path.splitext(path)[1].lower()
        mime_map = {
            ".js": "text/javascript",
            ".css": "text/css",
            ".html": "text/html",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
            ".ico": "image/x-icon"
        }
        ctype = ctype or mime_map.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            b = f.read()
        if os.path.basename(path) == "index.html":
            b = b.replace(b"{{PORT}}", str(PORT).encode())  # タブ名の語尾にポート番号
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        if os.path.basename(path) == "sw.js":
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Cache-Control", "no-cache")
        elif ext in (".html", ".js", ".css"):
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if n < 0 or n > BODY_MAX:
            raise ValueError("request body too large")
        d = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        if not isinstance(d, dict):
            raise ValueError("request body must be a JSON object")
        return d

    # ------------------------------------------------ access
    def send_plain(self, code, text):
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def blocked(self, u, post=False):
        """入れてよいリクエストでなければ、ここで断って True を返す"""
        h_name = host_name(self.headers.get("Host"))
        is_allowed = h_name in access["hosts"]
        if not is_allowed and HOST == "lan":
            if h_name.endswith(".local") or is_private_ip(h_name):
                is_allowed = True
                access["hosts"].add(h_name)
        if not is_allowed:
            # 知らない名前で届いた（DNS リバインディング）。PC の名前で開くなら RPCHAT_ALLOWED_HOSTS に足す
            self.send_plain(403, "unknown Host. Add it to RPCHAT_ALLOWED_HOSTS to allow it.")
            return True
        api = u.path.startswith("/api/") or u.path in ("/img", "/upload")
        if api or post:
            # ほかのサイトのページからの読み書きは断る（このアプリの画面・直接開いたとき・wait_input.py だけ通す）
            site = self.headers.get("Sec-Fetch-Site")
            origin = self.headers.get("Origin")
            if (site and site not in ("same-origin", "none")) or \
                    (origin and urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower()):
                self.send_plain(403, "cross-site request blocked")
                return True
        cl = 0
        try:
            cl = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            pass
        if post and cl > 0 and not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            self.send_plain(415, "Content-Type must be application/json")
            return True
        return False

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if self.blocked(u):
            return
        try:
            if p == "/api/sessions":
                with lock:
                    items = [summary_of(load(sid, readonly=True)) for sid in list_ids()]
                items.sort(key=lambda x: x.get("updated") or "", reverse=True)
                return self.send_json({"sessions": items})
            if p == "/api/session":
                sid = q.get("sid", [""])[0]
                client_rev = q.get("rev", [""])[0]
                cached_rev = session_revs.get(sid)
                cached_entry = session_cache.get(sid)
                if (cached_rev is not None
                        and client_rev == f"{sid}:{cached_rev}"
                        and not (cached_entry and _has_expired_subtasks(cached_entry.get("s")))):
                    return self.send_json({"unchanged": True, "id": sid, "rev": cached_rev})
                try:
                    s = load(sid, readonly=True)
                except (FileNotFoundError, ValueError):
                    session_revs.pop(sid, None)
                    return self.send_json({"error": "no such session"}, 404)
                session_revs[sid] = s.get("rev", 0)
                if client_rev and f"{s['id']}:{s.get('rev')}" == client_rev:
                    return self.send_json({"unchanged": True, "id": s["id"], "rev": s.get("rev")})
                return self.send_json(s)
            if p == "/api/session/export":
                s = load(q.get("sid", [""])[0])
                export_s = clone_session(s)
                states_data = load_states_data(s["id"], readonly=True)
                images_data = load_images_data(s["id"], readonly=True)
                for m in export_s.get("messages", []):
                    mid = m.get("id")
                    if mid:
                        if mid in states_data:
                            m["state"] = copy.deepcopy(states_data[mid])
                        if mid in images_data:
                            im = m.setdefault("image", {})
                            for k, v in images_data[mid].items():
                                im[k] = copy.deepcopy(v)
                b = json.dumps(export_s, ensure_ascii=False, indent=2).encode("utf-8")
                name = f"{s['setting'].get('title') or s['id']}_{time.strftime('%Y%m%d_%H%M')}.json"
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name))
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if p == "/api/message/state":
                sid = q.get("sid", [""])[0]
                mid = q.get("mid", [""])[0]
                fallback = q.get("fallback", ["0"])[0] in ("1", "true")
                if not sid or not mid:
                    return self.send_json({"error": "sid and mid are required"}, 400)
                st_data = load_states_data(sid, readonly=True)
                res_st = st_data.get(mid)
                if res_st is None:
                    try:
                        s = load(sid, readonly=True)
                        m = find_msg(s, mid)
                        if m and isinstance(m.get("state"), list):
                            res_st = m["state"]
                        elif fallback and s.get("messages"):
                            idx = next((i for i, x in enumerate(s["messages"]) if x.get("id") == mid), len(s["messages"]))
                            res_st = state_before(s["messages"], idx, sid=sid)
                    except Exception:
                        pass
                return self.send_json({"ok": True, "state": res_st if res_st is not None else []})
            if p == "/api/message/image_meta":
                sid = q.get("sid", [""])[0]
                mid = q.get("mid", [""])[0]
                if not sid or not mid:
                    return self.send_json({"error": "sid and mid are required"}, 400)
                im_data = load_images_data(sid, readonly=True)
                res_im = im_data.get(mid)
                if res_im is None:
                    try:
                        s = load(sid, readonly=True)
                        m = find_msg(s, mid)
                        if m and isinstance(m.get("image"), dict):
                            res_im = {k: v for k, v in m["image"].items() if k not in ("file", "status", "error")}
                    except Exception:
                        pass
                return self.send_json({"ok": True, "image": res_im if res_im is not None else {}})
            if p == "/api/pending":
                # ?ide=<会話ID> なら、その会話が担当するチャンネルだけ。無指定なら担当なしのチャンネルだけ
                ide = q.get("ide", [""])[0]
                check = q.get("check", [""])[0] == "1"
                if ide:
                    ide_waiting[ide] = time.time()
                    mark_known(ide)
                best, left = None, None
                all_ids, mine_ids = ([], []) if check else (None, None)
                any_is_main, any_is_choices = False, False
                any_parallel = False
                type_prio = {"user": 1, "image_refine": 2, "choices": 3, "memory_extract": 4, "consolidation": 5}

                # 「合図を送った会話(ide_pending_for)以外には渡さない」制限は廃止した。
                # 下の sid 絞り込みで、ここへ来るのは常に「いまの担当の会話」だけ。つまり刻印が
                # 担当と食い違うのは『合図を送ったあとに担当を切り替えた』ときしかありえず、
                # その状態で刻印を守ると、その発言は担当を外れた会話にも現在の会話にも
                # 渡らないまま永久に未応答になる（実測: 担当を変えた発言が45秒経っても
                # /api/pending に出ず、/api/claim も「別の会話が処理中」で拒否され続けた）。
                # 二重応答は process_outbox の「宛先の無い本文は捨てる」判定で防いでいる。

                with lock:
                    all_sids = list_ids()
                    if check:
                        all_ids.extend(all_sids)
                    for sid in all_sids:
                        cached_ide = session_ide_cache.get(sid)
                        if cached_ide is not None and ide and ide not in cached_ide:
                            continue
                        s = load(sid, readonly=True)
                        st = s["setting"]
                        par = has_parallel_choices_ide(st)
                        if ide:
                            is_main = st.get("ide_session", "") == ide
                            is_choices = par and (st.get("choices_ide_session", "") == ide)
                            if not is_main and not is_choices:
                                continue
                        else:
                            is_main = not st.get("ide_session")
                            is_choices = False
                            if not is_main:
                                continue
                        if is_main:
                            any_is_main = True
                            if par:
                                any_parallel = True
                        if is_choices:
                            any_is_choices = True
                        if check:
                            mine_ids.append(sid)

                        cand_cand = None
                        if is_choices:
                            if check and not left:
                                for m in reversed(s["messages"]):
                                    if m.get("choices_status") == "processing":
                                        left = [sid, m, "choices"]
                                        break
                            pending_choices = next((m for m in s["messages"] if m.get("choices_status") == "pending"), None)
                            if pending_choices:
                                cand_cand = (sid, pending_choices, "choices")
                        else:
                            if check and not left:
                                for m in reversed(s["messages"]):
                                    if m["role"] == "user" and m.get("status") == "processing" and not m.get("direct"):
                                        left = [sid, m, "user"]
                                        break
                                    elif m.get("image_refine_status") == "processing":
                                        left = [sid, m, "image_refine"]
                                        break
                                    elif not par and m["role"] == "assistant" and m.get("choices_status") == "processing":
                                        left = [sid, m, "choices"]
                                        break
                                    elif m["role"] == "assistant" and m.get("memory_status") == "processing":
                                        left = [sid, m, "memory_extract"]
                                        break
                                    elif m["role"] == "assistant" and m.get("consolidation_status") == "processing":
                                        left = [sid, m, "consolidation"]
                                        break

                            pending_user = next((m for m in s["messages"] if m.get("role") == "user" and m.get("status") == "pending"), None)
                            pending_img_refine = None
                            pending_choices = None
                            pending_mem = None
                            pending_cons = None
                            if not pending_user:
                                pending_img_refine = next((m for m in s["messages"] if m.get("image_refine_status") == "pending"), None)
                            if not pending_user and not pending_img_refine and not par:
                                pending_choices = next((m for m in s["messages"] if m.get("role") == "assistant" and m.get("choices_status") == "pending"), None)
                            if not pending_user and not pending_img_refine and not pending_choices:
                                pending_mem = next((m for m in s["messages"] if m.get("role") == "assistant" and m.get("memory_status") == "pending"), None)
                            if not pending_user and not pending_img_refine and not pending_choices and not pending_mem:
                                pending_cons = next((m for m in s["messages"] if m.get("role") == "assistant" and m.get("consolidation_status") == "pending"), None)

                            if pending_user:
                                cand_cand = (sid, pending_user, "user")
                            elif pending_img_refine:
                                cand_cand = (sid, pending_img_refine, "image_refine")
                            elif pending_choices:
                                cand_cand = (sid, pending_choices, "choices")
                            elif pending_mem:
                                cand_cand = (sid, pending_mem, "memory_extract")
                            elif pending_cons:
                                cand_cand = (sid, pending_cons, "consolidation")

                        if cand_cand:
                            if not best:
                                best = cand_cand
                            else:
                                cur_prio = type_prio.get(best[2], 99)
                                new_prio = type_prio.get(cand_cand[2], 99)
                                if new_prio < cur_prio:
                                    best = cand_cand
                                elif new_prio == cur_prio:
                                    cur_ts = task_ts(best[1], best[2])
                                    new_ts = task_ts(cand_cand[1], cand_cand[2])
                                    if new_ts < cur_ts:
                                        best = cand_cand

                ide_role = "choices" if (any_is_choices and not any_is_main) else ("main" if (any_is_main and not any_is_choices and any_parallel) else "both")
                res = {
                    "session": best[0] if best else None,
                    "pending": best[1] if best else None,
                    "task_type": best[2] if best else None,
                    "ide_role": ide_role
                }
                if check:
                    res.update(
                        all_ids=all_ids,
                        mine_ids=mine_ids,
                        left=left[:2] if left else None,
                        left_type=left[2] if left else None
                    )
                return self.send_json(res)
            if p == "/api/ide/sessions":
                try:
                    return self.send_json({"sessions": ide_sessions(), "new_dir": NEW_IDE_DIR})
                except (OSError, sqlite3.Error) as e:
                    return self.send_json({"error": str(e)}, 500)
            if p == "/api/ide/turns":
                # 分岐する場所を選ぶための、IDE会話のユーザー発言一覧
                if not ide_poke or not hasattr(ide_poke, "turns"):
                    return self.send_json({"error": "この環境では Antigravity の会話を読めない（Windows のみ）"}, 400)
                try:
                    s, res = ide_poke.turns(q.get("ide", [""])[0])
                except Exception as e:
                    s, res = None, f"会話を読めなかった: {e}"
                if s is None:
                    return self.send_json({"error": res}, 500)
                return self.send_json({"title": s.get("summary") or "", "steps": s.get("stepCount"), "turns": res})
            if p == "/api/ide/move":
                return self.send_json(moves_info())
            if p == "/api/ide/models":
                # 合図を送るときに選べるモデル（Antigravity の一覧と残りの利用枠）
                if not ide_poke or not hasattr(ide_poke, "models"):
                    return self.send_json({"error": "この環境では Antigravity のモデルを読めない（Windows のみ）"}, 400)
                try:
                    res, why = ide_poke.models()
                except Exception as e:
                    res, why = None, f"モデルの一覧を読めなかった: {e}"
                if res is None:
                    return self.send_json({"error": why}, 500)
                return self.send_json(res)
            if p == "/api/ide/progress":
                # 応答待ちのあいだ、担当の会話がいま何をしているか（ツール呼び出しの一覧。読むだけ）
                s = load(q.get("sid", [""])[0])
                st = s["setting"]
                m_user = next((x for x in s["messages"] if x["role"] == "user" and x.get("status") in ("pending", "processing")), None)
                m_refine = next((x for x in s["messages"] if x.get("image_refine_status") in ("pending", "processing")), None)
                m_choices = next((x for x in s["messages"] if x.get("choices_status") in ("pending", "processing")), None)
                m_mem = next((x for x in s["messages"] if x["role"] == "assistant" and (x.get("memory_status") in ("pending", "processing") or x.get("consolidation_status") in ("pending", "processing"))), None)

                if m_user:
                    ide = st.get("ide_session") or ""
                    m, ttype = m_user, None
                elif m_refine:
                    ide = st.get("ide_session") or ""
                    m, ttype = m_refine, "image_refine"
                elif m_choices:
                    ide = (st.get("choices_ide_session") if has_parallel_choices_ide(st) else st.get("ide_session")) or ""
                    m, ttype = m_choices, "choices"
                elif m_mem:
                    ide = st.get("ide_session") or ""
                    m = m_mem
                    ttype = "memory_extract" if m_mem.get("memory_status") in ("pending", "processing") else "consolidation"
                else:
                    ide = st.get("ide_session") or ""
                    m, ttype = None, None

                if not ide or not m:
                    return self.send_json({"items": []})
                if not ide_poke or not hasattr(ide_poke, "progress"):
                    return self.send_json({"items": [], "note": "この環境では Antigravity の会話を読めない"})
                try:
                    res, why = ide_poke.progress(ide)
                except Exception as e:
                    res, why = None, f"会話を読めなかった: {e}"
                if res is None:
                    return self.send_json({"items": [], "note": why})
                # 止まっている会話の、この入力より前のターンは関係ないので出さない
                if not res["running"] and (_epoch(res["user_time"]) or 0) < (_epoch(task_ts(m, ttype)) or 0) - 1:
                    return self.send_json({"items": [], "running": False,
                                           "note": "会話は止まっている（合図を待っている）"})
                return self.send_json(res)
            if p == "/api/agents":
                # チャンネルの作業フォルダと AGENTS.md の様子（中身のプレビューつき）
                with lock:
                    return self.send_json(agents_info(load(q.get("sid", [""])[0])))
            if p == "/api/meta":
                return self.send_json({"app": app_settings(), "phone": phone})
            if p == "/api/files":
                return self.send_json({"files": system_files()})
            if p == "/api/folder-files":
                return self.send_json({"folders": folder_options()})
            if p == "/api/file-presets":
                return self.send_json({"presets": [x for x in read_json(PRESETS, []) if isinstance(x, dict)]})
            if p == "/api/prompt-preview":
                s = load(q.get("sid", [""])[0])
                block, missing = settings_block(s["setting"])
                if s["setting"].get("image_mode") != "off":
                    block += "\n\n## 固定キャラ（画像に写るときこの外見タグが自動で付く）\n" + chars_block(s["setting"].get("chars") or [])
                return self.send_json({"text": block, "missing": missing, "chars": len(block)})
            if p == "/api/char-presets":
                return self.send_json({"presets": char_presets()})
            if p == "/api/rag":
                with lock:
                    return self.send_json({"collections": rag_summary()})
            if p == "/api/rag/collection":
                with lock:
                    return self.send_json({"collection": load_rag(q.get("id", [""])[0])})
            if p == "/api/rag/memories":
                sid = q.get("session_id", q.get("sid", [""]))[0]
                if not sid:
                    return self.send_json({"error": "session_id is required"}, 400)
                with lock:
                    try:
                        s = load(sid, readonly=True)
                        char_name = (s["setting"].get("character") or {}).get("name", "")
                    except Exception:
                        s = None
                        char_name = ""
                    data = memory_store.load_memories(sid, char_name)
                    if s and isinstance(s.get("messages"), list):
                        msg_map = {m["id"]: m for m in s["messages"] if isinstance(m, dict) and m.get("id")}
                        fallback_el = s.get("setting", {}).get("elapsed_time")
                        backfilled = False
                        for mem in data.get("memories", []):
                            if not mem.get("elapsed_time"):
                                mid = mem.get("mid")
                                m_obj = msg_map.get(mid) if mid else None
                                el = (m_obj.get("elapsed_time") if m_obj else None) or fallback_el
                                if el:
                                    mem["elapsed_time"] = copy.deepcopy(el)
                                    backfilled = True
                        if backfilled:
                            memory_store.save_memories(sid, data)
                    return self.send_json({
                        "ok": True,
                        "session_id": sid,
                        "character_name": data.get("character_name", ""),
                        "memories": data.get("memories", []),
                        "updated_at": data.get("updated_at")
                    })
            if p == "/api/rag/memories/regenerate/status":
                sid = q.get("session_id", q.get("sid", [""]))[0]
                st_info = memory_regen_state.get(sid, {
                    "running": False, "current": 0, "total": 0,
                    "processed_chunks": 0, "total_chunks": 0, "progress": 0,
                    "memories_count": 0, "status": "idle"
                })
                return self.send_json(dict({"ok": True}, **st_info))
            if p == "/api/messages/around":
                mid = q.get("mid", [""])[0]
                if not mid:
                    return self.send_json({"error": "mid is required"}, 400)
                sid = q.get("sid", [""])[0]
                if not sid:
                    sid = session_of_msg(mid)
                s = None
                msgs = []
                if sid:
                    try:
                        s = load(sid, readonly=True)
                        msgs = s.get("messages", [])
                    except Exception:
                        s, msgs = None, []
                target_idx = next((i for i, m in enumerate(msgs) if m.get("id") == mid), -1)
                if target_idx < 0 and os.path.isdir(ARCHIVE_DIR):
                    for fname in sorted(os.listdir(ARCHIVE_DIR), key=archive_sort_key, reverse=True):
                        if not fname.endswith(".json"):
                            continue
                        if sid and not fname.startswith(f"{sid}_"):
                            continue
                        try:
                            with open(os.path.join(ARCHIVE_DIR, fname), "r", encoding="utf-8") as af:
                                arc_s = json.load(af)
                                arc_msgs = arc_s.get("messages", [])
                                a_idx = next((i for i, m in enumerate(arc_msgs) if m.get("id") == mid), -1)
                                if a_idx >= 0:
                                    s = arc_s
                                    msgs = arc_msgs
                                    target_idx = a_idx
                                    sid = sid or arc_s.get("id") or (fname.rsplit("_rot", 1)[0] if "_rot" in fname else fname.split("_")[0])
                                    break
                        except Exception:
                            continue
                if not s or target_idx < 0:
                    return self.send_json({"error": "message not found"}, 404)
                try:
                    w = int(q.get("window", ["5"])[0])
                except (ValueError, TypeError):
                    w = 5
                w = max(1, min(w, 50))
                start = max(0, target_idx - w)
                end = min(len(msgs), target_idx + w + 1)
                res_msgs = []
                st_total = s.get("setting", {}).get("total_turns")
                sdata = load_states_data(sid, readonly=True) if sid else {}
                idata = load_images_data(sid, readonly=True) if sid else {}
                for i in range(start, end):
                    cur = msgs[i]
                    cur_mid = cur.get("id")
                    turn_idx = calc_turn_idx(msgs, i, st_total)
                    et = cur.get("snapshot_elapsed_time") or cur.get("elapsed_time")
                    if not et:
                        for prev in reversed(msgs[:i + 1]):
                            if prev.get("snapshot_elapsed_time"):
                                et = prev["snapshot_elapsed_time"]
                                break
                            elif prev.get("elapsed_time"):
                                et = prev["elapsed_time"]
                                break
                    if not et:
                        et = s.get("setting", {}).get("elapsed_time")
                    im = copy.deepcopy(cur.get("image")) if isinstance(cur.get("image"), dict) else None
                    if im is not None and cur_mid and cur_mid in idata:
                        for k, v in idata[cur_mid].items():
                            if k not in im or im[k] is None:
                                im[k] = copy.deepcopy(v)
                    st_val = copy.deepcopy(cur.get("state")) if isinstance(cur.get("state"), list) else None
                    if st_val is None and cur_mid and cur_mid in sdata:
                        st_val = copy.deepcopy(sdata[cur_mid])
                    res_msgs.append({
                        "id": cur.get("id"),
                        "role": cur.get("role"),
                        "text": cur.get("text", ""),
                        "ts": cur.get("ts"),
                        "turn_idx": turn_idx,
                        "elapsed_time": et,
                        "is_target": (cur.get("id") == mid),
                        "files": cur.get("files"),
                        "image": im,
                        "state": st_val
                    })
                return self.send_json({"ok": True, "sid": sid, "target_idx": target_idx, "messages": res_msgs})
            if p == "/api/push/vapid_key":
                if not Vapid:
                    return self.send_json({"ok": False, "error": "pywebpush not installed"}, 503)
                v, pub = get_vapid()
                if not pub:
                    return self.send_json({"ok": False, "error": "vapid key generation failed"}, 500)
                return self.send_json({"ok": True, "publicKey": pub})
            if p == "/api/push/test":
                if not Vapid:
                    return self.send_json({"ok": False, "error": "pywebpush がインストールされていません"}, 503)
                payload = {
                    "title": "🔔 RP Image Chat テスト通知",
                    "body": "プッシュ通知は正常に動作しています。スマホでの画像生成完了時に通知が届きます。",
                    "icon": "/icon.png",
                    "badge": "/icon.png",
                    "tag": "test-push",
                    "data": {
                        "type": "test",
                        "url": "/"
                    }
                }
                res = send_push_notification(payload)
                if res.get("total", 0) == 0:
                    return self.send_json({"ok": False, "error": "登録されている通知先がありません。先に「通知を有効化」を押してください。"}, 400)
                return self.send_json({"ok": True, "sent": res.get("sent", 0), "total": res.get("total", 0)})
        except (FileNotFoundError, ValueError):
            return self.send_json({"error": "no such session"}, 404)
        if p == "/img":
            f = q.get("f", [""])[0]
            full = os.path.abspath(os.path.normpath(os.path.join(COMFY_OUT, f)))
            base = os.path.normcase(os.path.abspath(COMFY_OUT)).rstrip(os.sep) + os.sep
            if not os.path.normcase(full).startswith(base):
                return self.send_error(403)
            return self.send_file(full)
        if p == "/upload":
            full = upload_abs(q.get("f", [""])[0])
            base_uploads = os.path.normcase(os.path.abspath(UPLOADS)).rstrip(os.sep) + os.sep
            if not os.path.normcase(os.path.abspath(full)).startswith(base_uploads) or not os.path.isfile(full):
                return self.send_error(404)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                b = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype if ctype in INLINE_TYPES else "application/octet-stream")
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype not in INLINE_TYPES:  # HTML などをこのアプリのページとして開かせない
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(os.path.basename(full).split("_", 3)[-1]))
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if p == "/":
            p = "/index.html"
        full = os.path.normpath(os.path.join(PUBLIC, p.lstrip("/")))
        try:
            inside_public = os.path.commonpath((PUBLIC, full)) == PUBLIC
        except ValueError:
            inside_public = False
        if not inside_public:
            return self.send_error(403)
        return self.send_file(full)

    def do_PUT(self):
        self.do_POST()

    def do_DELETE(self):
        self.do_POST()

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        if self.blocked(u, post=True):
            return
        try:
            d = self.body()
        except ValueError as e:
            return self.send_json({"error": f"bad request: {e}"}, 400)
        if p == "/api/push/subscribe":
            if d.get("unsubscribe"):
                ep = d.get("endpoint") or ""
                if ep:
                    with lock:
                        subs = read_json(PUSH_SUBS, {})
                        if isinstance(subs, dict):
                            subs.pop(ep, None)
                        elif isinstance(subs, list):
                            subs = [x for x in subs if isinstance(x, dict) and x.get("endpoint") != ep]
                        write_json(PUSH_SUBS, subs)
                return self.send_json({"ok": True, "unsubscribed": True})
            sub = d.get("subscription") or d
            if not isinstance(sub, dict) or not sub.get("endpoint") or not sub.get("keys"):
                return self.send_json({"error": "invalid subscription object"}, 400)
            ep = sub["endpoint"]
            with lock:
                subs = read_json(PUSH_SUBS, {})
                if not isinstance(subs, dict):
                    subs = {x["endpoint"]: x for x in subs if isinstance(x, dict) and x.get("endpoint")}
                sub_entry = {
                    "endpoint": ep,
                    "keys": sub["keys"],
                    "userAgent": self.headers.get("User-Agent", ""),
                    "updated": now()
                }
                subs[ep] = sub_entry
                write_json(PUSH_SUBS, subs)
            print(f"[push] subscribed: {ep[:45]}...")
            return self.send_json({"ok": True})
        if p == "/api/push/test":
            if not Vapid:
                return self.send_json({"ok": False, "error": "pywebpush がインストールされていません"}, 503)
            payload = {
                "title": "🔔 RP Image Chat テスト通知",
                "body": "プッシュ通知は正常に動作しています。スマホでの画像生成完了時に通知が届きます。",
                "icon": "/icon.png",
                "badge": "/icon.png",
                "tag": "test-push",
                "data": {
                    "type": "test",
                    "url": "/"
                }
            }
            res = send_push_notification(payload)
            if res.get("total", 0) == 0:
                return self.send_json({"ok": False, "error": "登録されている通知先がありません。先に「通知を有効化」を押してください。"}, 400)
            return self.send_json({"ok": True, "sent": res.get("sent", 0), "total": res.get("total", 0)})
        if p == "/api/ide/poke" and d.get("now"):
            # 今すぐ1回だけ合図する（動作確認・取りこぼし用）。送り終わるまで数秒かかるので、ほかを止めないよう lock の外で
            try:
                st = load(d.get("sid", ""))["setting"]
                target = d.get("target") or "main"
                ide = st.get("choices_ide_session") if target == "choices" else st.get("ide_session")
            except (FileNotFoundError, ValueError):
                return self.send_json({"error": "no such session"}, 404)
            if not ide:
                return self.send_json({"error": "担当のIDE会話が決まっていません"}, 400)
            ok, msg, retry = send_poke(ide, full=True)  # 押すのはスペースで動かなかったときなので、コマンドを送る
            return self.send_json({"ok": ok, "msg": msg})
        if p == "/api/ide/new":
            # Antigravity にチャンネルの作業フォルダの新しい会話を作って、このチャンネルの担当にする。作るのに数秒かかるので lock の外で
            if not ide_poke or not hasattr(ide_poke, "new_conversation"):
                return self.send_json({"error": "この環境では Antigravity の会話を作れない（Windows のみ）"}, 400)
            sid = d.get("sid", "")
            try:
                s = load(sid)
                rotation_abort_if_active(s, "手動で担当の会話が変更された")
            except (FileNotFoundError, ValueError, OSError):
                return self.send_json({"error": "no such session"}, 404)
            try:
                cid, title, msg, folder = new_ide(sid, target=d.get("target", "main"))
            except (FileNotFoundError, ValueError):
                return self.send_json({"error": "no such session"}, 404)
            if not cid:
                return self.send_json({"error": msg}, 500)
            return self.send_json({"ok": True, "ide": cid, "title": title, "msg": msg, "folder": folder})
        if p == "/api/pick-folder":
            # 閉じるまで待つので lock の外で
            if not from_this_pc(self.client_address[0]):
                return self.send_json({"error": "フォルダを選ぶウィンドウは PC の画面に出るので、スマホからは選べない"}, 400)
            if not pick_lock.acquire(blocking=False):
                return self.send_json({"error": "フォルダを選ぶウィンドウがもう開いている（PC の画面を見る）", "busy": True}, 409)
            try:
                initial = d.get("initial") if isinstance(d.get("initial"), str) else ""
                return self.send_json({"ok": True, "path": pick_folder(initial)})
            except Exception as e:
                return self.send_json({"error": f"フォルダを選ぶウィンドウを出せなかった: {e}"}, 500)
            finally:
                pick_lock.release()
        if p == "/api/ide/fork":
            # IDE会話を途中（at = ステップ番号、-1 は最後）から分岐する。元の会話はそのまま。assign なら分岐先をこのチャンネルの担当に
            src = d.get("ide") or ""
            if not src:
                return self.send_json({"error": "分岐する会話が決まっていません"}, 400)
            if not ide_poke or not hasattr(ide_poke, "fork"):
                return self.send_json({"error": "この環境では Antigravity の会話を分岐できない（Windows のみ）"}, 400)
            base = ide_poke.FORK_TAG.sub("", d.get("title") or "") or src[:8]
            n = d.get("n")
            title = f"【分岐#{n}】{base}" if n else f"【分岐】{base}"
            try:
                cid, msg = ide_poke.fork(src, int(d.get("at", -1)), title)
            except Exception as e:
                cid, msg = None, f"分岐できなかった: {e}"
            if not cid:
                return self.send_json({"error": msg}, 500)
            # 分岐先は wait_input.py の使い方も覚えているので、合図はスペースだけでよい
            if src in read_json(IDE_KNOWN, []):
                mark_known(cid)
            if d.get("assign"):
                try:
                    with lock:
                        s = load(d.get("sid", ""))
                        s["setting"]["ide_session"] = cid
                        s["setting"]["ide_title"] = title
                        if s["setting"].get("agents_md"):
                            sync_agents(s)
                        save(s)
                except (FileNotFoundError, ValueError):
                    return self.send_json({"error": "分岐はしたが、チャンネルが見つからず担当にできなかった: " + cid}, 404)
            move = None
            if isinstance(d.get("move_to"), str) and d["move_to"].strip():
                # 分岐先は元と同じフォルダにできるので、別のフォルダへ移す予約をする（実際に移すのは /api/ide/move-run）
                folder = resolve_folder(d["move_to"])
                if any(path_key(w) == path_key(folder) for w in ide_workspaces(src)):
                    msg += "。移す先が元と同じフォルダなので移さない"
                else:
                    with lock:
                        try:
                            ch = load(d.get("sid", ""))["setting"].get("title") or d.get("sid", "")
                        except (FileNotFoundError, ValueError):
                            ch = ""
                        pend = read_json(IDE_MOVES, {})
                        move = pend[cid] = {"folder": folder, "sid": d.get("sid", ""), "title": title, "channel": ch, "ts": now()}
                        write_json(IDE_MOVES, pend)
            print(f"[ide] fork {src[:8]} at {d.get('at')} -> {cid[:8]}" + (f" (move to {move['folder']})" if move else ""))
            return self.send_json({"ok": True, "ide": cid, "title": title, "msg": msg, "move": dict(move, ide=cid) if move else None})
        if p == "/api/ide/branch-fork":
            # チャンネルを分岐したとき（src の id のメッセージまでで sid を作った）、担当の会話も同じ時点で分岐して sid の担当にする
            if not ide_poke or not hasattr(ide_poke, "fork"):
                return self.send_json({"ok": True, "skipped": "この環境では Antigravity の会話を分岐できない（Windows のみ）"})
            try:
                with lock:
                    src = load(d.get("src", ""))
                    new = load(d.get("sid", ""))
            except (FileNotFoundError, ValueError):
                return self.send_json({"error": "no such session"}, 404)
            ide = src["setting"].get("ide_session") or ""
            if not ide:
                return self.send_json({"ok": True, "skipped": "元のチャンネルに担当の会話がない"})
            msgs = src["messages"]
            idx = next((i for i, m in enumerate(msgs) if m["id"] == d.get("id")), -1)
            if idx < 0:
                return self.send_json({"error": "no such message"}, 404)
            try:
                info, turns = ide_poke.turns(ide)
                if info is None:
                    return self.send_json({"ok": True, "skipped": turns})
                at, why = branch_fork_point(msgs, idx, turns)
                if why:
                    return self.send_json({"ok": True, "skipped": why})
                title = "【分岐】" + (new["setting"].get("title") or new["id"])
                cid, msg = ide_poke.fork(ide, at, title)
            except Exception as e:
                cid, msg = None, f"分岐できなかった: {e}"
            if not cid:
                return self.send_json({"error": msg}, 500)
            if ide in read_json(IDE_KNOWN, []):
                mark_known(cid)  # 分岐先も wait_input.py の使い方を覚えている
            with lock:
                n = load(new["id"])
                n["setting"].update(
                    ide_session=cid,
                    ide_title=title,
                    ide_model=src["setting"].get("ide_model") or "",
                    choices_ide_model=src["setting"].get("choices_ide_model") or "",
                    choices_ide_auto=bool(n["setting"].get("suggest_choices", True) and not src["setting"].get("choices_ide_manual_off"))
                )
                if n["setting"].get("agents_md"):
                    sync_agents(n)
                save(n)
            print(f"[ide] branch-fork {ide[:8]} at {at} -> {cid[:8]} for {new['id']}")
            return self.send_json({"ok": True, "ide": cid, "title": title, "at": at, "msg": msg})
        if p == "/api/ide/revert":
            # 発言を書き換えたあと（plan_revert で予定を作った）、担当の会話を書き換えた発言の前まで巻き戻す。
            # 巻き戻したら、次の入力ではチャンネルの設定と会話を全部送り直す
            sid = d.get("sid", "")
            with lock:
                plan = revert_plan.pop(sid, None)
            if not plan:
                return self.send_json({"ok": True, "skipped": "巻き戻す予定がない"})
            ide = plan["ide"]
            if not ide_poke or not hasattr(ide_poke, "revert"):
                return self.send_json({"ok": True, "skipped": "この環境では Antigravity の会話を巻き戻せない（Windows のみ）"})
            try:
                with lock:
                    cur = load(sid)["setting"].get("ide_session")
                    shared = [t for x, t in channels_of(ide) if x != sid]
            except (FileNotFoundError, ValueError):
                return self.send_json({"error": "no such session"}, 404)
            if cur != ide:
                return self.send_json({"ok": True, "skipped": "担当の会話が変わったので巻き戻さない"})
            if shared:
                return self.send_json({"ok": True, "skipped": "この会話はほかのチャンネル（" + "、".join(shared)
                                       + "）も担当にしているので巻き戻さない"})
            reverting.add(ide)
            try:
                info, turns = ide_poke.turns(ide)
                if info is None:
                    return self.send_json({"ok": True, "skipped": turns})
                at, why = branch_fork_point(plan["msgs"], plan["keep"], turns)
                if why:
                    why = why.replace("（新規会話を使う）", "").replace("分岐でき", "巻き戻せ").replace("分岐し", "巻き戻さ")
                    return self.send_json({"ok": True, "skipped": why})
                if at == -1:
                    return self.send_json({"ok": True, "skipped": "書き換えた発言はまだ Antigravity の会話に入っていない"})
                ok, msg = ide_poke.revert(ide, at, REVERT_BACKUP)
            except Exception as e:
                ok, msg = False, f"巻き戻せなかった: {e}"
            finally:
                reverting.discard(ide)
            if not ok:
                return self.send_json({"error": msg}, 500)
            write_sent(sid, {"ide": "", "msgs": [], "reset": True, "ts": now(),
                             "why": "担当の会話を巻き戻したので、続きがどこまで残っているか分からない"})
            print(f"[ide] revert {ide[:8]} to step {at} for {sid}")
            return self.send_json({"ok": True, "at": at, "msg": msg})
        if p == "/api/ide/rename":
            # Antigravity の会話の名前を変える。担当にしているチャンネルの控え（ide_title）も合わせる
            ide = d.get("ide") or ""
            title = (d.get("title") or "").strip()
            if not ide or not title:
                return self.send_json({"error": "会話と新しい名前を指定してください"}, 400)
            if not ide_poke or not hasattr(ide_poke, "rename"):
                return self.send_json({"error": "この環境では Antigravity の会話の名前を変えられない（Windows のみ）"}, 400)
            try:
                ok, msg = ide_poke.rename(ide, title)
            except Exception as e:
                ok, msg = False, f"名前を変えられなかった: {e}"
            if not ok:
                return self.send_json({"error": msg}, 500)
            with lock:
                for x, _ in channels_of(ide):
                    s = load(x)
                    if s["setting"].get("ide_session") == ide:
                        s["setting"]["ide_title"] = title
                    if s["setting"].get("choices_ide_session") == ide:
                        s["setting"]["choices_ide_title"] = title
                    save(s)
            print(f"[ide] rename {ide[:8]} -> {title}")
            return self.send_json({"ok": True, "title": title})
        if p == "/api/ide/move":
            # 担当の会話をチャンネルの作業フォルダへ移す予約（cancel で取り消し）。実際に移すのは /api/ide/move-run
            try:
                with lock:
                    s = load(d.get("sid", ""))
                    st = s["setting"]
                    ide = st.get("ide_session") or ""
                    if not ide:
                        return self.send_json({"error": "担当のIDE会話が決まっていません"}, 400)
                    pend = read_json(IDE_MOVES, {})
                    if d.get("cancel"):
                        pend.pop(ide, None)
                        write_json(IDE_MOVES, pend)
                        return self.send_json(dict(moves_info(), ok=True))
                    fixed = not (st.get("ide_folder") or "").strip()
                    folder = channel_folder(s, fix=True)
                    if fixed:
                        save(s)
                    if path_key(folder) == path_key(NEW_IDE_DIR):
                        return self.send_json({"error": f"{folder} はどのチャンネルの会話も使う場所なので、ここへは移さない。作業フォルダを別にする"}, 400)
                    if any(path_key(w) == path_key(folder) for w in ide_workspaces(ide)):
                        return self.send_json({"error": "担当の会話はもうこのフォルダにある"}, 400)
                    pend[ide] = {"folder": folder, "sid": s["id"], "title": st.get("ide_title") or ide[:8],
                                 "channel": st.get("title") or s["id"], "ts": now()}
                    write_json(IDE_MOVES, pend)
                    others = [t for x, t in channels_of(ide) if x != s["id"]]
                return self.send_json(dict(moves_info(), ok=True, others=others, folder=folder))
            except (FileNotFoundError, ValueError):
                return self.send_json({"error": "no such session"}, 404)
        if p == "/api/ide/move-run":
            # 予約した引っ越しをまとめて実行（Antigravity を閉じて、書き換えて、起動し直す）。数十秒かかるので別スレッドで
            if not ide_poke or not hasattr(ide_poke, "move_workspaces"):
                return self.send_json({"error": "この環境では会話を移せない（Windows のみ）"}, 400)
            if move_state.get("running"):
                return self.send_json({"error": "いま移している途中"}, 409)
            if not read_json(IDE_MOVES, {}):
                return self.send_json({"error": "移す予約がない"}, 400)
            try:
                busy = ide_poke.busy_conversations()
            except Exception as e:
                return self.send_json({"error": f"Antigravity の様子を読めなかった: {e}"}, 500)
            if busy is None:
                return self.send_json({"error": "Antigravity が起動していない（起動しておく）"}, 400)
            if busy and not d.get("force"):
                return self.send_json({"busy": busy})
            move_state.clear()
            move_state.update(running=True, msg="Antigravity を閉じて移している…", ts=now())
            threading.Thread(target=run_moves, args=(bool(d.get("force")),), daemon=True).start()
            return self.send_json({"ok": True, "started": True})
        with lock:
            if p == "/api/app-settings":
                cur = read_json(APP_SETTINGS, {})
                for k in DEFAULT_APP:
                    if isinstance(d.get(k), str) and isinstance(DEFAULT_APP[k], str):
                        cur[k] = d[k].strip()
                    elif isinstance(d.get(k), bool) and isinstance(DEFAULT_APP[k], bool):
                        cur[k] = d[k]
                    elif isinstance(d.get(k), list) and isinstance(DEFAULT_APP[k], list):
                        cur[k] = list(dict.fromkeys(x.strip() for x in str_list(d[k])))  # 同じものは1つに
                write_json(APP_SETTINGS, cur)
                return self.send_json({"ok": True, "app": app_settings()})
            if p == "/api/file-presets":
                name = d.get("name")
                if not isinstance(name, str) or not name.strip():
                    return self.send_json({"error": "name is required"}, 400)
                preset = {"id": new_id(), "name": name.strip(), "createdAt": now(),
                          "files": str_list(d.get("files")), "extra": d.get("extra") if isinstance(d.get("extra"), str) else ""}
                presets = read_json(PRESETS, [])
                presets.append(preset)
                write_json(PRESETS, presets)
                return self.send_json({"ok": True, "preset": preset})
            if p == "/api/file-presets/delete":
                presets = read_json(PRESETS, [])
                gone = [x for x in presets if x.get("id") == d.get("id")]
                if not gone:
                    return self.send_json({"error": "preset not found"}, 404)
                os.makedirs(TRASH, exist_ok=True)
                write_json(os.path.join(TRASH, f"filepreset_{gone[0]['id']}_{int(time.time())}.json"), gone[0])
                write_json(PRESETS, [x for x in presets if x.get("id") != d.get("id")])
                return self.send_json({"ok": True})
            if p == "/api/char-presets":
                name = (d.get("name") or "").strip() if isinstance(d.get("name"), str) else ""
                tags = ", ".join(split_tags(d.get("tags") if isinstance(d.get("tags"), str) else ""))
                if not name or not tags:
                    return self.send_json({"error": "名前とタグが必要です"}, 400)
                presets = char_presets()
                cur = find_char(presets, name)  # 同じ名前なら上書き
                if cur:
                    cur.update(name=name, tags=tags, updatedAt=now())
                else:
                    cur = {"id": new_id(), "name": name, "tags": tags, "createdAt": now()}
                    presets.append(cur)
                write_json(CHAR_PRESETS, presets)
                return self.send_json({"ok": True, "preset": cur})
            if p == "/api/char-presets/delete":
                presets = char_presets()
                gone = [x for x in presets if x.get("id") == d.get("id")]
                if not gone:
                    return self.send_json({"error": "preset not found"}, 404)
                os.makedirs(TRASH, exist_ok=True)
                write_json(os.path.join(TRASH, f"charpreset_{gone[0]['id']}_{int(time.time())}.json"), gone[0])
                write_json(CHAR_PRESETS, [x for x in presets if x.get("id") != d.get("id")])
                return self.send_json({"ok": True})
            if p == "/api/rag/memories":
                sid = d.get("session_id") or d.get("sid")
                if not sid:
                    return self.send_json({"error": "session_id is required"}, 400)
                cat_raw = (d.get("category") or "").strip()
                cat = memory_store.CATEGORY_ALIASES.get(cat_raw.lower(), cat_raw)
                content = (d.get("content") or "").strip()
                if not content:
                    return self.send_json({"error": "content is required"}, 400)
                if cat not in memory_store.VALID_CATEGORIES:
                    return self.send_json({"error": f"invalid category: {cat_raw}"}, 400)
                with lock:
                    try:
                        s = load(sid, readonly=True)
                        char_name = (s["setting"].get("character") or {}).get("name", "")
                        elapsed_time = d.get("elapsed_time") or s["setting"].get("elapsed_time")
                    except Exception:
                        char_name = ""
                        elapsed_time = d.get("elapsed_time")
                    try:
                        mem = memory_store.add_manual_memory(sid, char_name, cat, content, elapsed_time=elapsed_time, status=d.get("status", "active"))
                        return self.send_json({"ok": True, "memory": mem})
                    except Exception as e:
                        return self.send_json({"error": str(e)}, 500)

            if p.startswith("/api/rag/memories/") and not p.startswith("/api/rag/memories/regenerate"):
                mem_id = p[len("/api/rag/memories/"):].strip().split("/")[0].split("?")[0]
                sid = d.get("session_id") or d.get("sid") or q.get("session_id", q.get("sid", [""]))[0]
                if not sid:
                    return self.send_json({"error": "session_id is required"}, 400)
                is_delete = (self.command == "DELETE") or d.get("action") == "delete" or p.endswith("/delete")
                with lock:
                    if is_delete:
                        try:
                            memory_store.delete_memory(sid, mem_id)
                            return self.send_json({"ok": True})
                        except KeyError:
                            return self.send_json({"error": "memory not found"}, 404)
                        except Exception as e:
                            return self.send_json({"error": str(e)}, 500)
                    else:
                        cat = d.get("category")
                        content = d.get("content")
                        status = d.get("status")
                        try:
                            mem = memory_store.update_memory(sid, mem_id, category=cat, content=content, status=status)
                            return self.send_json({"ok": True, "memory": mem})
                        except KeyError:
                            return self.send_json({"error": "memory not found"}, 404)
                        except ValueError as e:
                            return self.send_json({"error": str(e)}, 400)
                        except Exception as e:
                            return self.send_json({"error": str(e)}, 500)

            if p == "/api/rag/memories/regenerate":
                sid = d.get("session_id") or d.get("sid")
                if not sid:
                    return self.send_json({"error": "session_id is required"}, 400)
                with lock:
                    if memory_regen_state.get(sid, {}).get("running"):
                        return self.send_json({"error": "already running"}, 409)
                    try:
                        s = load(sid)
                        char_name = (s["setting"].get("character") or {}).get("name", "")
                    except Exception:
                        return self.send_json({"error": "no such session"}, 404)

                    memory_store.clear_auto_memories(sid)

                    user_indices = [i for i, m in enumerate(s["messages"]) if m.get("role") == "user"]
                    if len(user_indices) > 100:
                        user_indices = user_indices[-100:]

                    chunks = []
                    chunk_size = 10
                    for i in range(0, len(user_indices), chunk_size):
                        chunk_u_idx = user_indices[i:i + chunk_size]
                        start_i = chunk_u_idx[0]
                        if i + chunk_size < len(user_indices):
                            end_i = user_indices[i + chunk_size]
                        else:
                            end_i = len(s["messages"])
                        chunks.append(s["messages"][start_i:end_i])

                    if d.get("memories"):
                        memory_store.save_extracted_memories(
                            sid,
                            char_name,
                            d.get("memories"),
                            turn_idx=(s["setting"].get("total_turns") or len(user_indices)),
                            elapsed_time=s.get("setting", {}).get("elapsed_time")
                        )
                        mem_data = memory_store.load_memories(sid)
                        mem_count = len(mem_data.get("memories", []))
                        memory_regen_state[sid] = {
                            "running": False, "current": len(chunks), "total": len(chunks),
                            "processed_chunks": len(chunks), "total_chunks": len(chunks),
                            "progress": 100, "memories_count": mem_count, "status": "completed"
                        }
                        return self.send_json({"ok": True, "total": len(chunks), "current": len(chunks), "status": "completed"})

                    total = len(chunks)
                    if total == 0:
                        mem_data = memory_store.load_memories(sid)
                        mem_count = len(mem_data.get("memories", []))
                        memory_regen_state[sid] = {
                            "running": False, "current": 0, "total": 0,
                            "processed_chunks": 0, "total_chunks": 0,
                            "progress": 100, "memories_count": mem_count, "status": "completed"
                        }
                        return self.send_json({"ok": True, "total": 0, "current": 0, "status": "completed"})

                    memory_regen_state[sid] = {
                        "running": True, "current": 0, "total": total,
                        "processed_chunks": 0, "total_chunks": total,
                        "progress": 0, "memories_count": 0, "status": "processing"
                    }
                    threading.Thread(target=run_memory_regeneration, args=(sid, chunks, char_name), daemon=True).start()
                    return self.send_json({"ok": True, "total": total, "current": 0, "status": "processing"})

            if p == "/api/rag/create":
                name = d.get("name", "")
                if not isinstance(name, str) or not name.strip():
                    return self.send_json({"error": "name is required"}, 400)
                col = {"id": new_id(), "name": name.strip(), "entries": []}
                save_rag(col)
                return self.send_json({"ok": True, "collection": col})
            if p in ("/api/rag/delete", "/api/rag/entry/save", "/api/rag/entry/delete"):
                try:
                    col = load_rag(d.get("id"))
                except (OSError, ValueError, json.JSONDecodeError):
                    return self.send_json({"error": "no such collection"}, 404)
                if p == "/api/rag/delete":
                    os.makedirs(TRASH, exist_ok=True)
                    shutil.move(rag_path(col["id"]), os.path.join(TRASH, f"rag_{col['id']}_{int(time.time())}.json"))
                    for sid in list_ids():
                        session = load(sid)
                        selected = session["setting"].get("rag_collections", [])
                        if col["id"] in selected:
                            session["setting"]["rag_collections"] = [x for x in selected if x != col["id"]]
                            save(session)
                    return self.send_json({"ok": True})
                entries = col.setdefault("entries", [])
                if p == "/api/rag/entry/save":
                    title, content = d.get("title"), d.get("content")
                    if not isinstance(title, str) or not title.strip() or not isinstance(content, str) or not content.strip():
                        return self.send_json({"error": "title and content are required"}, 400)
                    entry_id = d.get("entry_id")
                    if entry_id:
                        entry = next((e for e in entries if e.get("id") == entry_id), None)
                        if entry is None:
                            return self.send_json({"error": "no such entry"}, 404)
                        entry.update(title=title.strip(), content=content.strip())
                    else:
                        entry = {"id": new_id(), "title": title.strip(), "content": content.strip()}
                        entries.append(entry)
                    save_rag(col)
                    return self.send_json({"ok": True, "entry": entry})
                entry_id = d.get("entry_id")
                gone = [e for e in entries if e.get("id") == entry_id]
                if not gone:
                    return self.send_json({"error": "no such entry"}, 404)
                os.makedirs(TRASH, exist_ok=True)
                write_json(os.path.join(TRASH, f"ragentry_{col['id']}_{int(time.time())}.json"), gone[0])
                col["entries"] = [e for e in entries if e.get("id") != entry_id]
                save_rag(col)
                return self.send_json({"ok": True})
            if p == "/api/session/new":
                base = d.get("setting")
                if isinstance(base, dict):
                    # 新規チャンネル：環境・画像設定のみ引き継ぎ、ストーリー・キャラ・進行状態は初期化
                    base = copy.deepcopy(base)
                    for k in ("elapsed_time", "initial_elapsed_time", "rotation_notice", "last_memo_mid", "rotation", "rotation_skip_until", "rotation_reserve"):
                        base.pop(k, None)
                    for k in DEPRECATED_SETTING_KEYS:
                        base.pop(k, None)
                    if isinstance(base.get("image"), dict):
                        for k in DEPRECATED_IMAGE_KEYS:
                            base["image"].pop(k, None)
                    base.update(copy.deepcopy(RESET_PROGRESS))
                    base.update(copy.deepcopy(RESET_STORY))
                    base.update(NO_IDE)
                    base.update(NO_FOLDER)
                else:
                    base = None
                if d.get("copy_from"):
                    try:
                        src_st = copy.deepcopy(load(d["copy_from"])["setting"])
                        for k in ("elapsed_time", "initial_elapsed_time", "rotation_notice", "last_memo_mid", "rotation", "rotation_skip_until", "rotation_reserve"):
                            src_st.pop(k, None)
                        for k in DEPRECATED_SETTING_KEYS:
                            src_st.pop(k, None)
                        if isinstance(src_st.get("image"), dict):
                            for k in DEPRECATED_IMAGE_KEYS:
                                src_st["image"].pop(k, None)
                        src_st.update(copy.deepcopy(RESET_PROGRESS))
                        # 複製は RESET_STORY を適用しない（キャラ・シナリオは引き継ぐ）
                        src_st.update(NO_IDE)
                        src_st.update(NO_FOLDER)
                        src_st["title"] = src_st.get("title", "") + " (コピー)"
                        base = src_st
                    except Exception:
                        pass
                if d.get("title"):
                    base = dict(base or {}, title=d["title"])
                s = create_session(base)
                return self.send_json({"ok": True, "sid": s["id"]})
            if p == "/api/session/import":
                try:
                    st, msgs = clean_import(d.get("data"))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                st["title"] = (st.get("title") or "取り込み") + " (取込)"
                s = create_session(st, msgs)
                return self.send_json({"ok": True, "sid": s["id"]})
            try:
                s = load(d.get("sid", ""))
            except (FileNotFoundError, ValueError):
                return self.send_json({"error": "no such session"}, 404)
            sid = s["id"]

            if p == "/api/session/delete":
                old = read_json(AGENTS_LOG, {}).get(sid)
                if old:  # 消したチャンネルのルールが作業フォルダに残らないように
                    retire_agents(sid, old)
                session_revs.pop(sid, None)
                session_cache.pop(sid, None)
                choices_cache.pop(sid, None)
                states_cache.pop(sid, None)
                images_cache.pop(sid, None)
                sent_cache.pop(sid, None)
                session_ide_cache.pop(sid, None)
                summary_cache.pop(sid, None)
                with rotation_guard:
                    rotation_queue.pop(sid, None)
                cp = choices_path(sid)
                if os.path.exists(cp):
                    try:
                        shutil.move(cp, os.path.join(TRASH, f"{sid}_{int(time.time())}_choices.json"))
                    except Exception:
                        pass
                sp = states_path(sid)
                if os.path.exists(sp):
                    try:
                        shutil.move(sp, os.path.join(TRASH, f"{sid}_{int(time.time())}_states.json"))
                    except Exception:
                        pass
                ip = images_path(sid)
                if os.path.exists(ip):
                    try:
                        shutil.move(ip, os.path.join(TRASH, f"{sid}_{int(time.time())}_images.json"))
                    except Exception:
                        pass
                mp = memory_store.memory_path(sid)
                if os.path.exists(mp):
                    try:
                        shutil.move(mp, os.path.join(TRASH, f"{sid}_{int(time.time())}_memories.json"))
                    except Exception:
                        pass
                os.makedirs(TRASH, exist_ok=True)
                shutil.move(spath(sid), os.path.join(TRASH, f"{sid}_{int(time.time())}.json"))
                return self.send_json({"ok": True})
            if p == "/api/session/branch":
                idx = next((i for i, m in enumerate(s["messages"]) if m["id"] == d.get("id")), -1)
                if idx < 0:
                    return self.send_json({"error": "no such message"}, 404)
                orig_msgs = s["messages"][:idx + 1]
                st, msgs = clean_import({"setting": s["setting"], "messages": copy.deepcopy(orig_msgs)})
                st["title"] = (s["setting"].get("title") or "") + " (分岐)"
                n = create_session(st, msgs)
                restore_latest_snapshot(n)
                n["setting"]["initial_elapsed_time"] = copy.deepcopy(n["setting"]["elapsed_time"])
                n["setting"]["initial_memo"] = n["setting"].get("memo", "")

                # 分岐元から新セッションIDへ states と images を複製
                try:
                    old_states = load_states_data(s["id"], readonly=True)
                    old_images = load_images_data(s["id"], readonly=True)
                    new_states = {}
                    new_images = {}
                    for old_m, new_m in zip(orig_msgs, msgs):
                        old_mid = old_m.get("id")
                        new_mid = new_m.get("id")
                        if old_mid and new_mid:
                            if old_mid in old_states:
                                new_states[new_mid] = copy.deepcopy(old_states[old_mid])
                            elif old_m.get("state"):
                                new_states[new_mid] = copy.deepcopy(old_m["state"])
                            if old_mid in old_images:
                                new_images[new_mid] = copy.deepcopy(old_images[old_mid])
                            elif isinstance(old_m.get("image"), dict):
                                im_details = {k: v for k, v in old_m["image"].items() if k not in ("file", "status", "error")}
                                if im_details:
                                    new_images[new_mid] = copy.deepcopy(im_details)
                            if new_mid in new_states:
                                new_m["state"] = copy.deepcopy(new_states[new_mid])
                            if new_mid in new_images and isinstance(new_m.get("image"), dict):
                                new_m["image"].update(copy.deepcopy(new_images[new_mid]))
                    if new_states:
                        save_states_data(n["id"], new_states)
                    if new_images:
                        save_images_data(n["id"], new_images)
                except Exception as e:
                    print(f"[branch] error copying states/images: {e}")

                # 通算ターン番号は分岐元の番号を引き継ぐ（ローテーション後でも巻き戻らない）
                src_total = s["setting"].get("total_turns")
                # 通算ターンは「分岐元の末尾」ではなく「分岐位置 idx」で計算する（prefix では基準がずれる）
                last_turn = calc_turn_idx(s["messages"], idx, src_total) if s["messages"] else 0
                n["setting"]["total_turns"] = last_turn
                save(n)
                try:
                    # 直近がユーザー発言なら、そのターンの記憶はまだ無い（分岐後に応答を作り直すため）
                    mem_turn = max(0, last_turn - 1) if (orig_msgs and orig_msgs[-1].get("role") == "user") else last_turn
                    orig_mem_data = memory_store.load_memories(s["id"])
                    if orig_mem_data.get("memories"):
                        branch_mem_data = copy.deepcopy(orig_mem_data)
                        branch_mem_data["session_id"] = n["id"]
                        mid_map = {o.get("id"): nm.get("id") for o, nm in zip(orig_msgs, msgs) if o.get("id") and nm.get("id")}
                        for mem in branch_mem_data.get("memories", []):
                            if mem.get("mid") in mid_map:
                                mem["mid"] = mid_map[mem["mid"]]
                            if mem.get("resolved_mid") in mid_map:
                                mem["resolved_mid"] = mid_map[mem["resolved_mid"]]
                            for h in mem.get("history") or []:
                                if isinstance(h, dict) and h.get("mid") in mid_map:
                                    h["mid"] = mid_map[h["mid"]]
                        memory_store.save_memories(n["id"], branch_mem_data)
                        memory_store.delete_after_turn(n["id"], mem_turn)
                except Exception as e:
                    print(f"[branch] error copying memories: {e}")
                return self.send_json({"ok": True, "sid": n["id"]})
            if p == "/api/session/raw":
                try:
                    st = merge_defaults(d.get("setting"))
                    msgs = d.get("messages")
                    if not isinstance(msgs, list) or any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                                                         or not isinstance(m.get("text", ""), str) for m in msgs):
                        raise ValueError("messages が不正です")
                except (ValueError, TypeError, AttributeError) as e:
                    return self.send_json({"error": str(e)}, 400)
                backup(s, "rawedit")
                for m in msgs:
                    m.setdefault("id", new_id())
                    m.setdefault("ts", now())
                s["setting"] = st
                s["messages"] = msgs
                save(s)
                return self.send_json({"ok": True})
            if p == "/api/context":
                # wait_input.py が受け取った入力の、AIに見せる内容（全部か差分）
                m = find_msg(s, d.get("id"))
                if not m:
                    return self.send_json({"error": "no such message"}, 404)
                req_type = d.get("type")
                # type 未指定時のフォールバック。優先順位は /api/pending の type_prio と一致させる
                # （user > image_refine > choices > memory_extract > consolidation）
                if not req_type and m:
                    if m.get("role") == "user" and m.get("status") in ("pending", "processing"):
                        req_type = "user"
                    elif m.get("image_refine_status") in ("pending", "processing"):
                        req_type = "image_refine"
                    elif m.get("choices_status") in ("pending", "processing"):
                        req_type = "choices"
                    elif m.get("memory_status") in ("pending", "processing"):
                        req_type = "memory_extract"
                    elif m.get("consolidation_status") in ("pending", "processing"):
                        req_type = "consolidation"
                    elif m.get("role") == "user":
                        req_type = "user"

                ide = d.get("ide") or ""
                note = d.get("note") or ""
                if req_type == "user" or (not req_type and m.get("role") == "user"):
                    return self.send_json(with_render(s, m, build_context(s, m), None, ide, note))
                elif req_type == "image_refine":
                    return self.send_json(with_render(s, m, build_image_refine_context(s, m, base_prompt=m.get("image_refine_prompt")),
                                                      "image_refine", ide, note))
                elif req_type == "choices":
                    if m.get("role") == "user":
                        return self.send_json(with_render(s, m, build_parallel_choices_context(s, m), "choices", ide, note))
                    ctype = m.get("choices_type") or d.get("choices_type") or "normal"
                    if ctype == "erotic":
                        return self.send_json(with_render(s, m, build_choices_context(s, m), "choices", ide, note))
                    return self.send_json(with_render(s, m, build_normal_choices_context(s, m), "choices", ide, note))
                elif req_type == "consolidation":
                    return self.send_json(with_render(s, m, build_consolidation_context(s, m), "consolidation", ide, note))
                elif req_type == "memory_extract":
                    return self.send_json(with_render(s, m, build_memory_extract_context(s, m), "memory_extract", ide, note))
                return self.send_json({"error": "invalid message for context"}, 400)
            if p == "/api/context/reset":
                # 次の入力では設定と会話を全部送る（AIが忘れていそうなとき用）
                write_sent(s["id"], {"ide": "", "msgs": [], "reset": True, "ts": now()})
                return self.send_json({"ok": True})
            if p == "/api/upload":
                # 発言に付けるファイル。送信するまではどの発言にも付かない（送信で files に入れる）
                data = d.get("data")
                if not isinstance(data, str) or not data:
                    return self.send_json({"error": "data is required"}, 400)
                try:
                    raw = base64.b64decode(data.split(",", 1)[-1] if data.startswith("data:") else data, validate=True)
                except (ValueError, binascii.Error):
                    return self.send_json({"error": "data must be base64"}, 400)
                if len(raw) > UPLOAD_MAX:
                    return self.send_json({"error": f"ファイルが大きすぎます（{UPLOAD_MAX // 1024 // 1024}MB まで）"}, 400)
                return self.send_json({"ok": True, "file": save_upload(sid, d.get("name"), raw)})
            if p == "/api/send":
                text = (d.get("text") or "").strip()
                try:
                    files = clean_files(sid, d.get("files"))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                if not text and not files:
                    return self.send_json({"error": "empty"}, 400)
                m = {"id": new_id(), "role": "user", "text": text, "ts": now(), "status": "pending"}
                if files:
                    m["files"] = files
                if has_parallel_choices_ide(s["setting"]):
                    m["choices_status"] = "pending"
                    m["choices_type"] = "normal"
                    m["choices_ts"] = now()
                    m["choices_orig_id"] = m["id"]
                for msg in s["messages"]:
                    msg.pop("choices_status", None)
                    msg.pop("choices_type", None)
                    msg.pop("choices_ts", None)
                    msg.pop("choices_claimed_ts", None)
                    msg.pop("choices_orig_id", None)
                    msg.pop("staged_choices", None)
                    msg.pop("memory_status", None)
                    msg.pop("memory_ts", None)
                    msg.pop("memory_claimed_ts", None)
                s["messages"].append(m)
                # total_turns は「通算ユーザーターン番号」の唯一の真実。増えるのは送信時だけ。
                # 0 のときだけ total_turns_base で補填（旧セッションの巻き戻り防止）
                in_file = user_turns_of(s["messages"])
                prev_total = total_turns_base(s["setting"], max(0, in_file - 1))
                s["setting"]["total_turns"] = max(prev_total, in_file - 1) + 1
                rotation_maybe_arm(s)      # ← P2-1（30ターン目なら armed / 数ミリ秒）
                save(s)
                return self.send_json({"ok": True, "id": m["id"], "session": s})
            if p == "/api/send/stop":
                # 送信中の入力を取り消す（応答待ちの表示を止める）。
                # 担当IDEがすでに生成中でも、この発言を消すので、あとから届いた応答は outbox 側で
                # stale になり取り込まれない。入力した本文と添付は返して、画面の入力欄に戻せるようにする。
                target_id = d.get("id")
                if target_id:
                    # 画面の発言ごとの「停止」からは、押した発言を狙って止める
                    target = find_msg(s, target_id)
                    if not target or target.get("role") != "user" or target.get("status") not in ("pending", "processing"):
                        target = None
                else:
                    target = next((m for m in reversed(s["messages"])
                                   if m.get("role") == "user" and m.get("status") in ("pending", "processing")), None)
                if not target:
                    return self.send_json({"error": "停止できる送信がありません（もう応答が届いています）"}, 409)
                target_seen = target.get("status") == "processing"
                rotation_abort_if_active(s, "送信が停止された")
                backup(s, "stop")
                if target_seen:
                    # 担当IDEがもう読んでしまった発言。次の入力では、停止した発言とその応答を
                    # 無かったものとして扱うよう、全部送り直す（IDE側の会話履歴は消せないため、明示する）
                    sent = read_sent(sid)
                    sent.update({"ide": "", "msgs": [], "reset": True, "ts": now(),
                                 "why": "ユーザーが送信を停止した（停止した発言と、それへの応答は無かったものとして扱う）"})
                    write_sent(sid, sent)
                target_idx = s["messages"].index(target)
                old_total = s["setting"].get("total_turns") or 0
                deleted_turn = calc_turn_idx(s["messages"], target_idx, old_total)
                later_user = any(m.get("role") == "user" for m in s["messages"][target_idx + 1:])
                s["messages"] = [m for m in s["messages"] if m.get("id") != target["id"]]
                memory_store.delete_by_mid(sid, target["id"])
                # 取り消したのはユーザーターン1つ分なので、通算ターンも1つ戻す
                new_total = max(0, old_total - 1)
                s["setting"]["total_turns"] = new_total
                if later_user:
                    memory_store.shift_turn_indices(sid, deleted_turn, -1)
                else:
                    memory_store.delete_after_turn(sid, new_total)
                for k in [k for k in claimed_at if k == target["id"] or k.startswith(target["id"] + ":")]:
                    claimed_at.pop(k, None)
                restore_latest_snapshot(s)
                save(s)
                poke_event.set()
                return self.send_json({"ok": True, "id": target["id"], "text": target.get("text", ""),
                                       "files": target.get("files") or [], "session": s})
            if p == "/api/message/alter":
                rotation_abort_if_active(s, "会話の書き換えが行われた")
                m = find_msg(s, d.get("id"))
                text = d.get("text")
                if not m or m.get("role") != "assistant":
                    return self.send_json({"error": "no such assistant message"}, 404)
                if not isinstance(text, str) or not text.strip():
                    return self.send_json({"error": "text is required"}, 400)
                rv = text.strip() != m["text"] and plan_revert(s, s["messages"].index(m) - 1)
                m.setdefault("original_text", m["text"])
                m["text"] = text.strip()
                m["edited"] = True
                save(s)
                return self.send_json({"ok": True, "revert": rv})
            if p == "/api/message/state":
                # 応答のときのキャラの状態を手で直す（次の入力でそのまま AI に見せるので、巻き戻しはいらない）
                mid = d.get("id") or d.get("mid")
                m = find_msg(s, mid)
                if not m or m.get("role") != "assistant":
                    return self.send_json({"error": "no such assistant message"}, 404)
                if not isinstance(d.get("state"), list):
                    return self.send_json({"error": "state must be a list"}, 400)
                new_st = merge_state([], [u for u in clean_state(d["state"]) if not u.get("remove")])
                m["state"] = new_st
                m["state_turn"] = [
                    {
                        "name": c.get("name", ""),
                        "pose": c.get("pose", ""),
                        "expression": list(c.get("expression") or [])
                    }
                    for c in new_st
                ]
                sdata = load_states_data(sid)
                sdata[m["id"]] = new_st
                save_states_data(sid, sdata)
                save(s)
                return self.send_json({"ok": True, "state": new_st})
            if p in ("/api/message/edit", "/api/message/regenerate", "/api/message/truncate"):
                idx = next((i for i, m in enumerate(s["messages"]) if m["id"] == d.get("id")), -1)
                if idx < 0:
                    return self.send_json({"error": "no such message"}, 404)
                rotation_abort_if_active(s, "会話の書き換えが行われた")
                before_turns = user_turns_of(s["messages"])  # ← 追加（書き換え前）
                new_user, rv = None, False
                if p == "/api/message/truncate":
                    backup(s, "truncate")
                    rv = idx + 1 < len(s["messages"]) and plan_revert(s, idx)
                    s["messages"] = s["messages"][:idx + 1]
                elif p == "/api/message/edit":
                    old = s["messages"][idx]
                    text = d.get("text")
                    if old["role"] != "user":
                        return self.send_json({"error": "not a user message"}, 400)
                    if not isinstance(text, str) or not text.strip():
                        return self.send_json({"error": "text is required"}, 400)
                    backup(s, "edit")
                    rv = plan_revert(s, idx - 1)
                    new_user = {"id": new_id(), "role": "user", "text": text.strip(), "ts": now(), "status": "pending"}
                    if old.get("files"):
                        new_user["files"] = old["files"]
                    if has_parallel_choices_ide(s["setting"]):
                        new_user["choices_status"] = "pending"
                        new_user["choices_type"] = "normal"
                        new_user["choices_ts"] = now()
                        new_user["choices_orig_id"] = new_user["id"]
                    s["messages"] = s["messages"][:idx] + [new_user]
                else:
                    if s["messages"][idx]["role"] != "assistant":
                        return self.send_json({"error": "not an assistant message"}, 400)
                    user_idx = next((i for i in range(idx - 1, -1, -1) if s["messages"][i]["role"] == "user"), -1)
                    if user_idx < 0:
                        return self.send_json({"error": "no preceding user message"}, 400)
                    backup(s, "regenerate")
                    rv = plan_revert(s, user_idx - 1)
                    target_mid = s["messages"][idx]["id"]
                    s["messages"] = s["messages"][:user_idx + 1]
                    new_user = s["messages"][-1]
                    new_user["id"] = new_id()
                    new_user["status"] = "pending"
                    new_user.pop("pasted", None)  # 担当の会話は巻き戻すので、画像はもう一度貼る
                    new_user.pop("staged_choices", None)
                    new_user["ts"] = now()
                    if has_parallel_choices_ide(s["setting"]):
                        new_user["choices_status"] = "pending"
                        new_user["choices_type"] = "normal"
                        new_user["choices_ts"] = now()
                        new_user["choices_orig_id"] = new_user["id"]
                    else:
                        new_user.pop("choices_status", None)
                        new_user.pop("choices_type", None)
                        new_user.pop("choices_ts", None)
                        new_user.pop("choices_claimed_ts", None)
                        new_user.pop("choices_orig_id", None)
                if new_user:
                    for msg in s["messages"][:-1]:
                        msg.pop("choices_status", None)
                        msg.pop("choices_type", None)
                        msg.pop("choices_ts", None)
                        msg.pop("choices_claimed_ts", None)
                        msg.pop("choices_orig_id", None)
                        msg.pop("staged_choices", None)
                remaining_turn = user_turns_of(s["messages"])
                # 削除したユーザーターン数だけ通算を戻す（in_file から再計算しない＝巻き戻り事故の防止）
                removed = max(0, before_turns - remaining_turn)
                prev_total = total_turns_base(s["setting"], before_turns)
                s["setting"]["total_turns"] = max(0, prev_total - removed)
                current_total = s["setting"]["total_turns"]
                if p == "/api/message/regenerate":
                    memory_store.delete_by_mid(sid, target_mid)
                    memory_store.delete_after_turn(sid, max(0, current_total - 1))
                elif p == "/api/message/edit":
                    memory_store.delete_after_turn(sid, max(0, current_total - 1))
                else:
                    # truncate: if ending on a user message, its assistant response was cut
                    if s["messages"] and s["messages"][-1].get("role") == "user":
                        memory_store.delete_after_turn(sid, max(0, current_total - 1))
                    else:
                        memory_store.delete_after_turn(sid, current_total)
                restore_latest_snapshot(s)
                save(s)
                return self.send_json({"ok": True, "id": new_user["id"] if new_user else None, "revert": rv})
            if p == "/api/message/regenerate-choices":
                target_id = d.get("id")
                if target_id:
                    idx = next((i for i, m in enumerate(s["messages"]) if m["id"] == target_id), -1)
                else:
                    idx = next((i for i in range(len(s["messages"]) - 1, -1, -1) if s["messages"][i]["role"] == "assistant"), -1)
                if idx < 0:
                    return self.send_json({"error": "no assistant message found"}, 404)
                m = s["messages"][idx]
                if m["role"] != "assistant":
                    return self.send_json({"error": "not an assistant message"}, 400)
                if any(msg["role"] == "user" and msg.get("status") in ("pending", "processing") for msg in s["messages"]):
                    return self.send_json({"error": "現在、別のメッセージを応答中です"}, 400)
                if any(msg.get("choices_status") in ("pending", "processing") for msg in s["messages"]):
                    return self.send_json({"error": "現在、選択肢を生成中です"}, 400)
                backup(s, "regenerate_choices")
                m["choices_status"] = "pending"
                m["choices_type"] = d.get("type") or "erotic"
                m["choices_ts"] = now()
                save(s)
                poke_event.set()
                return self.send_json({"ok": True, "id": m["id"]})
            if p == "/api/rag/select":
                ids = d.get("ids")
                if not isinstance(ids, list) or any(not isinstance(cid, str) for cid in ids):
                    return self.send_json({"error": "ids must be a list"}, 400)
                known = {c["id"] for c in rag_summary()}
                if any(cid not in known for cid in ids):
                    return self.send_json({"error": "unknown collection"}, 400)
                s["setting"]["rag_collections"] = list(dict.fromkeys(ids))
                save(s)
                return self.send_json({"ok": True})
            if p == "/api/ide/assign":
                rotation_abort_if_active(s, "手動で担当の会話が変更された")
                ide, title = d.get("ide", ""), d.get("title", "")
                target = d.get("target") or "main"
                if not isinstance(ide, str) or not isinstance(title, str) or not all(c.isalnum() or c == "-" for c in ide):
                    return self.send_json({"error": "bad ide session"}, 400)
                if ide:
                    if target == "choices" and ide == s["setting"].get("ide_session"):
                        return self.send_json({"error": "メイン担当と同じ会話は指定できません。別の会話を選ぶか「新規会話」を押してください"}, 400)
                    if target == "main" and ide == s["setting"].get("choices_ide_session"):
                        return self.send_json({"error": "選択肢担当と同じ会話は指定できません。別の会話を選んでください"}, 400)
                # ほかのチャンネルがもうこの会話を担当にしていたら、どこかを知らせて確かめる（force でそのままつなぐ）
                others = []
                check_field = "choices_ide_session" if target == "choices" else "ide_session"
                if ide and ide != s["setting"].get(check_field):
                    for x, t in channels_of(ide):  # 同じ名前のチャンネルもあるので、プロジェクトとIDも付ける
                        if x != s["id"]:
                            pj = load(x, readonly=True)["setting"].get("project") or ""
                            others.append(f"{t}（{pj + '・' if pj else ''}{x[:6]}）")
                if others and not d.get("force"):
                    return self.send_json({"conflict": others})
                if target == "choices":
                    s["setting"]["choices_ide_session"] = ide
                    s["setting"]["choices_ide_title"] = title if ide else ""
                    s["setting"]["choices_ide_auto"] = False
                    s["setting"]["choices_ide_manual_off"] = not bool(ide)
                    if ide:
                        sync_choices_agents(s)
                else:
                    s["setting"]["ide_session"] = ide
                    s["setting"]["ide_title"] = title if ide else ""
                    s["setting"]["ide_auto"] = False
                    if ide and s["setting"].get("suggest_choices", True) and not has_parallel_choices_ide(s["setting"]) and not s["setting"].get("choices_ide_manual_off"):
                        s["setting"]["choices_ide_auto"] = True
                    if s["setting"].get("agents_md"):
                        sync_agents(s)
                save(s)
                return self.send_json({"ok": True})
            if p == "/api/session/rename":
                # チャンネルの名前だけ変える（作業フォルダは決まっていれば動かない）
                title = (d.get("title") or "").strip() if isinstance(d.get("title"), str) else ""
                if not title:
                    return self.send_json({"error": "新しい名前を入れてください"}, 400)
                if not (s["setting"].get("ide_folder") or "").strip() and s["setting"].get("ide_session"):
                    channel_folder(s, fix=True)  # 担当の会話がある間は、名前を変えてもいまのフォルダのまま
                s["setting"]["title"] = title
                if s["setting"].get("agents_md"):
                    sync_agents(s)  # AGENTS.md にチャンネル名が入っている
                save(s)
                return self.send_json({"ok": True, "title": title})
            if p == "/api/ide/model":
                # 合図を送るときのモデル。空なら会話の最後の入力と同じ
                model = d.get("model") or ""
                target = d.get("target") or "main"
                if not isinstance(model, str):
                    return self.send_json({"error": "model must be a string"}, 400)
                field = "choices_ide_model" if target == "choices" else "ide_model"
                s["setting"][field] = model.strip()
                save(s)
                return self.send_json({"ok": True, "model": s["setting"][field]})
            if p == "/api/claim":
                m = find_msg(s, d.get("id"))
                req_type = d.get("type")
                # type 未指定時のフォールバック。優先順位は /api/pending の type_prio と一致させる
                if not req_type and m:
                    if m.get("role") == "user" and m.get("status") == "pending":
                        req_type = "user"
                    elif m.get("image_refine_status") == "pending":
                        req_type = "image_refine"
                    elif m.get("choices_status") == "pending":
                        req_type = "choices"
                    elif m.get("memory_status") == "pending":
                        req_type = "memory_extract"
                    elif m.get("consolidation_status") == "pending":
                        req_type = "consolidation"
                    elif m.get("image_refine_status") == "processing":
                        req_type = "image_refine"
                    elif m.get("choices_status") == "processing":
                        req_type = "choices"
                    elif m.get("memory_status") == "processing":
                        req_type = "memory_extract"
                    elif m.get("consolidation_status") == "processing":
                        req_type = "consolidation"
                    elif m.get("role") == "user":
                        req_type = "user"

                if m and req_type == "user":
                    # 「合図した会話以外は受け取れない」制限は廃止（/api/pending のコメント参照）。
                    # 未応答の発言を宛先不明で永久にブロックしてしまうため。
                    m["status"] = "processing"
                    m["claimed_ts"] = now()
                    claimed_at[m["id"]] = time.time()
                    save(s)
                elif m and req_type == "image_refine":
                    m["image_refine_status"] = "processing"
                    m["image_refine_claimed_ts"] = now()
                    m["claimed_ts"] = now()
                    claimed_at[f"{m['id']}:image_refine"] = time.time()
                    save(s)
                elif m and req_type == "choices":
                    m["choices_status"] = "processing"
                    m["choices_claimed_ts"] = now()
                    if m.get("role") != "user":
                        m["claimed_ts"] = now()
                    claimed_at[f"{m['id']}:choices"] = time.time()
                    save(s)
                elif m and req_type == "consolidation":
                    m["consolidation_status"] = "processing"
                    m["consolidation_claimed_ts"] = now()
                    m["claimed_ts"] = now()
                    claimed_at[f"{m['id']}:consolidation"] = time.time()
                    save(s)
                elif m and req_type == "memory_extract":
                    m["memory_status"] = "processing"
                    m["memory_claimed_ts"] = now()
                    m["claimed_ts"] = now()
                    claimed_at[f"{m['id']}:memory"] = time.time()
                    save(s)

                res = {"ok": True}
                if d.get("context") and m:
                    task_type = req_type if req_type != "user" else None
                    if req_type == "user":
                        ctx = build_context(s, m)
                    elif req_type == "image_refine":
                        ctx = build_image_refine_context(s, m, base_prompt=m.get("image_refine_prompt"))
                    elif req_type == "choices":
                        if m.get("role") == "user":
                            ctx = build_parallel_choices_context(s, m)
                        else:
                            ctype = m.get("choices_type") or d.get("choices_type") or "normal"
                            if ctype == "erotic":
                                ctx = build_choices_context(s, m)
                            else:
                                ctx = build_normal_choices_context(s, m)
                    elif req_type == "consolidation":
                        ctx = build_consolidation_context(s, m)
                    elif req_type == "memory_extract":
                        ctx = build_memory_extract_context(s, m)
                    else:
                        if m.get("role") == "user":
                            ctx = build_context(s, m)
                        elif m.get("image_refine_status") in ("pending", "processing"):
                            ctx = build_image_refine_context(s, m, base_prompt=m.get("image_refine_prompt"))
                            task_type = "image_refine"
                        elif m.get("choices_status") in ("pending", "processing"):
                            if m.get("role") == "user":
                                ctx = build_parallel_choices_context(s, m)
                            else:
                                ctype = m.get("choices_type") or "normal"
                                ctx = build_choices_context(s, m) if ctype == "erotic" else build_normal_choices_context(s, m)
                            task_type = "choices"
                        elif m.get("consolidation_status") in ("pending", "processing"):
                            ctx = build_consolidation_context(s, m)
                            task_type = "consolidation"
                        elif m.get("memory_status") in ("pending", "processing"):
                            ctx = build_memory_extract_context(s, m)
                            task_type = "memory_extract"
                        else:
                            ctx = build_context(s, m)
                    res["context"] = with_render(s, m, ctx, task_type, d.get("ide") or "", d.get("note") or "")
                return self.send_json(res)
            if p == "/api/regen":
                m = find_msg(s, d.get("id"))
                if m and m.get("image"):
                    mid = m.get("id")
                    img_meta = load_images_data(s["id"], readonly=True).get(mid, {}) if mid else {}
                    if img_meta:
                        for k, v in img_meta.items():
                            if (k not in m["image"] or m["image"][k] is None) and v is not None:
                                m["image"][k] = copy.deepcopy(v)
                    old_prompt = m["image"].get("prompt") or img_meta.get("prompt", "")
                    m["image"]["seed"] = d.get("seed") or random.randint(0, 2**32 - 1)
                    new_p = d.get("prompt")
                    if new_p is not None:
                        if new_p != old_prompt:
                            # プロンプトを実際に書き換えたときだけ manual（単なるシード変更は自動扱いのまま）
                            m["image"]["prev_prompt"] = old_prompt
                            m["image"]["manual"] = True
                        m["image"]["prompt"] = new_p
                    m["image"]["refined_prompt"] = None
                    m["image"]["refined"] = None
                    m.pop("image_refine_status", None)
                    m.pop("image_refine_mode", None)
                    m.pop("image_refine_prompt", None)
                    m.pop("image_refine_error", None)
                    m["image"]["status"] = "queued"
                    m["image"].pop("error", None)
                    queued_images.append((s["id"], m["id"]))
                    save(s)
                    image_event.set()
                return self.send_json({"ok": True})
            if p == "/api/message/refine-image":
                m = find_msg(s, d.get("id"))
                if not m:
                    return self.send_json({"error": "no such message"}, 404)
                if not m.get("image"):
                    return self.send_json({"error": "message has no image"}, 400)
                m["image_refine_mode"] = "card"
                m["image_refine_status"] = "pending"
                m["image_refine_ts"] = now()
                m.pop("image_refine_claimed_ts", None)
                m.pop("image_refine_prompt", None)
                m.pop("image_refine_error", None)
                if isinstance(m.get("image"), dict):
                    m["image"]["refined_prompt"] = None
                save(s)
                poke_event.set()
                return self.send_json({"ok": True})
            if p == "/api/prompt/refine":
                m = find_msg(s, d.get("id"))
                if not m:
                    return self.send_json({"error": "no such message"}, 404)
                if not m.get("image"):
                    return self.send_json({"error": "message has no image"}, 400)
                m["image_refine_mode"] = "modal"
                m["image_refine_status"] = "pending"
                m["image_refine_ts"] = now()
                m["image_refine_prompt"] = d.get("prompt", "")
                m.pop("image_refine_claimed_ts", None)
                m.pop("image_refine_error", None)
                if isinstance(m.get("image"), dict):
                    m["image"]["refined_prompt"] = None
                save(s)
                poke_event.set()
                return self.send_json({"ok": True})
            if p == "/api/pick":
                m = find_msg(s, d.get("id"))
                if m and m.get("image"):
                    m["image"]["file"] = d.get("file")
                    save(s)
                return self.send_json({"ok": True})
            if p == "/api/delete":
                target = find_msg(s, d.get("id"))
                if not target:
                    return self.send_json({"error": "no such message"}, 404)
                backup(s, "delete")
                target_idx = s["messages"].index(target)
                old_total = s["setting"].get("total_turns") or 0
                deleted_turn = calc_turn_idx(s["messages"], target_idx, old_total)
                # 削除した位置より後にユーザー発言が残っているか（途中削除か末尾削除か）
                later_user = any(m.get("role") == "user" for m in s["messages"][target_idx + 1:])
                rv = plan_revert(s, target_idx - 1)
                s["messages"] = [m for m in s["messages"] if m["id"] != d.get("id")]
                memory_store.delete_by_mid(sid, target["id"])
                if later_user:
                    # 途中削除: 以降のターン番号を詰める（delete_after_turn で以降の記憶を巻き込まない）
                    if target.get("role") == "user":
                        memory_store.shift_turn_indices(sid, deleted_turn, -1)
                        s["setting"]["total_turns"] = max(0, old_total - 1)
                else:
                    new_total = calc_turn_idx(s["messages"], len(s["messages"]) - 1, old_total) if s["messages"] else 0
                    mem_turn = max(0, new_total - 1) if (s["messages"] and s["messages"][-1].get("role") == "user") else new_total
                    s["setting"]["total_turns"] = new_total
                    memory_store.delete_after_turn(sid, mem_turn)
                restore_latest_snapshot(s)
                save(s)
                return self.send_json({"ok": True, "revert": rv})
            if p == "/api/chars/upsert":
                c = d.get("char") if isinstance(d.get("char"), dict) else {}
                name = (c.get("name") or "").strip() if isinstance(c.get("name"), str) else ""
                tags = ", ".join(split_tags(c.get("tags") if isinstance(c.get("tags"), str) else ""))
                if not name or not tags:
                    return self.send_json({"error": "名前とタグが必要です"}, 400)
                chars = s["setting"].setdefault("chars", [])
                cur = next((x for x in chars if c.get("id") and x.get("id") == c["id"]), None)
                same = find_char(chars, name)
                if same and same is not cur:
                    return self.send_json({"error": f"「{name}」はもう登録されています"}, 400)
                if not cur:
                    cur = {"id": new_id()}
                    chars.append(cur)
                cur.update(name=name, tags=tags, locked=bool(c.get("locked")),
                           source=c.get("source") if c.get("source") in ("user", "preset") else "user")
                save(s)
                return self.send_json({"ok": True, "char": cur})
            if p == "/api/chars/delete":
                chars = s["setting"].get("chars") or []
                if not any(x.get("id") == d.get("id") for x in chars):
                    return self.send_json({"error": "no such character"}, 404)
                backup(s, "chardelete")
                s["setting"]["chars"] = [x for x in chars if x.get("id") != d.get("id")]
                save(s)
                return self.send_json({"ok": True})
            if p == "/api/setting":
                new = d.get("setting", s["setting"])
                if not isinstance(new, dict):
                    return self.send_json({"error": "setting must be an object"}, 400)
                # 固定キャラ（AI が応答で足す）と担当IDE（専用の操作で変える）はフォームから上書きさせない
                keep = {k: s["setting"].get(k, v) for k, v in NO_IDE.items()}
                keep["chars"] = s["setting"].get("chars") or []
                keep["total_turns"] = s["setting"].get("total_turns", 0)
                keep["rotation_count"] = s["setting"].get("rotation_count", 0)
                keep["last_rotation_turn"] = s["setting"].get("last_rotation_turn", 0)
                keep["turns_since_rotation"] = s["setting"].get("turns_since_rotation", 0)
                keep["rotation"] = s["setting"].get("rotation")
                keep["rotation_skip_until"] = s["setting"].get("rotation_skip_until", 0)
                keep["rotation_reserve"] = s["setting"].get("rotation_reserve")
                s["setting"] = merge_defaults(dict(new, **keep))
                for _k in ("rotation", "rotation_skip_until", "rotation_reserve"):
                    if s["setting"].get(_k) is None:
                        s["setting"].pop(_k, None)
                if s["setting"].get("elapsed_time") and isinstance(s["setting"]["elapsed_time"], dict):
                    s["setting"]["elapsed_time"]["period"] = normalize_period(s["setting"]["elapsed_time"].get("period"))
                # 会話開始前（user 発言なし）であれば初期値も追従更新
                if not any(m.get("role") == "user" for m in s.get("messages", [])):
                    s["setting"]["initial_elapsed_time"] = copy.deepcopy(s["setting"]["elapsed_time"])
                    s["setting"]["initial_memo"] = s["setting"].get("memo", "")
                sync_agents(s)  # オンなら書き出し、オフにしたなら前に書いたものを片付ける
                save(s)
                a = agents_state.get(sid)
                return self.send_json({"ok": True, "agents": a})
            if p == "/api/reset":
                backup(s, "reset")
                s["messages"] = []
                s["setting"]["memo"] = s["setting"].get("initial_memo", "")
                s["setting"]["turns_in_period"] = 0
                s["setting"]["total_turns"] = 0
                s["setting"]["rotation_count"] = 0
                s["setting"]["last_rotation_turn"] = 0
                s["setting"]["turns_since_rotation"] = 0
                s["setting"].pop("rotation_notice", None)
                s["setting"].pop("rotation", None)
                s["setting"].pop("rotation_reserve", None)
                s["setting"]["rotation_skip_until"] = 0
                if s["setting"].get("initial_elapsed_time"):
                    s["setting"]["elapsed_time"] = copy.deepcopy(s["setting"]["initial_elapsed_time"])
                else:
                    s["setting"]["elapsed_time"] = copy.deepcopy(DEFAULT_SETTING["elapsed_time"])
                memory_store.clear_auto_memories(sid)
                fm = s["setting"].get("first_message")
                if fm:
                    s["messages"].append({
                        "id": new_id(),
                        "role": "assistant",
                        "text": fm,
                        "ts": now(),
                        "elapsed_time": copy.deepcopy(s["setting"]["elapsed_time"]),
                        "memo": s["setting"].get("memo", "")
                    })
                save(s)
                return self.send_json({"ok": True})
        self.send_error(404)


def already_running():
    """同じ data/ を使うサーバーがもう動いていればその番号を返す（番号が違っても outbox を取り合うので2台目は起動しない）"""
    last = read_json(RUNNING, {})
    ports = [PORT, last.get("port") if isinstance(last, dict) else None]
    for port in dict.fromkeys(p for p in ports if isinstance(p, int)):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=1.5).read()
            return port
        except Exception:
            pass
    return None


def main():
    if os.name == "nt":
        os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except AttributeError:
        pass
    for d in (SESS_DIR, RAG_DIR, TRASH, SENT, DONE, CHOICES_DIR, STATES_DIR, IMAGES_DIR):
        os.makedirs(d, exist_ok=True)
    migrate_all_sessions()
    cleanup_done_dir()
    get_vapid()
    port = already_running()
    if port:
        print(f"RP Image Chat はもう起動しています: http://127.0.0.1:{port}/ （2台目は起動しません）")
        print(f"AI側は python wait_input.py で待機してください（つなぎ先 {port}）")
        sys.exit(1)
    write_json(RUNNING, {"port": PORT, "pid": os.getpid(), "ts": now()})
    # 旧形式の data/session.json は取り込まずに退避だけする
    if os.path.exists(LEGACY):
        shutil.move(LEGACY, os.path.join(DATA, f"session_legacy_{int(time.time())}.json"))
        print("[init] 旧 session.json を退避しました")
    if not list_ids():
        create_session({"title": "チャンネル1", "ide_auto": True})
    threading.Thread(target=memory_store.warmup, daemon=True).start()
    threading.Thread(target=outbox_worker, daemon=True).start()
    threading.Thread(target=rotation_worker, daemon=True).start()
    threading.Thread(target=memory_worker, daemon=True).start()
    threading.Thread(target=image_worker, daemon=True).start()
    threading.Thread(target=poke_worker, daemon=True).start()
    threading.Thread(target=ide_auto_worker, daemon=True).start()
    host = bind_host()
    setup_access(host)
    setup_phone(host)
    if phone["url"]:
        print("=" * 60)
        print("  RP Image Chat (モバイル・LANモード)")
        print("=" * 60)
        print(f"  スマホ用 URL : {phone['url']}")
        print(f"  PC 用 URL    : http://127.0.0.1:{PORT}/")
        print("\n  同一 Wi-Fi のスマホのカメラで下記の QR コードを読み取ると接続できます:\n")
        try:
            print(qr.terminal(phone["url"]))
        except Exception as e:
            print(f"  [QRコード表示エラー: {e}]")
        print(f"\n  ※ PC のブラウザ（http://127.0.0.1:{PORT}/）の「設定」→「スマホで開く」でも")
        print("    QR コードを表示できます。")
        print("=" * 60 + "\n")
    elif HOST == "lan":
        print("=" * 60)
        print("  RP Image Chat (モバイル・LANモード)")
        print("=" * 60)
        print("  [!] Wi-Fi / LAN の IP アドレスが見つかりませんでした。")
        print("      スマホと同じ Wi-Fi に接続されているか確認してください。")
        print(f"  PC 用 URL    : http://127.0.0.1:{PORT}/")
        print("=" * 60 + "\n")
    else:
        print(f"RP Image Chat: http://{host}:{PORT}/")
    if host != "127.0.0.1" and host not in ("0.0.0.0", "::"):
        # LANモードでも PC 上の wait_input.py（127.0.0.1 宛て）が届くように、ローカルでも待ち受ける
        local = ThreadingHTTPServer(("127.0.0.1", PORT), H)
        threading.Thread(target=local.serve_forever, daemon=True).start()
    ThreadingHTTPServer((host, PORT), H).serve_forever()


if __name__ == "__main__":
    main()

