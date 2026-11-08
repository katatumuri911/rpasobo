"""read_past_messages.py
過去メッセージ閲覧ツール (標準ライブラリのみで動作)
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
STATES_DIR = os.path.join(DATA_DIR, "states")
IMAGES_DIR = os.path.join(DATA_DIR, "images")


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


def get_default_port():
    port = os.environ.get("RPCHAT_PORT")
    if port:
        try:
            return int(port)
        except ValueError:
            pass
    server_json = os.path.join(DATA_DIR, "server.json")
    if os.path.isfile(server_json):
        try:
            with open(server_json, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d.get("port"), int):
                    return d["port"]
        except Exception:
            pass
    return 8200


def fetch_from_api(port, mid, sid=None, window=5):
    query = {"mid": mid, "window": str(window)}
    if sid:
        query["sid"] = sid
    url = f"http://127.0.0.1:{port}/api/messages/around?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
    return None


def fetch_from_files(mid, sid=None, window=5):
    target_sid = sid
    session_data = None

    if target_sid:
        spath = os.path.join(SESSIONS_DIR, f"{target_sid}.json")
        if os.path.isfile(spath):
            with open(spath, "r", encoding="utf-8") as f:
                cur_s = json.load(f)
                if any(m.get("id") == mid for m in cur_s.get("messages", [])):
                    session_data = cur_s
        if not session_data and os.path.isdir(ARCHIVE_DIR):
            for fname in sorted(os.listdir(ARCHIVE_DIR), key=archive_sort_key, reverse=True):
                if fname.startswith(f"{target_sid}_") and fname.endswith(".json"):
                    try:
                        with open(os.path.join(ARCHIVE_DIR, fname), "r", encoding="utf-8") as f:
                            cur_s = json.load(f)
                            if any(m.get("id") == mid for m in cur_s.get("messages", [])):
                                session_data = cur_s
                                break
                    except Exception:
                        continue
    else:
        if os.path.isdir(SESSIONS_DIR):
            for fname in os.listdir(SESSIONS_DIR):
                if not fname.endswith(".json"):
                    continue
                spath = os.path.join(SESSIONS_DIR, fname)
                try:
                    with open(spath, "r", encoding="utf-8") as f:
                        cur_s = json.load(f)
                        if any(m.get("id") == mid for m in cur_s.get("messages", [])):
                            session_data = cur_s
                            target_sid = cur_s.get("id") or os.path.splitext(fname)[0]
                            break
                except Exception:
                    continue
        if not session_data and os.path.isdir(ARCHIVE_DIR):
            for fname in sorted(os.listdir(ARCHIVE_DIR), key=archive_sort_key, reverse=True):
                if not fname.endswith(".json"):
                    continue
                apath = os.path.join(ARCHIVE_DIR, fname)
                try:
                    with open(apath, "r", encoding="utf-8") as f:
                        cur_s = json.load(f)
                        if any(m.get("id") == mid for m in cur_s.get("messages", [])):
                            session_data = cur_s
                            target_sid = cur_s.get("id") or (fname.rsplit("_rot", 1)[0] if "_rot" in fname else fname.split("_")[0])
                            break
                except Exception:
                    continue

    if not session_data:
        return None

    msgs = session_data.get("messages", [])
    target_idx = next((i for i, m in enumerate(msgs) if m.get("id") == mid), -1)
    if target_idx < 0:
        return None

    start = max(0, target_idx - window)
    end = min(len(msgs), target_idx + window + 1)
    res_msgs = []
    st_total = session_data.get("setting", {}).get("total_turns")

    states_data = {}
    images_data = {}
    if target_sid:
        sp = os.path.join(STATES_DIR, f"{target_sid}.json")
        if os.path.isfile(sp):
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    states_data = json.load(f)
            except Exception:
                pass
        ip = os.path.join(IMAGES_DIR, f"{target_sid}.json")
        if os.path.isfile(ip):
            try:
                with open(ip, "r", encoding="utf-8") as f:
                    images_data = json.load(f)
            except Exception:
                pass

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
            et = session_data.get("setting", {}).get("elapsed_time")

        im = cur.get("image")
        if isinstance(im, dict):
            im = dict(im)
            if cur_mid and cur_mid in images_data:
                for k, v in images_data[cur_mid].items():
                    if k not in im or im[k] is None:
                        im[k] = v
        st_val = cur.get("state")
        if st_val is None and cur_mid and cur_mid in states_data:
            st_val = states_data[cur_mid]

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

    return {
        "ok": True,
        "sid": target_sid,
        "target_idx": target_idx,
        "messages": res_msgs
    }


def format_elapsed_time(et):
    if not et or not isinstance(et, dict):
        return "日時不明"
    day = et.get("day", 1)
    period = et.get("period", "午前")
    return f"{day}日目・{period}"


def print_messages(data, mid):
    sid = data.get("sid", "unknown")
    msgs = data.get("messages", [])
    print(f"=== [Session: {sid} | Target Message: {mid} | Count: {len(msgs)}] ===\n")
    for m in msgs:
        turn_str = f"Turn {m.get('turn_idx', '?')}"
        et_str = format_elapsed_time(m.get("elapsed_time"))
        role = (m.get("role") or "").upper()
        target_marker = " >>> [TARGET] <<<" if m.get("is_target") or m.get("id") == mid else ""
        print(f"--- [{et_str} | {turn_str}] {role} (id: {m.get('id')}){target_marker} ---")
        text = m.get("text", "").strip()
        if text:
            print(text)
        files = m.get("files") or []
        for f in files:
            print(f"[添付ファイル: {f.get('name')}]")
        im = m.get("image") or {}
        if im.get("prompt"):
            print(f"[画像プロンプト: {im.get('prompt')}]")
        st = m.get("state")
        if st and isinstance(st, list):
            print(f"[キャラの状態: {len(st)}人]")
            for c in st:
                c_name = c.get("name", "名前なし")
                outfit = c.get("outfit", "")
                inner = c.get("inner", "")
                extras = []
                if c.get("outerwear"):
                    extras.append(f"上着: {c['outerwear']}")
                if c.get("underwear"):
                    extras.append(f"下着: {c['underwear']}")
                if c.get("penetration") and c["penetration"] != "なし":
                    extras.append(f"結合: {c['penetration']}")
                extra_str = (" | " + " | ".join(extras)) if extras else ""
                print(f"  ■ {c_name} | 服: {outfit}{extra_str} | 内心: {inner}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Read past messages around a given message ID.")
    parser.add_argument("--mid", required=True, help="Target message ID")
    parser.add_argument("--sid", default=None, help="Session ID (optional; inferred if omitted)")
    parser.add_argument("--window", type=int, default=5, help="Number of messages before/after (default: 5)")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: auto)")

    args = parser.parse_args()
    port = args.port or get_default_port()

    data = None
    # 1. Try API first
    try:
        data = fetch_from_api(port, args.mid, sid=args.sid, window=args.window)
    except Exception:
        data = None

    # 2. Fall back to local file reading
    if not data or not data.get("ok"):
        try:
            data = fetch_from_files(args.mid, sid=args.sid, window=args.window)
        except Exception as e:
            sys.stderr.write(f"Error reading local files: {e}\n")

    if not data or not data.get("ok"):
        sys.stderr.write(f"Message ID '{args.mid}' not found.\n")
        sys.exit(1)

    print_messages(data, args.mid)


if __name__ == "__main__":
    main()
