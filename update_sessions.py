"""
Migration script to update existing sessions in data/sessions to 8-step Hyper-SD fast generation.
"""
import os
import json
import time
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
SESS_DIR = os.path.join(ROOT, "data", "sessions")
TRASH_DIR = os.path.join(ROOT, "data", "trash")

NEW_IMAGE_PARAMS = {
    "steps": 8,
    "cfg": 2.0,
    "sampler": "euler",
    "scheduler": "normal",
    "clip_skip": 2,
    "speed_lora": "Hyper-SDXL-8steps-lora.safetensors",
    "speed_lora_strength": 0.8,
}

def migrate_sessions():
    os.makedirs(TRASH_DIR, exist_ok=True)
    session_files = [f for f in os.listdir(SESS_DIR) if f.endswith(".json")]
    print(f"Found {len(session_files)} session files in {SESS_DIR}")

    updated = []
    ts = int(time.time())

    for fname in session_files:
        fpath = os.path.join(SESS_DIR, fname)
        with open(fpath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        sid = data.get("id") or fname[:-5]
        # Backup
        backup_path = os.path.join(TRASH_DIR, f"{sid}_{ts}_pre_hypersd_backup.json")
        shutil.copy2(fpath, backup_path)

        st = data.setdefault("setting", {})
        img_st = st.setdefault("image", {})

        old_steps = img_st.get("steps")
        old_cfg = img_st.get("cfg")
        old_sampler = img_st.get("sampler")

        # Update image settings with fast generation parameters
        img_st.update(NEW_IMAGE_PARAMS)

        # Write safely
        tmp_path = f"{fpath}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, fpath)

        print(f"Updated {fname}: steps {old_steps}->{img_st['steps']}, cfg {old_cfg}->{img_st['cfg']}, sampler {old_sampler}->{img_st['sampler']}")
        updated.append(fname)

    return updated

def verify_sessions():
    print("\n=== Verifying migrated session files ===")
    session_files = [f for f in os.listdir(SESS_DIR) if f.endswith(".json")]
    all_ok = True
    for fname in session_files:
        fpath = os.path.join(SESS_DIR, fname)
        with open(fpath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        ic = data.get("setting", {}).get("image", {})
        for k, expected_v in NEW_IMAGE_PARAMS.items():
            actual_v = ic.get(k)
            if actual_v != expected_v:
                print(f"[FAIL] {fname}: {k} expected {expected_v}, got {actual_v}")
                all_ok = False
        print(f"[OK] {fname}: steps={ic.get('steps')}, cfg={ic.get('cfg')}, sampler={ic.get('sampler')}, clip_skip={ic.get('clip_skip')}, speed_lora={ic.get('speed_lora')}")
    return all_ok

if __name__ == "__main__":
    migrate_sessions()
    success = verify_sessions()
    print(f"\nMigration successful: {success}")
    if not success:
        sys.exit(1)
