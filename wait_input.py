"""
AI側の待機用。どれかのチャンネルに新しい入力が来るまで待ち、
来たらそのチャンネルの設定・AIメモ・固定キャラ・会話・発言をそのまま表示して終了する。
担当IDEの会話が前回と同じなら、増えた発言と変わった設定だけを表示する（前に表示したものは会話に残っているので）。
担当が切り替わった・初めて・過去が書き換えられたときは全部表示する。
  python wait_input.py
  python wait_input.py --ide <Antigravityの会話ID>   # UIでこの会話を担当にしたチャンネルだけ受け取る
  python wait_input.py --ide <会話ID> --wait 60      # 待つのは60秒まで（--wait なしは30秒）
担当IDE会話があるチャンネルでは、入力から2秒後にその会話へスペース1文字（合図）が送られる（会話IDを指定して送る）。
AIはそれを合図に wait_input.py を実行し、応答を書いたら待機せずにスペース1文字だけ返してターンを終える。
（--ide なしの場合は、担当IDE会話が指定されていないチャンネルだけを受け取る。このときは毎回全部表示する）
どちらでも、応答を書いたあとに wait_input.py で次の入力を待ち続けることはさせない。
待つ間も AI が何度も様子を見に動くので、入力がなくても使用量が減っていくため。
応答の書き方は「応答の書き方」の項に出る:
  outbox/<session>_<任意>.json に
  {"session": "...", "reply_to": "...", "text": "...", "memo": "",
   "choices": [{"label": "要約", "text": "セリフや行動"}],       # 任意（設定オン時）
   "image": {"prompt": "...", "characters": ["キャラ名"]},   # 任意
   "characters": [{"name": "キャラ名", "tags": "外見タグ"}]}  # 任意
"""
import http.client
import json, os, re, sys, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(HERE, "outbox")
DONE = os.path.join(OUTBOX, "done")
CTX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ctx")  # 長い内容の書き出し先（チャンネルごとに上書き）
LONG, LONG_LINES = 6500, 220  # これより長いと表示せずファイルに書く（Antigravityの標準出力上限手前まで直渡し）
IDE = sys.argv[sys.argv.index("--ide") + 1] if "--ide" in sys.argv[:-1] else ""
WAIT = float(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv[:-1] else 30
WAIT = WAIT if WAIT > 0 else 30  # 待ち続けはしない
sys.stdout.reconfigure(encoding="utf-8")

_SERVER_HOST_PORT = None
_HTTP_CONN = None


def server_host_port():
    """起動時に1度だけ接続先ポートを解決してキャッシュする"""
    global _SERVER_HOST_PORT
    if _SERVER_HOST_PORT is None:
        port = os.environ.get("RPCHAT_PORT")
        if not port:
            try:
                with open(os.path.join(HERE, "data", "server.json"), encoding="utf-8") as f:
                    port = str(int(json.load(f)["port"]))
            except Exception:
                port = "8200"
        _SERVER_HOST_PORT = f"127.0.0.1:{port}"
    return _SERVER_HOST_PORT


def server_url():
    return "http://" + server_host_port()


def _request(method, path, body=None):
    """Keep-Alive接続を再利用して高速にリクエストを送る"""
    global _HTTP_CONN
    headers = {"Connection": "keep-alive"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    for attempt in range(2):
        try:
            if _HTTP_CONN is None:
                _HTTP_CONN = http.client.HTTPConnection(server_host_port(), timeout=10)
            _HTTP_CONN.request(method, path, body=data, headers=headers)
            resp = _HTTP_CONN.getresponse()
            raw = resp.read()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}: {raw.decode('utf-8', 'replace')}")
            return json.loads(raw.decode("utf-8"))
        except Exception:
            if _HTTP_CONN is not None:
                try:
                    _HTTP_CONN.close()
                except Exception:
                    pass
                _HTTP_CONN = None
            if attempt == 1:
                raise


def get(p):
    return _request("GET", p)


def post(p, d):
    return _request("POST", p, d)


def _has_json_in_outbox():
    """os.scandirで直近1.5秒以内に書き込まれた未処理JSONの有無のみを高速に確認する"""
    now_t = time.time()
    try:
        with os.scandir(OUTBOX) as it:
            for e in it:
                if e.is_file() and e.name.lower().endswith(".json"):
                    try:
                        if now_t - e.stat().st_mtime < 1.5:
                            return True
                    except OSError:
                        pass
        return False
    except OSError:
        return False


def show(sid, m, note="", ctx=None, task_type=None):
    if not ctx:
        payload = {"sid": sid, "id": m.get("id", ""), "ide": IDE, "note": note}
        if task_type:
            payload["type"] = task_type
        ctx = post("/api/context", payload)
    text = ctx.get("render")
    if not text:
        # ctx に render がない場合（直接渡されたテスト等）、可能なら render_input_text で生成
        try:
            import server
            text = server.render_input_text(sid, m, ctx, task_type, IDE, note)
        except Exception:
            pass
    if not text:
        print("（サーバーが表示文を返さない: server.py と wait_input.py の版が違う可能性）", file=sys.stderr)
        sys.exit(1)
    print(text, end="")
    sys.exit(0)


def check_previous(return_pending=False):
    """前回書いた応答が本当に取り込まれたか確かめる。
    取り込めなかった応答ファイルの理由と、受け取ったまま応答が届いていない入力を返す"""
    # 未処理ファイルがない通常時は初回(0秒)で即 break し、処理中ファイルがある時のみ最大1.05秒待機する
    waited = False
    for _ in range(35):
        if not _has_json_in_outbox():
            break
        waited = True
        time.sleep(0.03)
    if waited:
        time.sleep(0.03)  # 取り込み待機が発生した直後のみ最小限のバッファ

    # /api/pending 1回で未完了(processing)と自身のセッションID一覧を受け取る（IDEが空のときの ? 欠落も防止）
    q_str = f"?ide={urllib.parse.quote(IDE)}&check=1" if IDE else "?check=1"
    r = get("/api/pending" + q_str)
    all_ids = set(r.get("all_ids") or [])
    ids = set(r.get("mine_ids") or [])
    left = r.get("left")  # [sid, msg] または None
    ide_role = r.get("ide_role", "both")

    def ours(name):  # 応答ファイル名は <session>_<任意>.json
        head = name.split("_")[0]
        if not (head in ids or head not in all_ids):
            return False
        if ide_role == "choices":
            return "_choices_" in name
        elif ide_role == "main":
            return "_choices_" not in name
        return True

    warn = []
    try:
        with os.scandir(OUTBOX) as it:
            entries = sorted([e for e in it if e.is_file()], key=lambda e: e.name)
            for entry in entries:
                n = entry.name
                if ours(n):
                    warn.append(f"{n}: まだ取り込まれていない" if n.lower().endswith(".json")
                                else f"{n}: 拡張子が .json ではないので取り込まれない")
    except OSError:
        pass
    reasons = {".bad": "JSONとして読めない", ".stale": "reply_to の入力が応答待ちではない（IDが違う/もう応答済み）",
               ".nosession": "session が存在しない"}
    if os.path.isdir(DONE):
        try:
            with os.scandir(DONE) as it:
                err_entries = [e for e in it if e.name.endswith((".bad", ".stale", ".nosession")) and e.is_file()]
            for entry in sorted(err_entries, key=lambda e: e.name):
                n = entry.name
                ext = os.path.splitext(n)[1]
                if ext in reasons and ours(n):
                    try:
                        st_mtime = entry.stat().st_mtime
                    except OSError:
                        continue
                    if time.time() - st_mtime < 600:
                        p = entry.path
                        why = reasons[ext]
                        if ext == ".bad":
                            try:
                                with open(p, encoding="utf-8-sig") as f:
                                    json.load(f)
                            except Exception as e:
                                why += f" ({e})"
                        warn.append(f"{n}: 取り込み失敗 - {why}")
        except OSError:
            pass

    task_type = r.get("task_type")
    pending = (r.get("session"), r.get("pending"), task_type) if (r.get("session") and r.get("pending")) else None
    cleaned_warn = warn if left else [w for w in warn if "取り込み失敗" not in w]
    left_res = tuple(left) if left else None
    left_type = r.get("left_type")
    if return_pending:
        return cleaned_warn, left_res, pending, left_type
    return cleaned_warn, left_res, left_type


def main():
    pending = None
    left_type = None
    try:
        warn, left, pending, left_type = check_previous(return_pending=True)
    except Exception:
        warn, left, pending, left_type = [], None, None, None
    if warn:
        print("=== 前回の応答ファイルについて ===")
        for w in warn:
            print(f"- {w}")
        print(f"（outbox の場所: {OUTBOX}）")
    if left:
        sid, m = left
        res = post("/api/claim", {"sid": sid, "id": m["id"], "type": left_type, "context": True,
                                  "ide": IDE, "note": "この入力は受け取り済みだが、応答がチャットに届いていない。もう一度 outbox に書き直すこと"})
        show(sid, m, note="この入力は受け取り済みだが、応答がチャットに届いていない。もう一度 outbox に書き直すこと",
             ctx=res.get("context"), task_type=left_type)

    if pending and pending[0] and pending[1]:
        sid, m, task_type = pending
        res = post("/api/claim", {"sid": sid, "id": m["id"], "type": task_type, "context": True, "ide": IDE})
        show(sid, m, ctx=res.get("context"), task_type=task_type)

    told = False
    start = time.time()
    while True:
        if time.time() - start > WAIT:
            print(f"（{WAIT:g}秒待ったが新しい入力はない。もう一度実行せずに、" +
                  ("この会話への返事は半角スペース1文字だけにして終える）" if IDE else "このターンを終える）"))
            sys.exit(0)
        try:
            r = get("/api/pending" + ("?ide=" + urllib.parse.quote(IDE) if IDE else ""))
            m, sid, task_type = r.get("pending"), r.get("session"), r.get("task_type")
            if m:
                res = post("/api/claim", {"sid": sid, "id": m["id"], "type": task_type, "context": True, "ide": IDE})
                show(sid, m, ctx=res.get("context"), task_type=task_type)
        except SystemExit:
            raise
        except Exception:
            if not told:  # 一度だけ伝える。サーバーはユーザーが起動するもの（2台目を立てると outbox を取り合う）
                print(f"（{server_url()} につながらない。server.py の起動を待っている。server.py を自分で起動しないこと）", flush=True)
                told = True
        time.sleep(0.08)


if __name__ == "__main__":
    main()
