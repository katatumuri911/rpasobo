"""
チャットに新しい入力が来たことを、Antigravity の会話に気づかせる合図（Windows 専用、標準ライブラリのみ）。中身は送らない。

■ 会話IDに直接送る（send_to_conversation、ふだんはこちら）
  Antigravity / Antigravity IDE の言語サーバー（language_server*.exe）に、ほかの発言と同じ
  SendUserCascadeMessage で半角スペースを1つ送る。ウィンドウもフォーカスも使わず、指定した会話だけに届く。
  1. 動いている言語サーバーのうち、その会話を持っているものを探す（コマンドラインの csrf トークンと待ち受けポート）
  2. 会話が待機中（IDLE）のときだけ送る。応答中なら送らずに「あとで」と返す（途中で割り込まないため）
  3. 前回のユーザー入力と同じ設定（cascadeConfig）を付けて送る。付けないと受け付けたまま捨てられることがある
  4. 会話が受け取った（最後のユーザー入力の時刻が変わった）ことを確かめる

■ ウィンドウにキーを打つ（poke、予備。RPCHAT_POKE_MODE=window のときだけ）
  1. 会話のワークスペース名をタイトルに持つ Antigravity IDE のウィンドウを探す
  2. いま前面のウィンドウを覚えてから、そのウィンドウを前面に出す（前面になったことを確かめる）
  3. Ctrl+L（エージェントのチャット欄へ移動）→ 半角スペース → Enter
  4. 元のウィンドウを前面に戻す
  途中で前面が別のウィンドウに変わったら（ユーザーが触ったら）そこでやめる。
  ウィンドウで開いている会話に打つので、担当の会話とは限らない。Antigravity（IDE でないほう）の会話には届かない。

■ 新しい会話を作る（new_conversation）
  Antigravity（IDE でないほう）の言語サーバーに StartCascade。フォルダと同じプロジェクトがあればその中に作る。何も送らない。

■ 会話の作業フォルダを付け替える（move_workspaces）
  Antigravity を閉じ、会話のデータ（.db）にある作業フォルダとプロジェクトを書き換えて、起動し直す。履歴はそのまま。

  python ide_poke.py --status <会話ID>       # その会話を持つ言語サーバーと状態を見る（送らない）
  python ide_poke.py --project <フォルダ>     # そのフォルダの Antigravity プロジェクトを探すだけ（作らない）
  python ide_poke.py --list                 # Antigravity IDE のウィンドウ一覧（キーは送らない）
  python ide_poke.py --find <ワークスペース名>  # 合図を送る先のウィンドウを探すだけ（キーは送らない）
"""
import copy, ctypes, datetime, json, os, re, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from ctypes import wintypes

EXE = "antigravity ide.exe"
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK_CONTROL, VK_RETURN, VK_L = 0x11, 0x0D, 0x4C
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
INPUT_KEYBOARD = 1
SW_RESTORE = 9


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # INPUT の大きさを正しくするためだけに使う
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _exe(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def ide_windows():
    """Antigravity IDE のウィンドウ [(hwnd, タイトル)]"""
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _title(hwnd)
            if "Antigravity IDE" in t and os.path.basename(_exe(hwnd)).lower() == EXE:
                out.append((hwnd, t))
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(workspaces):
    """ワークスペース名（フォルダ名）のどれかで開いているウィンドウ。タイトルは「<ワークスペース> - Antigravity IDE - <ファイル>」"""
    names = [os.path.basename(w.rstrip("/\\")).lower() for w in workspaces if w]
    for hwnd, t in ide_windows():
        if t.split(" - ")[0].strip().lower() in names:
            return hwnd, t
    return None, ""


def _key(vk=0, up=False, char=None):
    i = INPUT(type=INPUT_KEYBOARD)
    if char is not None:  # VK_PACKET で文字そのものを送る（IME が全角に変えないように）
        i.ki = KEYBDINPUT(0, ord(char), KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
    else:
        i.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return i


def _send(*keys):
    arr = (INPUT * len(keys))(*keys)
    if user32.SendInput(len(keys), arr, ctypes.sizeof(INPUT)) != len(keys):
        raise OSError(f"SendInput に失敗 ({ctypes.get_last_error()})")


def _to_front(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True
    # 裏で動いているプロセスは前面を奪えないので、前面のスレッドに入力を一時的につないでから頼む
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and user32.AttachThreadInput(me, other, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, other, False)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def poke(workspaces, text=" "):
    """合図を送る。(成功したか, 説明) を返す"""
    hwnd, title = find_window(workspaces)
    if not hwnd:
        return False, "Antigravity IDE のウィンドウが見つからない（ワークスペース: " + ", ".join(workspaces or ["なし"]) + "）"
    prev = user32.GetForegroundWindow()
    if not _to_front(hwnd):
        return False, f"ウィンドウを前面に出せなかった: {title}"

    def still():
        if user32.GetForegroundWindow() != hwnd:
            raise InterruptedError("途中で前面のウィンドウが変わったのでやめた")
    try:
        time.sleep(0.25)
        still()
        _send(_key(VK_CONTROL), _key(VK_L), _key(VK_L, up=True), _key(VK_CONTROL, up=True))
        time.sleep(0.5)
        still()
        for ch in text:
            _send(_key(char=ch), _key(char=ch, up=True))
        time.sleep(0.15)
        still()
        _send(_key(VK_RETURN), _key(VK_RETURN, up=True))
        time.sleep(0.3)
    except (InterruptedError, OSError) as e:
        return False, f"{e}: {title}"
    finally:
        if prev and prev != hwnd and user32.IsWindow(prev):
            user32.SetForegroundWindow(prev)
    return True, f"送った: {title}"


# ---------------------------------------------------------------- 会話IDに直接送る
SVC = "/exa.language_server_pb.LanguageServerService/"
IDLE = "CASCADE_RUN_STATUS_IDLE"
CONV_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "conversations")
_servers = {}  # 会話ID -> (port, token)。見つけた言語サーバーを覚えておく（探すのに1〜2秒かかる）
_servers_lock = threading.Lock()
_hub = None  # (port, token)


def _call(port, tok, method, body, timeout=15):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{SVC}{method}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-codeium-csrf-token": tok,
                                          "Connect-Protocol-Version": "1"})
    try:
        return 200, json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


_ls_cache = (0.0, None)
_ls_lock = threading.Lock()


def _language_servers(ttl=2.0):
    """動いている言語サーバー [(token, [待ち受けポート])] を一括クエリ＆短期キャッシュで高速取得"""
    global _ls_cache
    now_t = time.time()
    if _ls_cache[1] is not None and now_t - _ls_cache[0] < ttl:
        return _ls_cache[1]

    with _ls_lock:
        now_t = time.time()
        if _ls_cache[1] is not None and now_t - _ls_cache[0] < ttl:
            return _ls_cache[1]

        ps = (
            "$ports = @{}; "
            "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object { "
            "  $p = [int]$_.OwningProcess; "
            "  if (-not $ports.ContainsKey($p)) { $ports[$p] = @() }; "
            "  $ports[$p] += [int]$_.LocalPort "
            "}; "
            "Get-CimInstance Win32_Process -Filter \"Name like 'language_server%'\" | ForEach-Object { "
            "  $id = [int]$_.ProcessId; "
            "  [pscustomobject]@{cmd=$_.CommandLine; ports=($ports[$id] | Where-Object { $_ -ne $null })} "
            "} | ConvertTo-Json -Compress -Depth 3"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=30, creationflags=0x08000000
            ).stdout
            rows = json.loads(out or "[]")
        except Exception:
            rows = []
        res = []
        for r in (rows if isinstance(rows, list) else [rows]):
            if not isinstance(r, dict):
                continue
            m = re.search(r"--csrf_token(?:=|\s+)(\S+)", r.get("cmd") or "")
            if m:
                p_list = r.get("ports") or []
                if isinstance(p_list, int):
                    p_list = [p_list]
                res.append((m.group(1), list(dict.fromkeys(int(p) for p in p_list))))
        _ls_cache = (time.time(), res)
        return res


def _summary(cid, port, tok):
    code, d = _call(port, tok, "GetAllCascadeTrajectories", {}, timeout=5)
    return (d.get("trajectorySummaries") or {}).get(cid) if code == 200 else None


def locate(cid):
    """その会話を持っている言語サーバー。(port, token, 会話の概要) か (None, None, None)"""
    global _hub
    with _servers_lock:
        server_entry = _servers.get(cid)

    checked_ok = set()  # HTTP 200 で応答し、この cid を持っていないことが確定した (port, tok)
    checked_failed = set()  # 通信例外や HTTP エラー等で到達不能・認証失敗だった (port, tok)

    if server_entry:
        port, tok = server_entry
        try:
            s = _summary(cid, port, tok)
            if s is not None:  # 空辞書 {} でも会話が存在するため is not None で判定
                return port, tok, s
            # s is None: 会話が存在しないか、HTTPエラー/通信失敗
            code, d = _call(port, tok, "GetAllCascadeTrajectories", {}, timeout=5)
            if code == 200:
                # 正常応答したが cid を持っていない
                checked_ok.add((port, tok))
                with _servers_lock:
                    _servers.pop(cid, None)
            else:
                checked_failed.add((port, tok))
                with _servers_lock:
                    dead_keys = [k for k, v in _servers.items() if v == (port, tok)]
                    for k in dead_keys:
                        _servers.pop(k, None)
                if _hub == (port, tok):
                    _hub = None
        except (OSError, ValueError):
            # アプリ再起動等でポートが変わったかプロセス終了。このサーバーに紐づく会話を全削除
            checked_failed.add((port, tok))
            with _servers_lock:
                dead_keys = [k for k, v in _servers.items() if v == (port, tok)]
                for k in dead_keys:
                    _servers.pop(k, None)
            if _hub == (port, tok):
                _hub = None

    # 1. PowerShell (_language_servers) を呼ぶ前に、既知のサーバー (_hub や _servers) を先に試す
    # スレッド競合を避けるため _servers.values() のスナップショットから重複を除いて走査する
    with _servers_lock:
        servers_snapshot = list(_servers.values())
    known_candidates = list(dict.fromkeys(([(_hub[0], _hub[1])] if _hub and len(_hub) >= 2 else []) + servers_snapshot))

    for port, tok in known_candidates:
        if (port, tok) in checked_ok or (port, tok) in checked_failed:
            continue
        try:
            code, d = _call(port, tok, "GetAllCascadeTrajectories", {}, timeout=5)
        except (OSError, ValueError):
            code, d = 0, {}
        if code != 200:
            checked_failed.add((port, tok))
            with _servers_lock:
                dead_keys = [k for k, v in _servers.items() if v == (port, tok)]
                for k in dead_keys:
                    _servers.pop(k, None)
            if _hub == (port, tok):
                _hub = None
            continue
        checked_ok.add((port, tok))
        summaries = d.get("trajectorySummaries") or {}
        with _servers_lock:
            for k in summaries:
                _servers[k] = (port, tok)  # 取得できた全会話の所在を一括キャッシュ
        if cid in summaries:
            return port, tok, summaries[cid]

    # 2. 既知サーバーで見つからなければ、動いている言語サーバー一覧を取得して探す
    for tok, ports in _language_servers():
        if any((p, tok) in checked_ok for p in ports) or (ports and all((p, tok) in checked_failed for p in ports)):
            continue
        for port in ports:
            if (port, tok) in checked_ok:
                break
            if (port, tok) in checked_failed:
                continue
            try:
                code, d = _call(port, tok, "GetAllCascadeTrajectories", {}, timeout=5)
            except (OSError, ValueError):
                code, d = 0, {}
            if code != 200:
                checked_failed.add((port, tok))
                continue  # http で答えないポートや認証エラーポート
            checked_ok.add((port, tok))
            summaries = d.get("trajectorySummaries") or {}
            with _servers_lock:
                for k in summaries:
                    _servers[k] = (port, tok)  # 取得できた全会話の所在を一括キャッシュ
            if cid in summaries:
                return port, tok, summaries[cid]
            break  # この言語サーバーは持っていない

    # 3. 起動し直したばかりの Antigravity は開いた会話しか読み込んでいない。データがあれば読み込ませる
    hub = hub_server() if os.path.exists(os.path.join(CONV_DIR, cid + ".db")) else None
    if hub:
        try:
            if _call(hub[0], hub[1], "LoadTrajectory", {"cascadeId": cid}, timeout=60)[0] == 200:
                s = _summary(cid, *hub)
                if s is not None:
                    with _servers_lock:
                        _servers[cid] = hub
                    return hub[0], hub[1], s
        except (OSError, ValueError):
            pass
    return None, None, None


def _user_config(port, tok, cid, s):
    """その会話の最後のユーザー入力に付いていた設定（cascadeConfig）。なければ None"""
    if not s.get("lastUserInputTime"):
        return None
    code, d = _call(port, tok, "GetCascadeTrajectorySteps",
                    {"cascadeId": cid, "stepOffset": int(s.get("lastUserInputStepIndex") or 0)})
    cfg = None
    if code == 200:
        for x in d.get("steps") or []:
            if x.get("type") == "CORTEX_STEP_TYPE_USER_INPUT":
                cfg = (x.get("userInput") or {}).get("userConfig") or cfg
    return cfg


# server.py が選択肢専用会話のIDを入れる（累積）。選択肢会話の設定は「借りる元」にも
# 共通キャッシュ（last_cascade_config.json）の書き込み元にもしない（メイン会話の設定が汚れるため）
AVOID_BORROW = set()
_SKIP_TITLE = ("【選択肢】", "【旧】", "【失敗】")  # 名前で判るものは念のため除外（退避・旧世代の選択肢会話）


def _borrowed_config(port, tok, skip):
    """まだ一度も入力のない会話（新規）用に、同じ言語サーバーで最近ユーザーが入力した会話の設定を借りる"""
    code, d = _call(port, tok, "GetAllCascadeTrajectories", {}, timeout=5)
    if code != 200:
        return None
    rows = sorted((d.get("trajectorySummaries") or {}).items(), key=lambda kv: kv[1].get("lastUserInputTime") or "", reverse=True)
    for cid, s in rows[:5]:
        if cid == skip or cid in AVOID_BORROW or not s.get("lastUserInputTime"):
            continue
        if (s.get("summary") or "").startswith(_SKIP_TITLE):
            continue
        cfg = _user_config(port, tok, cid, s)
        if cfg:
            return cfg
    return None


CFG_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "last_cascade_config.json")
_disk_cfg_cache = None
_cid_cfg = {}  # cid -> cascadeConfig インメモリキャッシュ


def _cached_config(cfg=None):
    """最後に読めた設定を覚えておく（cfg を渡すと保存、渡さないと読む）。
    Antigravity を起動し直した直後は、言語サーバーに入力のある会話がまだ読み込まれておらず、新規会話に借りる設定がないので"""
    global _disk_cfg_cache
    try:
        if cfg:
            if cfg != _disk_cfg_cache:
                with open(CFG_CACHE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False)
                _disk_cfg_cache = cfg
            return cfg
        if _disk_cfg_cache is not None:
            return _disk_cfg_cache
        with open(CFG_CACHE, encoding="utf-8") as f:
            _disk_cfg_cache = json.load(f)
            return _disk_cfg_cache
    except (OSError, ValueError):
        return cfg


_models = {}  # port -> (読んだ時刻, [{model, label, left, reset}], 既定のモデル)


def _model_data(port, tok, fresh=False):
    """その言語サーバーで選べるモデルの一覧と既定のモデル。5分は覚えておく。読めなければ (None, None)"""
    t, rows, default = _models.get(port, (0, None, None))
    if fresh or time.time() - t > 300:
        code, d = _call(port, tok, "GetCascadeModelConfigData", {})
        if code != 200:
            return None, None
        rows = []
        for c in d.get("clientModelConfigs") or []:
            key = (c.get("modelOrAlias") or {}).get("model")
            if key:
                q = c.get("quotaInfo") or {}
                # 0 は省かれて届く（使い切ったモデルは resetTime だけになる）
                rows.append({"model": key, "label": c.get("label") or key,
                             "left": q.get("remainingFraction", 0.0 if q else None), "reset": q.get("resetTime") or ""})
        # 一覧の順は毎回変わるので、Antigravity の画面と同じ並び（clientModelSorts）にする
        order = [x for srt in d.get("clientModelSorts") or [] for g in srt.get("groups") or [] for x in g.get("modelLabels") or []]
        rows.sort(key=lambda r: order.index(r["label"]) if r["label"] in order else len(order))
        default = ((d.get("defaultOverrideModelConfig") or {}).get("modelOrAlias") or {}).get("model")
        _models[port] = (time.time(), rows, default)
    return rows, default


def models():
    """Antigravity で選べるモデル。({"models": [...], "default": キー}, None) か (None, 理由)"""
    hub = hub_server()
    if not hub:
        return None, "Antigravity の言語サーバーが見つからない（Antigravity が起動しているか確認）"
    rows, default = _model_data(*hub, fresh=True)
    if rows is None:
        return None, "モデルの一覧を読めなかった"
    return {"models": rows, "default": default}, None


def model_label(port, tok, key):
    rows, _ = _model_data(port, tok)
    return next((r["label"] for r in rows or [] if r["model"] == key), key)


def _resolve_model_key(port, tok, key_or_label):
    """表示名(label)が渡されても内部キー(model)に直す。一覧が読めなければそのまま返す"""
    if not isinstance(key_or_label, str) or not key_or_label:
        return ""
    rows, _ = _model_data(port, tok)
    if not rows:
        return key_or_label
    if any(r["model"] == key_or_label for r in rows):
        return key_or_label
    return next((r["model"] for r in rows if r["label"] == key_or_label), key_or_label)


def _set_cfg_model(cfg, model_key):
    """cfg の中でモデルを指す値をそろえる。既にあるキーだけ書き換える（oneof 衝突の回避）。
    planModel だけ書き換えると、借りてきた設定に残っている requestedModel / modelOrAlias が優先されて
    設定画面で選んだモデルが使われないので、存在するキーは全部そろえる"""
    if not isinstance(cfg, dict) or not model_key:
        return
    pc = cfg.get("plannerConfig")
    if not isinstance(pc, dict):
        pc = cfg["plannerConfig"] = {}
    pc["planModel"] = model_key
    for k in ("requestedModel", "modelOrAlias"):
        v = pc.get(k)
        if not isinstance(v, dict):
            continue
        inner = v.get("modelOrAlias")
        if isinstance(inner, dict):
            inner["model"] = model_key          # {modelOrAlias: {model: "..."}} 形
        elif isinstance(v.get("model"), str):
            v["model"] = model_key              # {model: "..."} 形


def _fix_model(port, tok, cfg):
    """設定のモデルが今は選べない（モデルの入れ替えで消えた）なら Antigravity の既定モデルに替える。
    古い設定をそのまま送ると「unknown model key …: model not found」で会話がすぐエラーになる。替えたら元のモデル名を返す"""
    pc = cfg.get("plannerConfig") or {}
    cur = pc.get("planModel")
    if not isinstance(cur, str) or not cur:
        return None
    resolved = _resolve_model_key(port, tok, cur)
    if resolved != cur:
        _set_cfg_model(cfg, resolved)        # 表示名で保存されていても内部キーに直して送る
        cur = resolved
    rows, default = _model_data(port, tok)
    if not rows or cur in {r["model"] for r in rows} or not default:
        return None
    _set_cfg_model(cfg, default)             # planModel だけでなく requestedModel なども替える
    return cur


def send_to_conversation(cid, text=" ", model="", media=None):
    """会話に text を送る。model（モデルのキー）を渡すとそのモデルで、空なら会話の最後の入力と同じモデルで。
    media: 一緒にチャット欄に貼る画像 [{"mimeType", "description", "inlineData"(base64)}]（画面で貼り付けたときと同じ形）。
    (成功したか, 説明, あとでやり直すべきか) を返す"""
    port, tok, s = locate(cid)
    if not port:
        return False, "この会話を開いている Antigravity が見つからない（Antigravity が起動しているか確認）", False
    title = s.get("summary") or cid[:8]
    status = s.get("status")
    # 新規作成直後の会話は status が None または UNSPECIFIED になるため待機中(IDLE)として扱う
    if status not in (IDLE, "IDLE", None, "", "CASCADE_RUN_STATUS_UNSPECIFIED"):
        return False, f"会話が応答中なので待つ（{str(status).replace('CASCADE_RUN_STATUS_', '')}）: {title}", True
    cached_cfg = _cid_cfg.get(cid)
    cfg = copy.deepcopy(cached_cfg) if isinstance(cached_cfg, dict) and cached_cfg else None

    if not cfg:
        cfg = _user_config(port, tok, cid, s) or _borrowed_config(port, tok, cid)
        if cid in AVOID_BORROW:
            cfg = cfg or _cached_config()      # 選択肢会話の設定はディスクの共通キャッシュに書かない
        else:
            cfg = _cached_config(cfg) if cfg else _cached_config()
        if cfg:
            _cid_cfg[cid] = copy.deepcopy(cfg)   # 上書き前の cfg を覚える（モデルを空欄に戻せるように）
            cfg = copy.deepcopy(cfg)  # グローバルキャッシュ(_disk_cfg_cache)の直接書き換えを防止
    if not cfg:
        return False, f"送るときの設定（モデルなど）が読めない: {title}", False
    if model:
        _set_cfg_model(cfg, _resolve_model_key(port, tok, model))
    old = _fix_model(port, tok, cfg)
    active = (cfg.get("plannerConfig") or {}).get("planModel") or ""
    if old:
        note = f"（{model_label(port, tok, old)} は選べないので {model_label(port, tok, active) or active} で送った）"
    else:
        note = f"（{model_label(port, tok, active)}）" if (model and active) else ""
        row = next((r for r in _model_data(port, tok)[0] or [] if r["model"] == active), None)
        if row and row["left"] == 0:
            note += f"（{row['label']} は利用枠を使い切っているので、会話がエラーになるかもしれない）"

    if os.environ.get("RPCHAT_MODEL_VERIFY") == "1":
        pc = cfg.get("plannerConfig") or {}
        print(f"[poke][cfg] {cid[:8]} plannerConfig keys={sorted(pc)} planModel={pc.get('planModel')}")
    before = s.get("lastUserInputTime")
    # deliveryStrategy は付けない（WHEN_IDLE だと待機中の会話では受け付けたまま捨てられた）
    body = {"cascadeId": cid, "items": [{"text": text}], "cascadeConfig": cfg}
    if media:
        body["media"] = media
    code, d = _call(port, tok, "SendUserCascadeMessage", body, timeout=60 if media else 15)
    if code != 200:
        _cid_cfg.pop(cid, None)
        return False, f"送れなかった（{code} {d}）: {title}", False
    for i in range(10):
        try:
            s2 = _summary(cid, port, tok)
        except Exception:
            s2 = None
        s2_status = s2.get("status") if s2 is not None else None
        new_input_time = s2.get("lastUserInputTime") if s2 is not None else None
        if s2 is not None and (
            (new_input_time and new_input_time != before)
            or (s2_status and s2_status not in (IDLE, "CASCADE_RUN_STATUS_UNSPECIFIED"))
        ):
            if os.environ.get("RPCHAT_MODEL_VERIFY") == "1":
                try:
                    got = ((_user_config(port, tok, cid, s2) or {}).get("plannerConfig") or {}).get("planModel")
                    print(f"[poke][model] {cid[:8]} want={active} got={got}")
                except Exception:
                    pass
            return True, f"送った{note}: {title}", False
        time.sleep(0.03 if i == 0 else 0.1)
    return False, f"送ったが会話が動かない: {title}", False


# ---------------------------------------------------------------- 新しい会話を作る
SUMMARIES_DB = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "conversation_summaries.db")
APP_STORAGE = os.path.join(os.environ.get("APPDATA", ""), "Antigravity", "app_storage.json")
PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "config", "projects")
_hub = None  # (port, token)


def folder_key(uri_or_path):
    """file:///d%3A/x/ も d:\\x も同じ "d:/x" にする（比べるため）"""
    p = urllib.parse.unquote(uri_or_path or "")
    if p.lower().startswith("file:///"):
        p = p[8:]
    return p.replace("\\", "/").rstrip("/").lower()


def folder_uri(path):
    """D:\\x\\y -> file:///d%3A/x/y （Antigravity と同じ書き方）"""
    p = os.path.abspath(path).replace("\\", "/")
    return "file:///" + urllib.parse.quote(p[0].lower() + p[1:], safe="/")


def hub_server():
    """Antigravity（IDE でないほう）の言語サーバー。プロジェクトを持っているのはこちらだけ"""
    global _hub
    if _hub:
        try:
            if _call(_hub[0], _hub[1], "ReadProjects", {}, timeout=3)[0] == 200:
                return _hub
        except (OSError, ValueError):
            pass
        _hub = None

    # 既知サーバーを先に試して PowerShell の起動を抑止する
    with _servers_lock:
        servers_snapshot = list(_servers.values())
    for port, tok in dict.fromkeys(servers_snapshot):
        try:
            if _call(port, tok, "ReadProjects", {}, timeout=3)[0] == 200:
                _hub = (port, tok)
                return _hub
        except (OSError, ValueError):
            continue

    for tok, ports in _language_servers():
        for port in ports:
            try:
                code, _ = _call(port, tok, "ReadProjects", {}, timeout=3)
            except (OSError, ValueError):
                continue
            if code == 200:
                _hub = (port, tok)
                return _hub
    return None


def find_project(port, tok, folder):
    """そのフォルダだけを持つ Antigravity のプロジェクトID。なければ None"""
    import sqlite3
    ids = []
    try:  # プロジェクトは1件ずつ ~/.gemini/config/projects/<id>.json にある（会話のないプロジェクトもここで見つかる）
        ids += [n[:-5] for n in os.listdir(PROJECTS_DIR) if n.endswith(".json") and len(n) > 5]
    except OSError:
        pass
    try:
        with open(APP_STORAGE, encoding="utf-8") as f:
            ids.append(json.load(f).get("lastCreatedProjectId"))
    except (OSError, ValueError):
        pass
    try:
        db = sqlite3.connect("file:" + SUMMARIES_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=3)
        try:
            ids += [r[0] for r in db.execute("SELECT DISTINCT project_id FROM conversation_summaries")]
        finally:
            db.close()
    except sqlite3.Error:
        pass
    want = folder_key(folder)
    for pid in dict.fromkeys(i for i in ids if i and i != "outside-of-project"):
        code, d = _call(port, tok, "ReadProject", {"id": pid}, timeout=5)
        res = ((d.get("project") or {}).get("projectResources") or {}).get("resources") or [] if code == 200 else []
        uris = [r.get("folderUri") or (r.get("gitFolder") or {}).get("folderUri") for r in res]  # git のフォルダは gitFolder に入る
        if [folder_key(u) for u in uris] == [want]:
            return pid
    return None


def create_project(port, tok, folder):
    """そのフォルダだけのプロジェクトを作る（名前はフォルダ名。同じ名前があれば番号を付ける）。プロジェクトID か None。
    プロジェクトに入っていない会話は Antigravity の一覧に出ないので、新規会話の前に作る"""
    import uuid
    base = os.path.basename(os.path.normpath(folder)) or "project"
    for i in range(1, 10):
        pid = str(uuid.uuid4())  # ID は作る側が決める（決めないと ID の空のプロジェクトができる）
        code, _ = _call(port, tok, "CreateProject", {"project": {
            "id": pid, "name": base if i == 1 else f"{base} {i}",
            "projectResources": {"resources": [{"folderUri": folder_uri(folder)}]}}})
        if code == 409:  # 同じ名前のプロジェクトがある
            continue
        return pid if code == 200 and find_project(port, tok, folder) == pid else None
    return None


def new_conversation(folder):
    """Antigravity にそのフォルダの新しい会話を作る（何も送らない）。(会話ID, 説明) か (None, 失敗の説明)"""
    hub = hub_server()
    if not hub:
        return None, "Antigravity が見つからない（Antigravity を起動しておく）"
    port, tok = hub
    pid = find_project(port, tok, folder) or create_project(port, tok, folder)
    body = {"source": "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT", "trajectoryType": "CORTEX_TRAJECTORY_TYPE_CASCADE"}
    if pid:  # プロジェクトに入れると Antigravity の一覧でそのプロジェクトの下に出る
        body["projectEnvConfig"] = {"projectId": pid, "defaultProjectEnvironment": {}}
    else:
        body["workspaceUris"] = [folder_uri(folder)]
    code, d = _call(port, tok, "StartCascade", body)
    if code != 200 or not d.get("cascadeId"):
        return None, f"会話を作れなかった（{code} {d}）"
    with _servers_lock:
        _servers[d["cascadeId"]] = hub
    return d["cascadeId"], "作った" + ("" if pid else "（このフォルダのプロジェクトを作れなかったので、プロジェクトなし。Antigravity の一覧には出ない）")


# ---------------------------------------------------------------- 会話を途中から分岐する
FORK_TAG = re.compile(r"^【分岐[^】]*】")


def summary(cid):
    """会話の概要（conversation_summaries の行）。読めなければ None"""
    try:
        port, tok, s = locate(cid)
        return s if port else None
    except Exception:
        return None


def idle(cid):
    """その会話が待機中か。(bool, status文字列)"""
    try:
        port, tok, s = locate(cid)
        if not port:
            return False, "not found"
        st = s.get("status") or ""
        return st in (IDLE, "", "CASCADE_RUN_STATUS_UNSPECIFIED"), st
    except Exception as e:
        return False, str(e)


def last_reply(cid, after_step=0):
    """最後の返事（PLANNER_RESPONSE.response の全文）とエラー。
    progress() は 110 字に切り詰めるので検証用に別途用意する。
    after_step: この絶対ステップ番号以前の返事は無視する（テスト送信前の返事を拾わない）。"""
    try:
        port, tok, s = locate(cid)
        if not port:
            return None, None, "会話が見つからない"
        n = int(s.get("stepCount") or 0)
        start = max(0, n - 30)
        code, d = _call(port, tok, "GetCascadeTrajectorySteps",
                        {"cascadeId": cid, "stepOffset": start}, timeout=20)
    except Exception as e:
        return None, None, f"読めなかった（{e}）"
    if code != 200:
        return None, None, f"読めなかった（{code}）"
    text, err = "", ""
    for j, x in enumerate(d.get("steps") or []):
        if start + j <= int(after_step or 0):
            continue
        t = (x.get("type") or "").replace("CORTEX_STEP_TYPE_", "")
        if t == "PLANNER_RESPONSE":
            pr = x.get("plannerResponse") or {}
            if not (pr.get("toolCalls") or []):
                rr = pr.get("response") or ""
                if rr:
                    text = rr
        elif t == "ERROR_MESSAGE":
            e = (x.get("errorMessage") or {}).get("error") or {}
            err = e.get("shortError") or e.get("userErrorMessage") or "error"
    return text, err, "ok"


def turns(cid):
    """会話のユーザー発言の一覧（古い順）。(会話の概要, [{n, step, at, text, time}]) か (None, 説明)。
    at はその発言の直前のステップ番号で、ここで分岐するとその発言から言い直せる（最初の発言は分岐できないので None）"""
    port, tok, s = locate(cid)
    if not port:
        return None, "この会話を開いている Antigravity が見つからない（Antigravity が起動しているか確認）"
    code, d = _call(port, tok, "GetCascadeTrajectorySteps", {"cascadeId": cid, "stepOffset": 0}, timeout=120)
    if code != 200:
        return None, f"会話を読めなかった（{code} {d}）"
    out = []
    for i, x in enumerate(d.get("steps") or []):
        if x.get("type") != "CORTEX_STEP_TYPE_USER_INPUT":
            continue
        text = "".join(y.get("text", "") for y in (x.get("userInput") or {}).get("items") or [])
        out.append({"n": len(out) + 1, "step": i, "at": i - 1 if i > 0 else None, "text": text[:400],
                    "time": (x.get("metadata") or {}).get("createdAt", "")})
    return s, out


def _short(text, n=110):
    line = next((x.strip() for x in str(text or "").splitlines() if x.strip()), "")
    return line if len(line) <= n else line[:n - 1] + "…"


def _tool_line(tc):
    """ツール呼び出し1つを {tool, text, detail} にする（Antigravity の画面のツール行と同じ toolAction を使う）"""
    try:
        a = json.loads(tc.get("argumentsJson") or "{}")
    except ValueError:
        a = {}
    a = a if isinstance(a, dict) else {}
    detail = ""
    for k in ("CommandLine", "AbsolutePath", "TargetFile", "Url", "query", "Query", "SearchPath", "DirectoryPath", "TaskId"):
        if a.get(k):
            v = str(a[k])
            # パスは名前だけ（長いパスは画面に収まらない）
            detail = _short(v) if k in ("CommandLine", "Url", "query", "Query", "TaskId") else re.split(r"[\\/]", v.rstrip("\\/"))[-1]
            break
    return {"tool": tc.get("name") or "", "text": _short(a.get("toolAction") or a.get("toolSummary") or tc.get("name"), 80),
            "detail": detail}


def progress(cid, limit=10):
    """会話のいまのターン（最後のユーザー入力から後）で何をしているか。読むだけ。
    ({running, user_time, steps, items: [{kind, tool, text, detail, done}]}, None) か (None, 説明)。
    kind は tool（ツール）/ think（考えている）/ reply（返事を書いた）/ error"""
    port, tok, s = locate(cid)
    if not port:
        return None, "この会話を開いている Antigravity が見つからない"
    n = int(s.get("stepCount") or 0)
    first = int(s.get("lastUserInputStepIndex") or 0)
    start = max(first, n - 40)  # 1つのターンで何件も受け取る使い方だとターンが長いので、最近の分だけ
    code, d = _call(port, tok, "GetCascadeTrajectorySteps", {"cascadeId": cid, "stepOffset": start}, timeout=20)
    if code != 200:
        return None, f"会話を読めなかった（{code}）"
    items = []
    for x in d.get("steps") or []:
        t = (x.get("type") or "").replace("CORTEX_STEP_TYPE_", "")
        done = (x.get("status") or "").endswith("_DONE")
        if t == "PLANNER_RESPONSE":
            pr = x.get("plannerResponse") or {}
            calls = pr.get("toolCalls") or []
            for tc in calls:
                items.append(dict(_tool_line(tc), kind="tool", done=False))  # 終わったかは次の実行ステップで決める
            if not calls:
                if pr.get("response", "").strip():
                    items.append({"kind": "reply", "tool": "", "text": "返事を書いた", "detail": _short(pr["response"]), "done": True})
                elif not done:
                    heads = re.findall(r"\*\*(.+?)\*\*", pr.get("thinking") or "")
                    items.append({"kind": "think", "tool": "", "text": "考えている", "detail": _short(heads[-1] if heads else ""), "done": False})
        elif t == "ERROR_MESSAGE":
            e = (x.get("errorMessage") or {}).get("error") or {}
            items.append({"kind": "error", "tool": "", "text": "エラー", "detail": _short(e.get("shortError") or e.get("userErrorMessage")), "done": True})
        elif t not in ("USER_INPUT", "CHECKPOINT", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE"):
            # ツールを実行したステップ（RUN_COMMAND / VIEW_FILE / CODE_ACTION など）。まだ終わっていない最初のツール行に当てる
            tool = next((it for it in items if it["kind"] == "tool" and not it.get("ran")), None)
            if tool:
                tool["ran"], tool["done"] = True, done
    for it in items:
        it.pop("ran", None)
    running = s.get("status") == "CASCADE_RUN_STATUS_RUNNING"
    if not running:
        for it in items:
            it["done"] = True  # 止まった会話に「実行中」は残さない
    return {"running": running, "user_time": s.get("lastUserInputTime") or "", "steps": n, "items": items[-limit:]}, None


READ_TOOL_NAMES = {"view_file", "read_file", "open_file", "read_text_file", "view_file_content"}


def tool_usage_since(cid, after_step=None, limit=200):
    """after_step 以降のツール呼び出しを数える（read_file 検出用の軽量版）。
    戻り値: {"total", "read_file", "names", "ok"}。IDE に問い合わせできなければ ok=False。"""
    out = {"total": 0, "read_file": False, "names": [], "ok": False}
    try:
        port, tok, s = locate(cid)
        if not port:
            return out
        n = int(s.get("stepCount") or 0)
        if after_step is None:
            after_step = int(s.get("lastUserInputStepIndex") or 0)
        start = max(0, int(after_step or 0))
        truncated = n - start > limit
        if truncated:
            start = max(0, n - limit)
        code, d = _call(port, tok, "GetCascadeTrajectorySteps",
                        {"cascadeId": cid, "stepOffset": start}, timeout=20)
        if code != 200:
            return out
        names = []
        for x in d.get("steps") or []:
            t = (x.get("type") or "").replace("CORTEX_STEP_TYPE_", "")
            if t == "PLANNER_RESPONSE":
                for tc in (x.get("plannerResponse") or {}).get("toolCalls") or []:
                    nm = str(tc.get("name") or "")
                    if nm:
                        names.append(nm)
            elif t in ("VIEW_FILE", "READ_FILE", "OPEN_FILE"):
                names.append(t.lower())
        uniq = list(dict.fromkeys(names))
        out.update(
            total=len(uniq),
            names=uniq[:20],
            ok=True,
            truncated=truncated,
            read_file=any(nm.lower() in READ_TOOL_NAMES for nm in uniq),
        )
        return out
    except Exception:
        return out


def _ts(ts):
    """now() のローカル表記も Antigravity の UTC (末尾 Z) 表記も正確に POSIX 秒にする"""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z)?", ts or "")
    if not m:
        return None
    t = time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    sec = (datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc).timestamp()
           if m.group(3) else time.mktime(t))
    return sec + (float(m.group(2)) if m.group(2) else 0)


def errors_since(cid, since="", kinds=None, limit=200):
    """since 以降の content safety ブロックを含むエラーを抽出"""
    t0 = _ts(since) if since else None
    if not t0:
        return []
    try:
        port, tok, s = locate(cid)
        if not port:
            return []
        n = int(s.get("stepCount") or 0)
        code, d = _call(port, tok, "GetCascadeTrajectorySteps",
                        {"cascadeId": cid, "stepOffset": max(0, n - limit)}, timeout=10)
    except Exception:
        return []
    if code != 200:
        return []
    out = []
    for x in d.get("steps") or []:
        t = (x.get("type") or "").replace("CORTEX_STEP_TYPE_", "")
        at = (x.get("metadata") or {}).get("createdAt") or ""
        if t == "ERROR_MESSAGE":
            e = (x.get("errorMessage") or {}).get("error") or {}
            msg = e.get("shortError") or e.get("userErrorMessage") or ""
            kind = "filter" if ("content safety" in msg or "Prohibited Use" in msg) else "error"
            text = msg[:300]
        elif t == "PLANNER_RESPONSE":
            if (x.get("plannerResponse") or {}).get("stopReason") != "STOP_REASON_CONTENT_FILTER":
                continue
            kind, text = "filter", "STOP_REASON_CONTENT_FILTER"
        else:
            continue
        ep = _ts(at)
        if ep and ep < t0 - 3:
            continue
        out.append({"kind": kind, "text": text, "at": at})
    return [e for e in out if not kinds or e["kind"] in kinds]


def fork(cid, at=-1, title=None):
    """会話を at（ステップ番号。-1 は最後まで）で分岐し、名前を title にする。同じワークスペース・プロジェクトのまま。
    (新しい会話ID, 説明) か (None, 失敗の説明)"""
    port, tok, s = locate(cid)
    if not port:
        return None, "この会話を開いている Antigravity が見つからない（Antigravity が起動しているか確認）"
    if s.get("status") != IDLE:
        return None, "会話が応答中なので分岐できない。終わってからもう一度"
    code, d = _call(port, tok, "ForkConversation", {"sourceCascadeId": cid, "forkAtStepIndex": at,
                                                    "targetForkWorkspace": "FORK_TARGET_CURRENT_WORKSPACE"}, timeout=180)
    if code != 200 or not d.get("newCascadeId"):
        return None, f"分岐できなかった（{code} {d}）"
    new = d["newCascadeId"]
    with _servers_lock:
        _servers[new] = (port, tok)
    if title:
        _call(port, tok, "UpdateConversationAnnotations", {"cascadeId": new, "annotations": {"title": title}, "mergeAnnotations": True})
    return new, "分岐した"


def rename(cid, title):
    """会話の名前を変える（再起動は要らない）。(できたか, 説明)"""
    port, tok, s = locate(cid)
    if not port:
        return False, "この会話を開いている Antigravity が見つからない（Antigravity が起動しているか確認）"
    code, d = _call(port, tok, "UpdateConversationAnnotations",
                    {"cascadeId": cid, "annotations": {"title": title}, "mergeAnnotations": True})
    if code != 200:
        return False, f"名前を変えられなかった（{code} {d}）"
    return True, "名前を変えた"


def revert(cid, at, backup_dir):
    """会話をステップ at まで（at を含む）に戻す。後ろのステップは消える。会話IDは同じまま。
    conversationOnly なので、AI が書き換えたファイルは戻さない。戻す前に会話のデータを backup_dir に写しておく。
    overrideConfig（モデルの指定）がないと Antigravity は 500 を返し、会話にエラーのステップが付く。(できたか, 説明)"""
    port, tok, s = locate(cid)
    if not port:
        return False, "この会話を開いている Antigravity が見つからない（Antigravity が起動しているか確認）"
    if s.get("status") != IDLE:
        return False, "会話が応答中なので巻き戻せない"
    n = int(s.get("stepCount") or 0)
    if at < 0 or at >= n - 1:
        return False, "巻き戻すところがない"
    cfg = _user_config(port, tok, cid, s) or _cached_config()
    if not cfg:
        return False, "会話の設定（モデル）が読めないので巻き戻せない"
    import shutil
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for ext in ("", "-wal", "-shm"):
        src = os.path.join(CONV_DIR, cid + ".db" + ext)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, f"{cid}_{stamp}.db{ext}"))
    code, d = _call(port, tok, "RevertToCascadeStep",
                    {"cascadeId": cid, "stepIndex": at, "conversationOnly": True, "overrideConfig": cfg}, timeout=120)
    if code != 200:
        return False, f"巻き戻せなかった（{code} {d}）"
    return True, f"Antigravity の会話を {n} ステップから {at + 1} ステップに戻した"


def revert_when_idle(cid, at, backup_dir=None, timeout=45):
    """会話が IDLE になるのを待ってから指定ステップ at まで巻き戻す"""
    if at is None or at < 0:
        return False, "巻き戻すステップがない"
    if backup_dir is None:
        backup_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "trash", "ide_revert"
        )
    start = time.time()
    while time.time() - start < timeout:
        port, tok, s = locate(cid)
        if not port:
            return False, "この会話を開いている Antigravity が見つからない"
        st = s.get("status") or ""
        if st in (IDLE, "IDLE", "", "CASCADE_RUN_STATUS_UNSPECIFIED"):
            return revert(cid, at, backup_dir)
        time.sleep(0.4)
    return False, "IDLE待機タイムアウト"


# ---------------------------------------------------------------- 会話の作業フォルダを付け替える（Antigravity の再起動が要る）
# 会話の作業フォルダは conversations/<ID>.db の trajectory_metadata_blob（protobuf）にある:
#   1.1 = file:///d:/...（コロンはそのまま）, 7 = file:///d%3A/...（folder_uri と同じ）, 18 = プロジェクトID（プロジェクトなしなら欄ごとない）
# 動いている Antigravity は会話の情報をメモリに持っていて読み直さないので、閉じてから書き換えて起動し直す。
# 起動時に conversation_summaries は blob から作り直される（念のためこちらも書き換える）
CONV_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "conversations")


def _pb_varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def _pb_parse(b):
    i, out = 0, []
    while i < len(b):
        k, i = _pb_varint(b, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _pb_varint(b, i)
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        elif w == 2:
            n, i = _pb_varint(b, i)
            v, i = b[i:i + n], i + n
        else:
            raise ValueError(f"知らない protobuf の型 {w}")
        out.append((f, w, v))
    return out


def _pb_enc_varint(n):
    o = bytearray()
    while True:
        x, n = n & 0x7F, n >> 7
        if n:
            o.append(x | 0x80)
        else:
            o.append(x)
            return bytes(o)


def _pb_build(fields):
    o = bytearray()
    for f, w, v in fields:
        o += _pb_enc_varint(f << 3 | w)
        o += _pb_enc_varint(v) if w == 0 else (_pb_enc_varint(len(v)) + v if w == 2 else v)
    return bytes(o)


def _app_procs():
    """Antigravity（IDE でないほう）のプロセスと、hub の言語サーバー。([(pid, 親pid, exe)], hub の言語サーバーの数)"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='Antigravity.exe' or Name like 'language_server%'\" | ForEach-Object {"
          " [pscustomobject]@{pid=$_.ProcessId; ppid=$_.ParentProcessId; name=$_.Name; exe=$_.ExecutablePath;"
          " hub=($_.CommandLine -like '*subclient_type hub*')} } | ConvertTo-Json")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=30, creationflags=0x08000000).stdout
        rows = json.loads(out or "[]")
    except Exception:
        rows = []
    rows = rows if isinstance(rows, list) else [rows]
    app = [(r["pid"], r["ppid"], r.get("exe") or "") for r in rows if (r.get("name") or "").lower() == "antigravity.exe"]
    return app, sum(1 for r in rows if (r.get("name") or "").lower().startswith("language_server") and r.get("hub"))


def _rewrite_workspace(cid, folder, project, backup):
    """会話の .db の作業フォルダを書き換える（Antigravity を閉じてから）。backup(cid, 元の blob, 元の summaries の行) を先に呼ぶ"""
    import sqlite3
    path = os.path.join(CONV_DIR, cid + ".db")
    if not os.path.exists(path):
        raise FileNotFoundError(f"会話のデータがない: {path}")
    uri = folder_uri(folder)  # file:///d%3A/...
    raw = uri.replace("%3A", ":", 1)  # file:///d:/...
    db = sqlite3.connect(path, timeout=10)
    try:
        blob = db.execute("SELECT data FROM trajectory_metadata_blob").fetchone()[0]
        out = []
        for f, w, v in _pb_parse(blob):
            if f == 1 and w == 2:
                v = _pb_build([(ff, ww, raw.encode() if ff == 1 else vv) for ff, ww, vv in _pb_parse(v)])
            elif f == 7 and w == 2:
                v = uri.encode()
            elif f == 18:
                continue  # プロジェクトは下で付け直す
            out.append((f, w, v))
        if project:
            out.append((18, 2, project.encode()))
        sm = sqlite3.connect(SUMMARIES_DB, timeout=10)
        try:
            row = sm.execute("SELECT workspace_uris, project_id FROM conversation_summaries WHERE conversation_id = ?", (cid,)).fetchone()
            backup(cid, blob, row)
            db.execute("UPDATE trajectory_metadata_blob SET data = ?", (_pb_build(out),))
            db.commit()
            sm.execute("UPDATE conversation_summaries SET workspace_uris = ?, project_id = ? WHERE conversation_id = ?",
                       (json.dumps([uri]), project or "", cid))
            sm.commit()
        finally:
            sm.close()
    finally:
        db.close()


def busy_conversations():
    """Antigravity（IDE でないほう）で応答中の会話の名前。Antigravity が動いていなければ None"""
    hub = hub_server()
    if not hub:
        return None
    port, tok = hub
    code, d = _call(port, tok, "GetAllCascadeTrajectories", {}, timeout=10)
    loaded = (d.get("trajectorySummaries") or {}) if code == 200 else {}
    return [s.get("summary") or c[:8] for c, s in loaded.items() if s.get("status") != IDLE]


def move_workspaces(moves, force=False, backup=lambda cid, blob, row: None, log=print):
    """moves = {会話ID: フォルダ}。Antigravity を閉じて会話の作業フォルダを書き換え、起動し直す。
    応答中の会話があれば force でない限りやめる。({会話ID: (成功したか, 説明)}, 全体の説明) を返す"""
    global _hub
    busy = busy_conversations()
    if busy is None:
        return {}, "Antigravity が起動していない（起動しておくと、閉じて書き換えて起動し直すところまでやる）"
    port, tok = _hub
    if busy and not force:
        return {}, "応答中の会話がある: " + "、".join(busy)
    res = {}
    for cid in moves:
        if not os.path.exists(os.path.join(CONV_DIR, cid + ".db")):
            res[cid] = (False, "Antigravity の会話のデータが見つからない（Antigravity IDE の会話は移せない）")
    todo = {c: f for c, f in moves.items() if c not in res}
    if not todo:
        return res, "移せる会話がない"
    projects = {}
    for f in set(todo.values()):
        os.makedirs(f, exist_ok=True)
        # 同じフォルダのプロジェクトへ。なければ作る（プロジェクトに入っていない会話は Antigravity の一覧に出ない）
        projects[f] = find_project(port, tok, f) or create_project(port, tok, f)
    app, _ = _app_procs()
    pids = {p for p, _, _ in app}
    main = [(p, exe) for p, pp, exe in app if pp not in pids]
    if not main:
        for cid in todo:
            res[cid] = (False, "Antigravity のプロセスが見つからない")
        return res, "Antigravity のプロセスが見つからない"
    exe = main[0][1]
    log(f"[move] Antigravity を閉じる（{len(todo)} 件を移すため）")
    for p, _ in main:  # /F なしならウィンドウを閉じる（ふつうに終わる）
        subprocess.run(["taskkill", "/PID", str(p)], capture_output=True, creationflags=0x08000000)
    t0 = time.time()
    while True:
        time.sleep(1)
        app, hubs = _app_procs()
        if not app and not hubs:
            break
        if time.time() - t0 > 25:
            log("[move] Antigravity が終了しないため中断（未保存ファイルのデータ消失を防ぐため強制終了は行いません）")
            for cid in todo:
                res[cid] = (False, "Antigravity が終了しませんでした（未保存ファイルがある可能性があります）")
            return res, "Antigravity が終了しませんでした（未保存ファイルがある可能性があります）。ファイルを保存してからもう一度お試しください"
    _hub = None
    with _servers_lock:
        _servers.clear()
    time.sleep(1)
    for cid, f in todo.items():
        try:
            _rewrite_workspace(cid, f, projects.get(f), backup)
            res[cid] = (True, f"{f} に移した" + ("" if projects.get(f) else "（プロジェクトなし）"))
            log(f"[move] {cid[:8]} -> {f}")
        except Exception as e:
            res[cid] = (False, f"書き換えられなかった: {e}")
    log(f"[move] Antigravity を起動（閉じるのに {time.time() - t0:.0f} 秒）")
    os.startfile(exe)
    t1 = time.time()
    for _ in range(60):
        time.sleep(2)
        if hub_server():
            for cid, (ok, _) in res.items():  # 起動直後は開いた会話しか読み込まれないので、移した会話を読み込ませておく
                if ok:
                    locate(cid)
            log(f"[move] 起動した（{time.time() - t1:.0f} 秒）")
            return res, "Antigravity を起動し直した"
    return res, "書き換えたが、Antigravity が起動したか確かめられない（起動していなければ手で起動）"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--status" in sys.argv[:-1]:
        cid = sys.argv[sys.argv.index("--status") + 1]
        port, tok, s = locate(cid)
        print(f"port={port} {s.get('summary')} {s.get('status')} steps={s.get('stepCount')}"
              f" lastUserInput={s.get('lastUserInputTime')}" if port else "この会話を持つ言語サーバーが見つからない")
    elif "--project" in sys.argv[:-1]:
        hub = hub_server()
        print(find_project(*hub, sys.argv[sys.argv.index("--project") + 1]) if hub else "Antigravity が見つからない")
    elif "--find" in sys.argv[:-1]:
        hwnd, t = find_window([sys.argv[sys.argv.index("--find") + 1]])
        print(f"見つかった: {t}" if hwnd else "見つからない")
    else:
        for hwnd, t in ide_windows():
            print(hwnd, t)
