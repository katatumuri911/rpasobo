# -*- coding: utf-8 -*-
import os
import json
import re
import zipfile
import datetime

ROOT_DIR = r"c:\Users\nukon\rp-image-chat-main"
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTBOX_DIR = os.path.join(ROOT_DIR, "outbox")
SESS_DIR = os.path.join(DATA_DIR, "sessions")

jp_re = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]")

def get_session_info():
    keep_sids = {}
    del_sids = {}
    for f in os.listdir(SESS_DIR):
        if not f.endswith(".json"):
            continue
        p = os.path.join(SESS_DIR, f)
        try:
            with open(p, "r", encoding="utf-8") as fp:
                d = json.load(fp)
                sid = d.get("id", f[:-5])
                title = (d.get("setting", {}) or {}).get("title", "")
                if jp_re.search(title):
                    keep_sids[sid] = title
                else:
                    del_sids[sid] = title
        except Exception as e:
            print(f"Error reading session {f}: {e}")
    return keep_sids, del_sids

def collect_target_files(keep_sids, del_sids):
    to_delete = []

    # 1. sessions: del_sids に該当するもの & tmp
    for f in os.listdir(SESS_DIR):
        sid = f.split(".")[0].split("_")[0]
        if sid in del_sids:
            to_delete.append(os.path.join(SESS_DIR, f))
        elif sid not in keep_sids and f.endswith(".json"):
            to_delete.append(os.path.join(SESS_DIR, f))

    # 2. choices, states, images, sent
    for sub in ["choices", "states", "images", "sent"]:
        dp = os.path.join(DATA_DIR, sub)
        if not os.path.exists(dp):
            continue
        for f in os.listdir(dp):
            sid = f.split(".")[0].split("_")[0]
            if sid in del_sids or sid not in keep_sids:
                to_delete.append(os.path.join(dp, f))

    # 3. rag
    rag_dp = os.path.join(DATA_DIR, "rag")
    if os.path.exists(rag_dp):
        for f in os.listdir(rag_dp):
            sid = f.split(".")[0].split("_")[0]
            if sid in del_sids or sid not in keep_sids:
                to_delete.append(os.path.join(rag_dp, f))

    # 4. ctx
    ctx_dp = os.path.join(DATA_DIR, "ctx")
    if os.path.exists(ctx_dp):
        for f in os.listdir(ctx_dp):
            sid = f.split(".")[0].split("_")[0]
            if sid in del_sids or sid not in keep_sids:
                to_delete.append(os.path.join(ctx_dp, f))

    # 5. archive
    arc_dp = os.path.join(DATA_DIR, "archive")
    if os.path.exists(arc_dp):
        for f in os.listdir(arc_dp):
            sid = f.split(".")[0].split("_")[0]
            if sid in del_sids or sid not in keep_sids:
                to_delete.append(os.path.join(arc_dp, f))

    # 6. uploads
    up_dp = os.path.join(DATA_DIR, "uploads")
    if os.path.exists(up_dp):
        for item in os.listdir(up_dp):
            item_p = os.path.join(up_dp, item)
            if os.path.isdir(item_p):
                sid = item
                if sid in del_sids or sid not in keep_sids:
                    to_delete.append(item_p)

    # 7. outbox & outbox/done
    for b in [OUTBOX_DIR, os.path.join(OUTBOX_DIR, "done")]:
        if os.path.exists(b):
            for f in os.listdir(b):
                fp = os.path.join(b, f)
                if not os.path.isfile(fp):
                    continue
                # f could be <sid>_* or <ts>_<sid>_*
                parts = f.split("_")
                match_del = False
                for sid in del_sids:
                    if f.startswith(f"{sid}_") or (len(parts) > 1 and parts[1] == sid):
                        match_del = True
                        break
                if match_del:
                    to_delete.append(fp)

    return sorted(list(set(to_delete)))

def create_backup_and_execute(to_delete, keep_sids):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_zip_path = os.path.join(DATA_DIR, f"_cleanup_backup_{timestamp}.zip")

    print(f"Creating backup zip at: {backup_zip_path}")
    with zipfile.ZipFile(backup_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in to_delete:
            if os.path.isfile(p):
                arcname = os.path.relpath(p, ROOT_DIR)
                zf.write(p, arcname)
            elif os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    for file in files:
                        fp = os.path.join(root, file)
                        arcname = os.path.relpath(fp, ROOT_DIR)
                        zf.write(fp, arcname)

    print(f"Backup complete. Size: {os.path.getsize(backup_zip_path)} bytes.")

    # 削除実行
    del_count = 0
    dir_count = 0
    import shutil
    for p in to_delete:
        try:
            if os.path.isfile(p):
                os.remove(p)
                del_count += 1
            elif os.path.isdir(p):
                shutil.rmtree(p)
                dir_count += 1
        except Exception as e:
            print(f"Failed to delete {p}: {e}")

    print(f"Cleanup finished. Files removed: {del_count}, Directories removed: {dir_count}")

    # 検証
    remaining_sessions = [f[:-5] for f in os.listdir(SESS_DIR) if f.endswith(".json")]
    print(f"Remaining sessions count: {len(remaining_sessions)}")
    assert set(remaining_sessions) == set(keep_sids.keys()), f"Mismatch in kept sessions! {set(remaining_sessions)} != {set(keep_sids.keys())}"
    print("Verification SUCCESS: All and only kept Japanese sessions exist!")

if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    keep_sids, del_sids = get_session_info()
    print(f"Keep sessions ({len(keep_sids)}): {list(keep_sids.values())}")
    print(f"Delete sessions ({len(del_sids)})")
    to_delete = collect_target_files(keep_sids, del_sids)
    print(f"Total target files/dirs to delete: {len(to_delete)}")
    if dry_run:
        print("[DRY RUN] No files were deleted or backed up.")
    else:
        create_backup_and_execute(to_delete, keep_sids)
