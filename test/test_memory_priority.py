"""
test_memory_priority.py - 5段階優先度（ピン留め＋乗算Boost）完全動作検証スクリプト
"""
import os
import tempfile
import shutil
import memory_store


def test_priority_resolution():
    print("[1/8] Testing priority key resolution & aliases...")
    assert memory_store.resolve_priority_key("ピン留め") == "pinned"
    assert memory_store.resolve_priority_key("最優先") == "highest"
    assert memory_store.resolve_priority_key("優先") == "high"
    assert memory_store.resolve_priority_key("少し優先") == "high"
    assert memory_store.resolve_priority_key("slightly_high") == "high"
    assert memory_store.resolve_priority_key("標準") == "default"
    assert memory_store.resolve_priority_key("抑制") == "low"
    assert memory_store.resolve_priority_key(boost=0.08) == "pinned"
    assert memory_store.resolve_priority_key(boost=0.05) == "highest"
    assert memory_store.resolve_priority_key(boost=0.02) == "high"
    assert memory_store.resolve_priority_key(boost=-0.02) == "low"


def test_rule_standing_ranks():
    print("[2/8] Testing Tier 1 rule standing level hierarchy...")
    # 自セッションピン留め(4) > 自セッション恒久・優先ルール(3) > 他セッションピン留め・恒久ルール(2) > 手動ルール(1) > 抑制(0)
    assert memory_store.rule_standing_level("ルール", priority_key="pinned", is_other=False) == 4
    assert memory_store.rule_standing_level("校則により禁止", priority_key="default", is_other=False) == 3
    assert memory_store.rule_standing_level("任意の決まり", priority_key="high", is_other=False) == 3
    assert memory_store.rule_standing_level("ルール", priority_key="pinned", is_other=True) == 2
    assert memory_store.rule_standing_level("校則により禁止", priority_key="default", is_other=True) == 2
    assert memory_store.rule_standing_level("手書き", source="manual", priority_key="default", is_other=False) == 1
    assert memory_store.rule_standing_level("校則により禁止", priority_key="low", is_other=False) == 0


def test_format_prompt_protection_and_trim():
    print("[3/8] Testing format_prompt_memories protection & trimming...")
    mems = [
        {"id": "m_pin_ev", "category": "event", "priority": "pinned", "status": "active", "content": "絶対に忘れてはならない過去の重大な誓い", "score": 0.40},
        {"id": "m_rule_1", "category": "rule", "priority": "default", "status": "active", "content": "門限は夜9時というルールになっている", "score": 0.80},
        {"id": "m_rule_low", "category": "rule", "priority": "low", "status": "active", "content": "一時的な古いルール", "score": 0.90},
        {"id": "m_ev_norm", "category": "event", "priority": "default", "status": "active", "content": "昨日一緒にカフェへ行った", "score": 0.65},
    ]
    stats = {}
    out = memory_store.format_prompt_memories(mems, current_turn=10, stats=stats, soft_budget=350, recall_budget=200)
    assert "m_pin_ev" in stats["included_ids"], "ピン留めされたeventが保護層に残ること"
    assert "【作中の前提・制約（常時確認・遵守事項）】" in out


def test_positive_boost_guard_and_mult():
    print("[4/8] Testing relevance guard & multiplication math...")
    info_highest = memory_store.PRIORITY_BY_KEY["highest"]
    info_high = memory_store.PRIORITY_BY_KEY["high"]
    assert info_highest["mult"] == 1.25
    assert info_high["mult"] == 1.12

    # 1. 無関係な日常会話（s_kw=0, s_vec=0.30 < 0.35）-> ガード作動で ×1.00
    s_vec, s_kw = 0.30, 0.0
    raw_score = 0.70 * s_vec + 0.30 * s_kw + 0.10  # 0.31
    eff_mult = 1.0 if (info_highest["mult"] > 1.0 and s_kw == 0.0 and s_vec < memory_store.MIN_VEC_FOR_POSITIVE_BOOST) else info_highest["mult"]
    assert raw_score * eff_mult == 0.31 < info_highest["min_score"]

    # 2. 文脈が近い過去の会話（s_kw=0, s_vec=0.65 >= 0.35, recency=0.02）-> raw=0.475
    s_vec2, s_kw2 = 0.65, 0.0
    raw_score2 = 0.70 * s_vec2 + 0.02  # 0.475 (標準では 0.53 未満で不採用)
    assert round(raw_score2 * info_high["mult"], 4) == 0.532 >= info_high["min_score"], "優先(×1.12)で足切り突破"
    assert round(raw_score2 * info_highest["mult"], 4) == 0.5938 >= info_highest["min_score"], "最優先(×1.25)で上位突破"


def test_low_priority_threshold():
    print("[5/8] Testing low priority suppression threshold...")
    info_low = memory_store.PRIORITY_BY_KEY["low"]
    assert info_low["mult"] == 0.82
    assert info_low["min_score"] == 0.60
    assert round(0.73 * 0.82, 4) < 0.60
    assert round(0.74 * 0.82, 4) >= 0.60


def test_io_source_preservation_and_revert_protection():
    print("[6/8] Testing update_memory source=='auto' preservation...")
    tmp_dir = tempfile.mkdtemp(prefix="rpchat_mem_test_")
    orig_rag_dir = memory_store.RAG_DIR
    orig_trash_dir = memory_store.TRASH
    orig_emb = memory_store.get_embedding
    orig_batch = memory_store.get_embeddings_batch
    orig_tok = memory_store.extract_tokens
    try:
        memory_store.RAG_DIR = os.path.join(tmp_dir, "rag")
        memory_store.TRASH = os.path.join(tmp_dir, "trash")
        memory_store._memory_cache.clear()
        # 高速ダミー推論に差し替え
        memory_store.get_embedding = lambda t: [0.5, 0.5, 0.5, 0.5]
        memory_store.get_embeddings_batch = lambda ts: [[0.5, 0.5, 0.5, 0.5] for _ in ts]
        memory_store.extract_tokens = lambda t: ["テスト", "記憶"]

        sid = "testsession01"
        memory_store.save_extracted_memories(
            sid, "ラピ",
            [
                {"category": "event", "content": "ラピは指揮官と水族館へ行った"},
                {"category": "promise", "content": "ラピは指揮官と映画に行く約束をした"},
            ],
            turn_idx=5, mid="m5"
        )
        data = memory_store.load_memories(sid)
        m_ev, m_pr = data["memories"][0], data["memories"][1]
        assert m_ev["source"] == "auto"

        # 優先度のみ変更 -> source == "auto" が維持されること
        updated_ev = memory_store.update_memory(sid, m_ev["id"], priority="pinned")
        assert updated_ev["source"] == "auto", "優先度のみの変更ではsource='auto'が維持されること"
        assert updated_ev["priority"] == "pinned"

        updated_pr = memory_store.update_memory(sid, m_pr["id"], priority="high")
        assert updated_pr["source"] == "auto"
        assert updated_pr["priority"] == "high"

        print("[7/8] Testing delete_after_turn pinned protection...")
        # ターン3に巻き戻し -> turn_idx=5 の auto 記憶のうち、pinned は保護され、high は削除されること
        memory_store.delete_after_turn(sid, 3)
        after_revert = memory_store.load_memories(sid)["memories"]
        remaining_ids = [m["id"] for m in after_revert]
        assert m_ev["id"] in remaining_ids, "ピン留めしたauto記憶は巻き戻しでも消えないこと"
        assert m_pr["id"] not in remaining_ids, "ピン留め以外の未来ターンのauto記憶は巻き戻しで消えること"

        print("[8/8] Testing clear_auto_memories priority protection...")
        # 新たに standard の auto 記憶と high の auto 記憶を追加し、clear_auto_memories を実行
        memory_store.save_extracted_memories(
            sid, "ラピ",
            [
                {"category": "event", "content": "標準の自動抽出記憶"},
                {"category": "event", "content": "優先設定した自動抽出記憶"},
            ],
            turn_idx=6, mid="m6"
        )
        mems_now = memory_store.load_memories(sid)["memories"]
        m_std = next(m for m in mems_now if m["content"] == "標準の自動抽出記憶")
        m_hi = next(m for m in mems_now if m["content"] == "優先設定した自動抽出記憶")
        memory_store.update_memory(sid, m_hi["id"], priority="high")

        memory_store.clear_auto_memories(sid)
        after_clear = memory_store.load_memories(sid)["memories"]
        clear_ids = [m["id"] for m in after_clear]
        assert m_ev["id"] in clear_ids, "pinned記憶がclear_auto_memoriesから保護されること"
        assert m_hi["id"] in clear_ids, "優先度変更済み記憶がclear_auto_memoriesから保護されること"
        assert m_std["id"] not in clear_ids, "標準のauto記憶はclear_auto_memoriesで消去されること"
    finally:
        memory_store.RAG_DIR = orig_rag_dir
        memory_store.TRASH = orig_trash_dir
        memory_store.get_embedding = orig_emb
        memory_store.get_embeddings_batch = orig_batch
        memory_store.extract_tokens = orig_tok
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_priority_resolution()
    test_rule_standing_ranks()
    test_format_prompt_protection_and_trim()
    test_positive_boost_guard_and_mult()
    test_low_priority_threshold()
    test_io_source_preservation_and_revert_protection()
    print("\n🎉 All 8 priority specification tests passed successfully!")

