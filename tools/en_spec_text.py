# -*- coding: utf-8 -*-
"""Test A 用の英語版 spec（diff × compact 分岐のみ）。

server.output_text(st, is_diff=True) の compact 分岐（server.py 2392-2445 付近）を
英語化したドラフト。原形維持する要素（JSON サンプル値・enum・なし・Danbooru タグ例・
「応答スタイル」「固定キャラ一覧」「指示」等の参照ラベル）はそのまま残している。

日本語は 1 文字 3 バイトだが情報密度が高いため、直訳ではバイトが増える。
ここでは記事・前置詞・重複を削った電文調にして、JP spec より小さくしている。
本実装時は server.py 側へ移す前提の計測用モジュール。
"""
import json


def en_output_text(st, is_diff=False):
    if not is_diff:
        raise ValueError("en_output_text は is_diff=True の計測専用")

    mode = st.get("image_mode", "always")
    inline_choices = bool(st.get("suggest_choices", True) and not _has_parallel_choices_ide(st))

    schema = {
        "session": "<session>",
        "reply_to": "<reply_to>",
        "text": "ロールプレイ本文（末尾の「応答スタイル」に従う）",
    }
    if inline_choices:
        schema["choices"] = [{"label": "行動・発言の要約", "text": "「短いセリフ」＋具体的なアクション"}]
    if mode != "off":
        schema["image"] = {"characters": ["キャラ名"]}
    if st.get("rp_state"):
        schema["state"] = [{
            "name": "キャラ名",
            "pose": "今の体勢",
            "expression": ["今の表情"],
            "situation": ["状況・体調"],
            "inner": "内心",
        }]
    schema["memories"] = [{
        "category": "rule",
        "content": "キャラ名は…という決まりを守る（三人称1文）",
    }]
    sample_json = json.dumps(schema, ensure_ascii=False, separators=(",", ": "))

    must = ["text"]
    if st.get("rp_state"):
        must.append("state")
    if mode != "off":
        must.append("image")

    lines = [
        "Write UTF-8 JSON to the wait_input.py outbox (session/reply_to as-is). Once; no duplicate.",
        "```json", sample_json, "```",
        f"Required: {'/'.join(must)}. Optional: memories/memo/elapsed_time/resolved_ids/memory_query.",
    ]
    if inline_choices:
        lines.append("- choices: 8 natural player replies (label <=10 chars, text:「短いセリフ」＋具体的な行動).")
    if st.get("rp_state"):
        lines.append(
            "- text: one fixed 1st-person POV; others+user 3rd person; never the user's inner"
            " thoughts or unseen events. Inner voice (thoughts, tsukkomi), not objective narration."
            " No meta. <=300 JP chars, max 600 in action/climax (channel「指示」 wins)."
            " Dialogue count/order: follow「応答スタイル」."
        )
    else:
        lines.append(
            "- text: one fixed 1st-person POV; others+user 3rd person. Inner voice (thoughts,"
            " tsukkomi), not objective narration. No meta. <=300 JP chars, max 600"
            " (channel「指示」 wins). Dialogue count/order: follow「応答スタイル」."
        )
    lines.append(
        "- memories: lasting rule added by user (hypnosis/suggestion/imprint,「〜するようになる」"
        "「〜は常識」) → always category rule, one 3rd-person sentence, char as subject."
        " Also names/promises/items/traces/relationships. Else omit."
    )
    lines.append(
        f"- Optional: memo (full; every {_memo_turns()} turns/major scene change), elapsed_time"
        " (only when period advances; narrate, not dialogue), resolved_ids (done/cleared mem IDs),"
        " memory_query (recall keywords)."
    )
    if st.get("rp_state"):
        lines.append(
            "- state: pose+expression every turn; background+camera only when changed, 3 each;"
            " outfit/outerwear/underwear/equipped_items/action/body_fluids only changed keys"
            " (omit=keep). IMPORTANT: overwrite ended/removed with「なし」(equipped_items: [])."
            " Danbooru English tags. penis_present only if male genitalia visible."
        )
    if mode == "off":
        lines.append("- No image or characters.")
    else:
        lines.append(
            "- image: image.characters every turn (visible fixed names, same spelling as"
            " 固定キャラ一覧). Never image.prompt (server builds it from state). characters only on"
            " first appearance/immutable change."
        )
    lines.append(
        "- Language: Japanese for text/state.inner/state.situation/memo/memories.content;"
        " keep「」and「なし」as-is."
    )
    return "\n".join(lines)


def _memo_turns():
    import server
    return server.MEMO_REMIND_TURNS


def _has_parallel_choices_ide(st):
    import server
    return server.has_parallel_choices_ide(st)
