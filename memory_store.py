"""
長期記憶（RAG）システム - memory_store.py
完全ローカル・単一JSONファイルによるキャラクター長期記憶ストレージ＆ハイブリッド検索エンジン
"""
import collections
import copy
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
RAG_DIR = os.path.join(DATA, "rag")
TRASH = os.path.join(DATA, "trash")
MODELS_DIR = os.environ.get("RPCHAT_MODELS", os.path.join(ROOT, "models"))

STOP_WORDS = {
    "ユーザー", "こと", "もの", "ため", "よう", "さん", "ちゃん", "くん",
    "日", "時", "今", "今日", "明日", "行く", "来る", "する", "なる",
    "いる", "ある", "言う", "思う", "見る"
}

PROMISE_KEYWORDS = ("約束", "予定", "誓約", "誓い", "行く約束", "会う約束")
USER_FACT_KEYWORDS = ("好き", "嫌い", "好み", "苦手", "趣味", "タイプ", "誕生日", "実は", "飼って", "秘密", "個人情報")
CHAR_FACT_KEYWORDS = ("実は", "秘密", "トラウマ", "過去", "家族", "体質", "弱点", "本当は", "コンプレックス", "生い立ち")
RELATIONSHIP_KEYWORDS = ("関係", "距離", "好意", "好き", "信頼", "依存", "恋人", "友達", "仲")
CALLING_KEYWORDS = ("呼び方", "呼んで", "名前", "くん", "ちゃん", "先輩", "お兄ちゃん", "様", "敬語", "呼び捨て")
ITEM_KEYWORDS = ("もらった", "あげた", "プレゼント", "合鍵", "指輪", "ネックレス", "首輪", "アナルプラグ", "プラグ", "拘束具", "キスマーク", "傷", "痕", "持ち物", "アイテム")
RULE_KEYWORDS = ("ルール", "規則", "禁止", "門限", "約束", "掟", "校則", "地雷", "タブー", "秘密", "約束事")
EVENT_KEYWORDS = ("事件", "出来事", "雨宿り", "旅行", "告白", "助けた", "救った", "出会った", "記念", "初めて", "お泊まり", "デート")

CATEGORY_LABELS = {
    "calling": "呼称・口調",
    "rule": "ルール・規則",
    "taboo": "タブー・禁忌",
    "rule_taboo": "規則・禁忌",
    "item": "アイテム・所持品",
    "status": "状態・痕跡",
    "item_status": "アイテム・痕跡",
    "promise": "約束・予定",
    "char_fact": "キャラの秘密",
    "user_fact": "ユーザーの好み",
    "relationship": "関係性の変化",
    "event": "重要出来事"
}

CATEGORY_ALIASES = {
    "calling": "calling",
    "call": "calling",
    "call_name": "calling",
    "呼称": "calling",
    "呼び方": "calling",
    "口調": "calling",
    "rule": "rule",
    "ルール": "rule",
    "校則": "rule",
    "掟": "rule",
    "規則": "rule",
    "taboo": "taboo",
    "タブー": "taboo",
    "禁忌": "taboo",
    "地雷": "taboo",
    "禁止": "taboo",
    "rule_taboo": "rule_taboo",
    "規則・禁忌": "rule_taboo",
    "item": "item",
    "アイテム": "item",
    "所持品": "item",
    "持ち物": "item",
    "プレゼント": "item",
    "status": "status",
    "状態": "status",
    "痕跡": "status",
    "item_status": "item_status",
    "アイテム・痕跡": "item_status",
    "promise": "promise",
    "約束": "promise",
    "予定": "promise",
    "誓約": "promise",
    "char_fact": "char_fact",
    "char": "char_fact",
    "character": "char_fact",
    "secret": "char_fact",
    "キャラ": "char_fact",
    "キャラの秘密": "char_fact",
    "user_fact": "user_fact",
    "user": "user_fact",
    "ユーザー": "user_fact",
    "ユーザーの好み": "user_fact",
    "好み": "user_fact",
    "relationship": "relationship",
    "relation": "relationship",
    "rel": "relationship",
    "関係": "relationship",
    "関係性": "relationship",
    "event": "event",
    "出来事": "event",
    "事件": "event",
}

VALID_CATEGORIES = set(CATEGORY_LABELS.keys())

_store_lock = threading.RLock()
_tokenizer = None
_tokenizer_lock = threading.Lock()
_embedder = None
_embedder_lock = threading.Lock()


def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        with _tokenizer_lock:
            if _tokenizer is None:
                from janome.tokenizer import Tokenizer
                _tokenizer = Tokenizer()
    return _tokenizer


def get_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from fastembed import TextEmbedding
                os.makedirs(MODELS_DIR, exist_ok=True)
                _embedder = TextEmbedding(
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                    cache_dir=MODELS_DIR
                )
    return _embedder


def compute_hash(content):
    if not isinstance(content, str):
        content = ""
    return hashlib.md5(content.strip().encode("utf-8")).hexdigest()[:8]


def extract_tokens(text):
    if not text or not isinstance(text, str):
        return []
    try:
        tokenizer = get_tokenizer()
        tokens = []
        for token in tokenizer.tokenize(text):
            pos = token.part_of_speech.split(",")
            pos0 = pos[0]
            pos1 = pos[1] if len(pos) > 1 else ""

            word = None
            if pos0 == "名詞":
                if pos1 not in ("非自立", "代名詞", "数"):
                    word = token.surface
            elif pos0 == "動詞" and pos1 == "自立":
                word = token.base_form if token.base_form != "*" else token.surface
            elif pos0 == "形容詞" and pos1 == "自立":
                word = token.base_form if token.base_form != "*" else token.surface

            if word and word not in STOP_WORDS and len(word.strip()) > 0:
                tokens.append(word.strip())
        return list(dict.fromkeys(tokens))
    except Exception as e:
        print(f"[memory_store] extract_tokens error: {e}")
        return []


_COMPACT_JSON_RE = re.compile(r'(?<!\\)"(tokens|embedding)":\s*\[([^\]]*?)\]', flags=re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r',\s*([\]}])')
_memory_cache = {}  # session_id -> {"mtime": float, "size": int, "data": dict}
_query_cache = collections.OrderedDict()  # text -> (tokens, embedding) の LRU キャッシュ
_query_cache_lock = threading.Lock()
_feature_compute_lock = threading.Lock()


def get_query_features(text):
    """クエリの形態素トークンとEmbeddingをLRUキャッシュ付きで取得する（二重推論防止ロック付き）"""
    if not text or not isinstance(text, str):
        return [], []
    text = text.strip()
    if not text:
        return [], []
    with _query_cache_lock:
        cached = _query_cache.get(text)
        if cached is not None:
            _query_cache.move_to_end(text)
            return list(cached[0]), list(cached[1])

    with _feature_compute_lock:
        with _query_cache_lock:
            cached = _query_cache.get(text)
            if cached is not None:
                _query_cache.move_to_end(text)
                return list(cached[0]), list(cached[1])
        tokens = extract_tokens(text)
        vec = get_embedding(text)
        if vec:
            with _query_cache_lock:
                _query_cache[text] = (tokens, vec)
                _query_cache.move_to_end(text)
                while len(_query_cache) > 128:
                    _query_cache.popitem(last=False)
        return list(tokens), list(vec)


def warmup():
    """サーバー起動時にバックグラウンドでJanomeの初回tokenizeとFastEmbedの初回推論を実行しておく"""
    try:
        extract_tokens("初期ウォームアップ")
        get_embedding("初期ウォームアップ")
    except Exception as e:
        print(f"[memory_store] warmup error: {e}")


def get_embeddings_batch(texts):
    """複数テキストをまとめて1回の推論でベクトル化する"""
    if not texts:
        return []
    embedder = get_embedder()
    results = []
    for vec in embedder.embed(texts):
        norm = math.sqrt(sum(float(x) * float(x) for x in vec))
        if norm > 0:
            results.append([round(float(x) / norm, 4) for x in vec])
        else:
            results.append([round(float(x), 4) for x in vec])
    return results


def get_embedding(text):
    if not text or not isinstance(text, str):
        return []
    try:
        res = get_embeddings_batch([text])
        return res[0] if res else []
    except Exception as e:
        print(f"[memory_store] get_embedding error: {e}")
        return []


def cosine_similarity(v1, v2):
    """get_embeddingで正規化済みのため、内積のみで高速にコサイン類似度を算出する"""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    sim = sum(a * b for a, b in zip(v1, v2))
    return max(0.0, min(1.0, float(sim)))


def memory_path(session_id):
    os.makedirs(RAG_DIR, exist_ok=True)
    return os.path.join(RAG_DIR, f"{session_id}_memories.json")


def to_compact_json(data):
    s = json.dumps(data, ensure_ascii=False, indent=2)

    def repl(m):
        key = m.group(1)
        items = [x.strip() for x in m.group(2).split(",") if x.strip()]
        return f'"{key}": [' + ", ".join(items) + "]"

    return _COMPACT_JSON_RE.sub(repl, s)


def load_memories(session_id, character_name=""):
    path = memory_path(session_id)
    with _store_lock:
        try:
            st = os.stat(path)
        except OSError:
            _memory_cache.pop(session_id, None)
            return {
                "session_id": session_id,
                "character_name": character_name,
                "updated_at": time.time(),
                "memories": []
            }

        cached = _memory_cache.get(session_id)
        if cached and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
            data = copy.deepcopy(cached["data"])
            if character_name and not data.get("character_name"):
                data["character_name"] = character_name
            return data

        try:
            with open(path, encoding="utf-8") as f:
                raw_text = f.read()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                # Attempt to repair common trailing comma errors from Notepad edits
                cleaned = _TRAILING_COMMA_RE.sub(r'\1', raw_text)
                data = json.loads(cleaned)
            if not isinstance(data, dict):
                raise ValueError("JSON is not an object")
        except Exception as e:
            print(f"[memory_store] error loading {path}: {e}")
            try:
                import shutil
                corrupt_backup = path + f".corrupt_{int(time.time())}"
                shutil.copy2(path, corrupt_backup)
                print(f"[memory_store] backed up corrupted memories to {corrupt_backup}")
            except Exception:
                pass
            return {
                "session_id": session_id,
                "character_name": character_name,
                "updated_at": time.time(),
                "memories": []
            }

        data.setdefault("session_id", session_id)
        if character_name and not data.get("character_name"):
            data["character_name"] = character_name
        data.setdefault("updated_at", time.time())
        data.setdefault("memories", [])

        # Check content_hash for each memory (detect Notepad manual edits)
        needs_resave = False
        valid_memories = []
        for mem in data["memories"]:
            if not isinstance(mem, dict):
                continue
            content = mem.get("content", "")
            if not content:
                continue
            if not mem.get("id"):
                mem["id"] = "mem_" + uuid.uuid4().hex[:8]
                needs_resave = True
            if not mem.get("session_id"):
                mem["session_id"] = session_id
                needs_resave = True
            raw_cat = str(mem.get("category") or "").strip().lower()
            norm_cat = CATEGORY_ALIASES.get(raw_cat, raw_cat)
            if norm_cat in VALID_CATEGORIES:
                if mem.get("category") != norm_cat:
                    mem["category"] = norm_cat
                    needs_resave = True
            else:
                mem["category"] = "promise"
                needs_resave = True
            if not mem.get("source"):
                mem["source"] = "manual"
                needs_resave = True
            now_ts = time.time()
            if not mem.get("created_at"):
                mem["created_at"] = now_ts
                needs_resave = True
            current_hash = compute_hash(content)
            if mem.get("content_hash") != current_hash or not mem.get("embedding") or not mem.get("tokens"):
                mem["content_hash"] = current_hash
                mem["tokens"] = extract_tokens(content)
                mem["embedding"] = get_embedding(content)
                mem["updated_at"] = now_ts
                needs_resave = True
            if not mem.get("status"):
                mem["status"] = "active"
                needs_resave = True
            if not isinstance(mem.get("history"), list):
                mem["history"] = []
                needs_resave = True
            if not mem.get("elapsed_time"):
                try:
                    sess_path = os.path.join(DATA, "sessions", f"{session_id}.json")
                    if os.path.exists(sess_path):
                        with open(sess_path, encoding="utf-8") as sf:
                            s_data = json.load(sf)
                            msg_map = {m["id"]: m for m in s_data.get("messages", []) if isinstance(m, dict) and m.get("id")}
                            mid = mem.get("mid")
                            m_obj = msg_map.get(mid) if mid else None
                            el = (m_obj.get("elapsed_time") if m_obj else None) or s_data.get("setting", {}).get("elapsed_time")
                            if el:
                                mem["elapsed_time"] = copy.deepcopy(el)
                                needs_resave = True
                except Exception:
                    pass
                if not mem.get("elapsed_time"):
                    mem["elapsed_time"] = {"day": 1, "period": "午前"}
                    needs_resave = True
            valid_memories.append(mem)

        data["memories"] = valid_memories
        if needs_resave:
            save_memories(session_id, data)
        else:
            _memory_cache[session_id] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "data": copy.deepcopy(data)
            }

        return data


def save_memories(session_id, data):
    path = memory_path(session_id)
    tmp_path = path + f".tmp_{os.getpid()}_{threading.get_ident()}"
    with _store_lock:
        data["updated_at"] = time.time()
        formatted = to_compact_json(data)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(formatted)
        for _ in range(10):
            try:
                os.replace(tmp_path, path)
                break
            except OSError:
                time.sleep(0.01)
        else:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
        try:
            st = os.stat(path)
            _memory_cache[session_id] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "data": copy.deepcopy(data)
            }
        except OSError:
            _memory_cache.pop(session_id, None)


def search_memories(session_id, query_text, current_turn=None, shared_memories=False, character_name="", limit=10):
    query_text = (query_text or "").strip()
    if not query_text:
        return []

    candidates = []
    own_data = load_memories(session_id, character_name)
    own_memories = own_data.get("memories", [])

    for m in own_memories:
        # Filter: Exclude auto memories within recent 2 turns, except active unfulfilled promises, callings, rules, items
        if m.get("source") == "auto" and current_turn is not None:
            turn_idx_val = m.get("turn_idx") or 0
            if current_turn - turn_idx_val <= 2:
                if not (m.get("category") in ("promise", "calling", "rule_taboo", "rule", "taboo", "item_status", "item", "status") and m.get("status", "active") == "active"):
                    continue
        candidates.append(dict(m, _is_other=False))

    # If shared_memories is True, scan other session files (Read-Only)
    if shared_memories and character_name:
        try:
            for fname in sorted(os.listdir(RAG_DIR)):
                if not fname.endswith("_memories.json"):
                    continue
                other_sid = fname[:-len("_memories.json")]
                if other_sid == session_id:
                    continue
                try:
                    other_data = load_memories(other_sid)
                    if other_data.get("character_name") == character_name:
                        for om in other_data.get("memories", []):
                            if not any(c.get("id") == om.get("id") for c in candidates):
                                candidates.append(dict(om, _is_other=True))
                except Exception:
                    continue
        except Exception as e:
            print(f"[memory_store] error loading shared memories: {e}")

    if not candidates:
        return []

    query_tokens, query_vec = get_query_features(query_text)
    query_tokens_set = set(query_tokens)

    scored = []
    for mem in candidates:
        bonus = 0.0
        cat = mem.get("category", "")
        if cat == "promise" and any(kw in query_text for kw in PROMISE_KEYWORDS):
            bonus = 0.15
        elif cat == "user_fact" and any(kw in query_text for kw in USER_FACT_KEYWORDS):
            bonus = 0.15
        elif cat == "char_fact" and any(kw in query_text for kw in CHAR_FACT_KEYWORDS):
            bonus = 0.15
        elif cat == "calling" and any(kw in query_text for kw in CALLING_KEYWORDS):
            bonus = 0.15
        elif cat in ("item_status", "item", "status") and any(kw in query_text for kw in ITEM_KEYWORDS):
            bonus = 0.15
        elif cat in ("rule_taboo", "rule", "taboo") and any(kw in query_text for kw in RULE_KEYWORDS):
            bonus = 0.15
        elif cat == "relationship" and any(kw in query_text for kw in RELATIONSHIP_KEYWORDS):
            bonus = 0.15
        elif cat == "event" and any(kw in query_text for kw in EVENT_KEYWORDS):
            bonus = 0.15

        mem_tokens = mem.get("tokens") or []
        matched_count = 0
        if query_tokens and mem_tokens:
            mem_tokens_set = set(mem_tokens)
            exact_matches = query_tokens_set.intersection(mem_tokens_set)
            matched_count = len(exact_matches)
            for qt in query_tokens:
                if qt not in exact_matches and any(len(qt) >= 2 and len(mt) >= 2 and (qt in mt or mt in qt) for mt in mem_tokens):
                    matched_count += 1
        overlap_q = (matched_count / max(1, len(query_tokens))) if query_tokens else 0.0
        overlap_m = (matched_count / max(1, len(mem_tokens))) if mem_tokens else 0.0
        kw_ratio = max(overlap_q, overlap_m)
        s_kw = min(1.0, max(0.0, kw_ratio))

        mem_vec = mem.get("embedding") or []
        s_vec = cosine_similarity(query_vec, mem_vec)

        s_base = 0.70 * s_vec + 0.30 * s_kw + bonus

        # Recency Boost (0.0 for shared memories, 0.0-1.0 clamped ratio for own session)
        if mem.get("_is_other") or current_turn is None or current_turn <= 0:
            recency_boost = 0.0
        else:
            turn_idx_val = mem.get("turn_idx") or 0
            ratio = max(0.0, min(1.0, turn_idx_val / max(1, current_turn)))
            recency_boost = 0.05 * ratio

        final_score = s_base + recency_boost

        m_copy = dict(mem)
        m_copy["score"] = round(final_score, 4)
        scored.append({
            "memory": m_copy,
            "score": final_score,
            "s_vec": s_vec,
            "s_kw": s_kw
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 1. Tier 1: 常時前提枠（calling, active rule_taboo, active item_status）
    tier1_items = []
    tier1_ids = set()

    # calling (active): 全件（最大2件）
    callings = [item["memory"] for item in scored if item["memory"].get("category") == "calling" and item["memory"].get("status", "active") == "active"]
    for c in callings[:2]:
        c["_tier"] = 1
        tier1_items.append(c)
        tier1_ids.add(c["id"])

    # rule / taboo / rule_taboo (active): 最大2件
    rules = [item["memory"] for item in scored if item["memory"].get("category") in ("rule_taboo", "rule", "taboo") and item["memory"].get("status", "active") == "active" and item["memory"]["id"] not in tier1_ids]
    for r in rules[:2]:
        r["_tier"] = 1
        tier1_items.append(r)
        tier1_ids.add(r["id"])

    # item / status / item_status (active): 最大2件（スコア上位または直近）
    items = [item["memory"] for item in scored if item["memory"].get("category") in ("item_status", "item", "status") and item["memory"].get("status", "active") == "active" and item["memory"]["id"] not in tier1_ids]
    for it in items[:2]:
        it["_tier"] = 1
        tier1_items.append(it)
        tier1_ids.add(it["id"])

    # 2. Tier 2: 類似度想起枠（未達成の約束を最大2件保証し、残りを類似度スコア順で埋める）
    unfulfilled_promises = [
        item for item in scored
        if item["memory"].get("category") == "promise"
        and item["memory"].get("status", "active") == "active"
        and item["memory"]["id"] not in tier1_ids
    ]
    picked_promises = [p["memory"] for p in unfulfilled_promises[:2]]
    for p in picked_promises:
        p["_tier"] = 2
    picked_tier2_ids = {p["id"] for p in picked_promises}

    # 類似度スコア0.38以上の通常記憶
    remaining_scored = [
        item["memory"] for item in scored
        if item["score"] >= 0.38
        and item["memory"]["id"] not in tier1_ids
        and item["memory"]["id"] not in picked_tier2_ids
    ]
    for r in remaining_scored:
        r["_tier"] = 2

    # 残り枠を計算
    remaining_capacity = max(0, limit - len(tier1_items) - len(picked_promises))
    tier2_items = picked_promises + remaining_scored[:remaining_capacity]

    return tier1_items + tier2_items


def save_extracted_memories(session_id, character_name, new_memories, resolved_ids=None, turn_idx=None, mid=None, elapsed_time=None):
    if not session_id or not isinstance(session_id, str):
        return {"session_id": session_id or "", "character_name": character_name or "", "memories": [], "updated_at": time.time()}
    session_id = session_id.strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return {"session_id": "", "character_name": character_name or "", "memories": [], "updated_at": time.time()}

    character_name = str(character_name or "").strip()
    if turn_idx is not None:
        try:
            turn_idx = int(turn_idx)
        except (ValueError, TypeError):
            turn_idx = None
    if mid is not None:
        mid = str(mid).strip() or None

    # 1. 重い形態素解析とONNXベクトル推論は _store_lock の外で事前に完了させる（他スレッドの検索・削除をブロックしない）
    if isinstance(new_memories, dict):
        new_memories = [new_memories]
    elif isinstance(new_memories, str):
        new_memories = [{"category": "event", "content": new_memories}]

    valid_items = []
    seen_items = set()
    for item in (new_memories or []):
        if isinstance(item, str):
            item = {"category": "event", "content": item}
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or item.get("type") or item.get("cat") or "").strip().lower()
        cat = CATEGORY_ALIASES.get(cat, cat)
        content = str(item.get("content") or item.get("text") or item.get("memory") or item.get("detail") or "").strip()
        if not content or cat not in VALID_CATEGORIES:
            continue
        pair = (cat, content)
        if pair in seen_items:
            continue
        seen_items.add(pair)
        valid_items.append(pair)

    prepared_items = []
    if valid_items:
        texts = [c for _, c in valid_items]
        try:
            embeddings = get_embeddings_batch(texts)
        except Exception as e:
            print(f"[save_extracted_memories] get_embeddings_batch error: {e}")
            embeddings = [[] for _ in texts]
        if len(embeddings) != len(texts):
            embeddings = [[] for _ in texts]

        for (cat, content), new_vec in zip(valid_items, embeddings):
            try:
                tokens = extract_tokens(content)
            except Exception as e:
                print(f"[save_extracted_memories] extract_tokens error: {e}")
                tokens = []
            h = compute_hash(content)
            prepared_items.append((cat, content, new_vec, tokens, h))

    if elapsed_time is None and session_id:
        try:
            sess_path = os.path.join(DATA, "sessions", f"{session_id}.json")
            if os.path.exists(sess_path):
                with open(sess_path, encoding="utf-8-sig") as sf:
                    s_data = json.load(sf)
                    elapsed_time = copy.deepcopy(s_data.get("setting", {}).get("elapsed_time"))
        except Exception:
            pass
    if elapsed_time is None or not isinstance(elapsed_time, dict):
        elapsed_time = {"day": 1, "period": "午前"}
    else:
        elapsed_time = copy.deepcopy(elapsed_time)

    SEMANTIC_THRESHOLDS = {
        "calling": 0.70,
        "relationship": 0.70,
        "rule": 0.70,
        "taboo": 0.70,
        "rule_taboo": 0.70,
        "char_fact": 0.75,
        "user_fact": 0.75,
    }

    # 2. _store_lock 内では高速なメモリ上の類似度マージとファイル保存のみを行う
    with _store_lock:
        data = load_memories(session_id, character_name)
        memories = data.get("memories", [])

        # Process resolved_ids non-destructively
        if resolved_ids:
            if isinstance(resolved_ids, (str, int)):
                resolved_ids = [resolved_ids]
            if isinstance(resolved_ids, (list, tuple, set)):
                res_set = {str(rid).strip() for rid in resolved_ids if rid is not None and str(rid).strip()}
                for m in memories:
                    if m.get("id") in res_set:
                        m["status"] = "fulfilled"
                        m["resolved_turn"] = turn_idx
                        m["resolved_mid"] = mid
                        m["resolved_elapsed_time"] = copy.deepcopy(elapsed_time) if elapsed_time else None
                        m["updated_at"] = time.time()

        if prepared_items:
            now_ts = time.time()
            for cat, content, new_vec, new_tokens, new_hash in prepared_items:
                if cat in ("event", "promise"):
                    best_recent_sim = -1.0
                    best_recent_idx = -1
                    for idx, existing in enumerate(memories):
                        if existing.get("category") == cat and existing.get("source") == "auto":
                            if cat == "promise" and existing.get("status", "active") != "active":
                                continue
                            ex_turn = existing.get("turn_idx")
                            if turn_idx is not None and ex_turn is not None and abs(turn_idx - ex_turn) <= 1:
                                sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                                if sim > best_recent_sim:
                                    best_recent_sim = sim
                                    best_recent_idx = idx

                    if best_recent_sim >= 0.85 and best_recent_idx >= 0:
                        old_hist = {
                            "turn_idx": memories[best_recent_idx].get("turn_idx"),
                            "elapsed_time": copy.deepcopy(memories[best_recent_idx].get("elapsed_time")),
                            "mid": memories[best_recent_idx].get("mid"),
                            "content": memories[best_recent_idx].get("content"),
                            "tokens": copy.deepcopy(memories[best_recent_idx].get("tokens") or []),
                            "embedding": copy.deepcopy(memories[best_recent_idx].get("embedding") or []),
                            "content_hash": memories[best_recent_idx].get("content_hash") or "",
                            "archived_at": now_ts
                        }
                        memories[best_recent_idx].setdefault("history", []).append(old_hist)
                        memories[best_recent_idx]["content"] = content
                        memories[best_recent_idx]["tokens"] = new_tokens
                        memories[best_recent_idx]["embedding"] = new_vec
                        memories[best_recent_idx]["content_hash"] = new_hash
                        memories[best_recent_idx]["updated_at"] = now_ts
                        memories[best_recent_idx]["turn_idx"] = turn_idx
                        if mid:
                            memories[best_recent_idx]["mid"] = mid
                        if elapsed_time:
                            memories[best_recent_idx]["elapsed_time"] = copy.deepcopy(elapsed_time)
                    else:
                        new_id = "mem_" + uuid.uuid4().hex[:8]
                        memories.append({
                            "id": new_id,
                            "session_id": session_id,
                            "mid": mid,
                            "turn_idx": turn_idx,
                            "elapsed_time": copy.deepcopy(elapsed_time) if elapsed_time else None,
                            "source": "auto",
                            "category": cat,
                            "status": "active",
                            "content": content,
                            "history": [],
                            "content_hash": new_hash,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "tokens": new_tokens,
                            "embedding": new_vec
                        })
                elif cat in ("item_status", "item", "status"):
                    best_sim = -1.0
                    best_idx = -1
                    for idx, existing in enumerate(memories):
                        if existing.get("category") in ("item_status", "item", "status") and existing.get("source") == "auto":
                            if existing.get("status", "active") != "active":
                                continue
                            sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                            if sim > best_sim:
                                best_sim = sim
                                best_idx = idx

                    if best_idx >= 0 and best_sim >= 0.85:
                        old_hist = {
                            "turn_idx": memories[best_idx].get("turn_idx"),
                            "elapsed_time": copy.deepcopy(memories[best_idx].get("elapsed_time")),
                            "mid": memories[best_idx].get("mid"),
                            "content": memories[best_idx].get("content"),
                            "tokens": copy.deepcopy(memories[best_idx].get("tokens") or []),
                            "embedding": copy.deepcopy(memories[best_idx].get("embedding") or []),
                            "content_hash": memories[best_idx].get("content_hash") or "",
                            "archived_at": now_ts
                        }
                        memories[best_idx].setdefault("history", []).append(old_hist)
                        memories[best_idx]["content"] = content
                        memories[best_idx]["tokens"] = new_tokens
                        memories[best_idx]["embedding"] = new_vec
                        memories[best_idx]["content_hash"] = new_hash
                        memories[best_idx]["updated_at"] = now_ts
                        memories[best_idx]["turn_idx"] = turn_idx
                        if mid:
                            memories[best_idx]["mid"] = mid
                        if elapsed_time:
                            memories[best_idx]["elapsed_time"] = copy.deepcopy(elapsed_time)
                    else:
                        new_id = "mem_" + uuid.uuid4().hex[:8]
                        memories.append({
                            "id": new_id,
                            "session_id": session_id,
                            "mid": mid,
                            "turn_idx": turn_idx,
                            "elapsed_time": copy.deepcopy(elapsed_time) if elapsed_time else None,
                            "source": "auto",
                            "category": cat,
                            "status": "active",
                            "content": content,
                            "history": [],
                            "content_hash": new_hash,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "tokens": new_tokens,
                            "embedding": new_vec
                        })
                else:  # calling, relationship, rule, taboo, rule_taboo, char_fact, user_fact
                    best_sim = -1.0
                    best_idx = -1
                    thresh = SEMANTIC_THRESHOLDS.get(cat, 0.70)
                    for idx, existing in enumerate(memories):
                        ex_cat = existing.get("category")
                        cat_matches = (ex_cat == cat) or (cat in ("rule", "taboo", "rule_taboo") and ex_cat in ("rule", "taboo", "rule_taboo"))
                        if cat_matches and existing.get("source") == "auto":
                            if cat in ("rule_taboo", "rule", "taboo") and existing.get("status", "active") != "active":
                                continue
                            sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                            if sim > best_sim:
                                best_sim = sim
                                best_idx = idx

                    if best_idx >= 0 and best_sim >= thresh:
                        old_hist = {
                            "turn_idx": memories[best_idx].get("turn_idx"),
                            "elapsed_time": copy.deepcopy(memories[best_idx].get("elapsed_time")),
                            "mid": memories[best_idx].get("mid"),
                            "content": memories[best_idx].get("content"),
                            "tokens": copy.deepcopy(memories[best_idx].get("tokens") or []),
                            "embedding": copy.deepcopy(memories[best_idx].get("embedding") or []),
                            "content_hash": memories[best_idx].get("content_hash") or "",
                            "archived_at": now_ts
                        }
                        memories[best_idx].setdefault("history", []).append(old_hist)
                        memories[best_idx]["content"] = content
                        memories[best_idx]["tokens"] = new_tokens
                        memories[best_idx]["embedding"] = new_vec
                        memories[best_idx]["content_hash"] = new_hash
                        memories[best_idx]["updated_at"] = now_ts
                        memories[best_idx]["turn_idx"] = turn_idx
                        if mid:
                            memories[best_idx]["mid"] = mid
                        if elapsed_time:
                            memories[best_idx]["elapsed_time"] = copy.deepcopy(elapsed_time)
                    else:
                        new_id = "mem_" + uuid.uuid4().hex[:8]
                        memories.append({
                            "id": new_id,
                            "session_id": session_id,
                            "mid": mid,
                            "turn_idx": turn_idx,
                            "elapsed_time": copy.deepcopy(elapsed_time) if elapsed_time else None,
                            "source": "auto",
                            "category": cat,
                            "status": "active",
                            "content": content,
                            "history": [],
                            "content_hash": new_hash,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "tokens": new_tokens,
                            "embedding": new_vec
                        })

        data["memories"] = memories
        if character_name:
            data["character_name"] = character_name
        data["updated_at"] = time.time()
        save_memories(session_id, data)
        return data


def delete_after_turn(session_id, turn_idx):
    if not session_id or turn_idx is None:
        return
    session_id = str(session_id).strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return
    try:
        turn_idx = int(turn_idx)
    except (ValueError, TypeError):
        return

    with _store_lock:
        data = load_memories(session_id)
        original_memories = data.get("memories", [])
        new_memories = []
        changed = False

        for m in original_memories:
            # 1. If fulfilled promise was resolved after turn_idx, revert status to active
            if m.get("status") == "fulfilled":
                res_turn = m.get("resolved_turn")
                try:
                    res_turn_val = int(res_turn) if res_turn is not None else None
                except (ValueError, TypeError):
                    res_turn_val = None
                if res_turn_val is not None and res_turn_val > turn_idx:
                    m["status"] = "active"
                    m.pop("resolved_turn", None)
                    m.pop("resolved_mid", None)
                    m.pop("resolved_elapsed_time", None)
                    m["updated_at"] = time.time()
                    changed = True

            if m.get("source") != "auto":
                new_memories.append(m)
                continue

            # 2. Check if the memory itself was created or updated after turn_idx
            m_turn = m.get("turn_idx")
            try:
                m_turn_val = int(m_turn) if m_turn is not None else None
            except (ValueError, TypeError):
                m_turn_val = None

            if m_turn_val is not None and m_turn_val > turn_idx:
                history = m.get("history") or []
                valid_hist = []
                for h in history:
                    try:
                        h_turn = int(h.get("turn_idx")) if h.get("turn_idx") is not None else None
                    except (ValueError, TypeError):
                        h_turn = None
                    if h_turn is not None and h_turn <= turn_idx:
                        valid_hist.append(h)

                if valid_hist:
                    restored = valid_hist[-1]
                    m["content"] = restored["content"]
                    m["turn_idx"] = restored.get("turn_idx")
                    m["mid"] = restored.get("mid")
                    m["elapsed_time"] = copy.deepcopy(restored.get("elapsed_time"))
                    m["content_hash"] = restored.get("content_hash") or compute_hash(restored["content"])
                    m["tokens"] = copy.deepcopy(restored.get("tokens")) if restored.get("tokens") else extract_tokens(restored["content"])
                    m["embedding"] = copy.deepcopy(restored.get("embedding")) if restored.get("embedding") else get_embedding(restored["content"])
                    m["history"] = valid_hist[:-1]
                    m["updated_at"] = time.time()
                    new_memories.append(m)
                    changed = True
                else:
                    # Memory was created after turn_idx and has no past history before turn_idx -> drop it
                    changed = True
                    continue
            else:
                new_memories.append(m)

        if changed or len(new_memories) != len(original_memories):
            data["memories"] = new_memories
            data["updated_at"] = time.time()
            save_memories(session_id, data)


def delete_by_mid(session_id, mid):
    if not mid or not session_id:
        return
    session_id = str(session_id).strip()
    mid = str(mid).strip()
    if not session_id or not mid or not all(c.isalnum() or c in "-_" for c in session_id):
        return

    with _store_lock:
        data = load_memories(session_id)
        original_memories = data.get("memories", [])
        new_memories = []
        changed = False

        for m in original_memories:
            # 1. If resolved_mid matches mid, revert to active
            if m.get("status") == "fulfilled" and m.get("resolved_mid") == mid:
                m["status"] = "active"
                m.pop("resolved_turn", None)
                m.pop("resolved_mid", None)
                m.pop("resolved_elapsed_time", None)
                m["updated_at"] = time.time()
                changed = True

            if m.get("source") != "auto":
                new_memories.append(m)
                continue

            # 2. If memory mid matches mid, restore from history if possible
            if m.get("mid") == mid:
                history = m.get("history") or []
                if history:
                    restored = history[-1]
                    m["content"] = restored["content"]
                    m["turn_idx"] = restored.get("turn_idx")
                    m["mid"] = restored.get("mid")
                    m["elapsed_time"] = copy.deepcopy(restored.get("elapsed_time"))
                    m["content_hash"] = restored.get("content_hash") or compute_hash(restored["content"])
                    m["tokens"] = copy.deepcopy(restored.get("tokens")) if restored.get("tokens") else extract_tokens(restored["content"])
                    m["embedding"] = copy.deepcopy(restored.get("embedding")) if restored.get("embedding") else get_embedding(restored["content"])
                    m["history"] = history[:-1]
                    m["updated_at"] = time.time()
                    new_memories.append(m)
                    changed = True
                else:
                    changed = True
                    continue
            else:
                new_memories.append(m)

        if changed or len(new_memories) != len(original_memories):
            data["memories"] = new_memories
            data["updated_at"] = time.time()
            save_memories(session_id, data)


def shift_turn_indices(session_id, after_turn, delta=-1):
    """after_turn より後ろの通算ターン番号を delta だけずらす。
    途中のメッセージを削除してターンが詰まったときに、以降の記憶の番号を合わせるために使う。"""
    if not session_id or after_turn is None or not delta:
        return
    session_id = str(session_id).strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return
    try:
        after_turn = int(after_turn)
        delta = int(delta)
    except (ValueError, TypeError):
        return
    if not delta:
        return

    def _shift(v):
        try:
            n = int(v)
        except (ValueError, TypeError):
            return v
        return max(0, n + delta) if n > after_turn else n

    with _store_lock:
        data = load_memories(session_id)
        changed = False
        for m in data.get("memories", []):
            if m.get("turn_idx") is not None:
                nv = _shift(m.get("turn_idx"))
                if nv != m.get("turn_idx"):
                    m["turn_idx"] = nv
                    changed = True
            if m.get("resolved_turn") is not None:
                nv = _shift(m.get("resolved_turn"))
                if nv != m.get("resolved_turn"):
                    m["resolved_turn"] = nv
                    changed = True
            for h in m.get("history") or []:
                if isinstance(h, dict) and h.get("turn_idx") is not None:
                    nv = _shift(h.get("turn_idx"))
                    if nv != h.get("turn_idx"):
                        h["turn_idx"] = nv
                        changed = True
        if changed:
            data["updated_at"] = time.time()
            save_memories(session_id, data)


def add_manual_memory(session_id, character_name, category, content, elapsed_time=None, status="active"):
    content = (content or "").strip()
    if not content:
        raise ValueError("content is required")
    category = CATEGORY_ALIASES.get((category or "").strip().lower(), (category or "").strip())
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category: {category}")

    status = (status or "active").strip().lower()
    if status not in ("active", "fulfilled"):
        status = "active"

    if elapsed_time is None and session_id:
        try:
            sess_path = os.path.join(DATA, "sessions", f"{session_id}.json")
            if os.path.exists(sess_path):
                with open(sess_path, encoding="utf-8-sig") as sf:
                    s_data = json.load(sf)
                    elapsed_time = copy.deepcopy(s_data.get("setting", {}).get("elapsed_time"))
        except Exception:
            pass
    if elapsed_time is None:
        elapsed_time = {"day": 1, "period": "午前"}

    tokens = extract_tokens(content)
    vec = get_embedding(content)
    h = compute_hash(content)
    now_ts = time.time()
    new_id = "mem_" + uuid.uuid4().hex[:8]

    new_mem = {
        "id": new_id,
        "session_id": session_id,
        "mid": None,
        "turn_idx": None,
        "elapsed_time": copy.deepcopy(elapsed_time),
        "source": "manual",
        "category": category,
        "status": status,
        "content": content,
        "history": [],
        "content_hash": h,
        "created_at": now_ts,
        "updated_at": now_ts,
        "tokens": tokens,
        "embedding": vec
    }

    with _store_lock:
        data = load_memories(session_id, character_name)
        data["memories"].append(new_mem)
        if character_name:
            data["character_name"] = character_name
        save_memories(session_id, data)

    return new_mem


def update_memory(session_id, mem_id, category=None, content=None, status=None):
    if not session_id or not mem_id:
        raise ValueError("session_id and mem_id are required")
    new_tokens = None
    new_vec = None
    new_hash = None
    if content is not None:
        content = content.strip()
        if not content:
            raise ValueError("content cannot be empty")
        new_hash = compute_hash(content)
        new_tokens = extract_tokens(content)
        new_vec = get_embedding(content)

    if category is not None:
        category = CATEGORY_ALIASES.get(category.strip().lower(), category.strip())
        if category not in VALID_CATEGORIES:
            raise ValueError(f"invalid category: {category}")

    with _store_lock:
        data = load_memories(session_id)
        mem = next((m for m in data.get("memories", []) if m.get("id") == mem_id), None)
        if not mem:
            raise KeyError("no such memory")

        if category is not None:
            mem["category"] = category

        if content is not None:
            mem["content"] = content
            mem["content_hash"] = new_hash
            mem["tokens"] = new_tokens
            mem["embedding"] = new_vec

        if status is not None:
            status = status.strip().lower()
            if status in ("active", "fulfilled"):
                mem["status"] = status
                if status == "fulfilled":
                    mem["resolved_at"] = time.time()
                elif "resolved_at" in mem:
                    del mem["resolved_at"]

        # Promoted to manual edit (protected from revert)
        mem["source"] = "manual"
        mem["updated_at"] = time.time()
        save_memories(session_id, data)
        return mem


def delete_memory(session_id, mem_id):
    with _store_lock:
        data = load_memories(session_id)
        orig_len = len(data.get("memories", []))
        data["memories"] = [m for m in data.get("memories", []) if m.get("id") != mem_id]
        if len(data["memories"]) == orig_len:
            raise KeyError("no such memory")
        save_memories(session_id, data)
        return True


def clear_auto_memories(session_id):
    with _store_lock:
        path = memory_path(session_id)
        if os.path.exists(path):
            os.makedirs(TRASH, exist_ok=True)
            trash_path = os.path.join(TRASH, f"{session_id}_{int(time.time())}_memories.json")
            try:
                shutil.copy2(path, trash_path)
            except Exception as e:
                print(f"[memory_store] error backing up memories to trash: {e}")
        data = load_memories(session_id)
        new_manual = []
        for m in data.get("memories", []):
            if m.get("source") == "manual":
                if m.get("status") == "fulfilled":
                    m["status"] = "active"
                    m.pop("resolved_turn", None)
                    m.pop("resolved_mid", None)
                    m.pop("resolved_elapsed_time", None)
                    m["updated_at"] = time.time()
                new_manual.append(m)
        data["memories"] = new_manual
        save_memories(session_id, data)
        return data


def format_prompt_memories(memories, current_turn, current_elapsed_time=None):
    if not memories:
        return ""

    tier1 = [m for m in memories if m.get("_tier") == 1]
    tier2 = [m for m in memories if m.get("_tier") != 1]

    def _format_single(mem):
        cat_label = CATEGORY_LABELS.get(mem.get("category"), "記憶")
        if mem.get("status") == "fulfilled":
            cat_label += "（達成/解消済）"

        el_time = mem.get("elapsed_time")
        if el_time and isinstance(el_time, dict):
            m_day = el_time.get("day", 1)
            m_period = el_time.get("period", "")
            cur_day = current_elapsed_time.get("day") if (current_elapsed_time and isinstance(current_elapsed_time, dict)) else None
            if cur_day is not None and m_day is not None:
                diff_d = cur_day - m_day
                if diff_d == 0:
                    rel_str = "今日"
                elif diff_d == 1:
                    rel_str = "1日前"
                elif diff_d > 1:
                    rel_str = f"{diff_d}日前"
                else:
                    rel_str = ""
                turn_text = f"（{m_day}日目・{m_period}（{rel_str}））" if rel_str else (f"（{m_day}日目・{m_period}）" if m_period else f"（{m_day}日目）")
            elif m_period:
                turn_text = f"（{m_day}日目・{m_period}）"
            else:
                turn_text = f"（{m_day}日目）"
        elif el_time and isinstance(el_time, str) and el_time.strip():
            turn_text = f"（{el_time.strip()}）"
        elif mem.get("_is_other"):
            turn_text = "（重要記憶）"
        elif mem.get("turn_idx") is not None and current_turn is not None:
            diff = max(0, current_turn - (mem.get("turn_idx") or 0))
            turn_text = f"（約{diff}ターン前）"
        else:
            turn_text = "（重要記憶）"
        return f"- [ID: {mem['id']} | {cat_label}] {mem['content']} {turn_text}"

    sections = []
    if tier1:
        t1_lines = [
            "【作中の前提・制約（常時確認・遵守事項）】",
            "※以下は現在の呼び方・ルール・所持品・身体状態など、現在の状況の前提となる確定事項です。ロールプレイ中常に遵守してください。"
        ]
        for m in tier1:
            t1_lines.append(_format_single(m))
        sections.append("\n".join(t1_lines))

    if tier2:
        t2_lines = [
            "【過去の記憶・約束（関連する記録）】",
            "※以下は過去の会話からあなたが思い出した内容です。無理にすべてに触れる必要はありませんが、",
            "話の流れに合わせて自然に言及・引用し、過去の出来事や約束を覚えているキャラクターとして振る舞ってください。",
            "（※記憶IDは内部管理用のため、セリフ内には絶対に出力しないでください）\n"
        ]
        for m in tier2:
            t2_lines.append(_format_single(m))
        sections.append("\n".join(t2_lines))

    return "\n\n".join(sections)
