"""長期記憶（RAG）のキーワード抽出（server.py から切り出し）"""
import os
import re

import memory_store

# server.py の ROTATION_TURNS - ROTATION_KEEP_TURNS と同値。
# server.py 側が起動時に実値を代入するので、通常はこの既定値は使われない。
ROTATION_PERIOD = 20


def extract_matching_sentences(text, keywords, max_len=120):
    if not text or not keywords:
        return []
    # セリフ（「」など）の中では区切らない（区切ると引用の途中で切れて、誰の発言か分からない断片になる）
    sentences = []
    curr = ""
    depth = 0
    for ch in text:
        curr += ch
        if ch in "「『“":
            depth += 1
        elif ch in "」』”":
            depth = max(0, depth - 1)
        if ch in "。！？!?\n" and depth == 0:
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
    """ユーザー発言・アシスタント発言から8カテゴリの重要情報をキーワード抽出する。
    主語（誰が）が文に書かれていなくても、話者（ユーザー／キャラ）から補って三人称文にする。
    セリフだけの断片は状態・所持品（item/status）には使わない（state が持っている情報のため）。
    """
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

    user_label = (user_name or "").strip()
    if user_label in ("", "あなた", "君", "きみ"):
        user_label = "ユーザー"
    char_label = (char_name or "").strip() or "キャラクター"

    QUOTE_OPEN = ("「", "『", "“", '"')
    QUOTE_CLOSE = ("」", "』", "”", '"')

    def attribute(sentence, actor, category):
        """主語を補って三人称の文にする。記録に使えない文なら None を返す"""
        s = (sentence or "").strip()
        if not s:
            return None
        starts_q = s[:1] in QUOTE_OPEN
        ends_q = s[-1:] in QUOTE_CLOSE
        unclosed = s.count("「") > s.count("」")
        # セリフ（断片含む）は「状態・所望品」の記録には向かない（直近ログと state が持っている）
        if category in ("item", "status") and (starts_q or ends_q):
            return None
        if starts_q and ends_q:
            # 文全体がセリフ → 「と言った」で事実文にする
            return f"{actor}は{s}と言った"
        if unclosed:
            # 中で切れたセリフを閉じる。セリフから始まる文なら発話として扱う
            s += "」"
            if starts_q:
                return f"{actor}は{s}と言った"
        # 接続詞・副詞で始まる続きの断片は、落としてから主語を補う（「ダクネスはけれど、…」を防ぐ）
        s = re.sub(r"^(でも|けれども|けれど|しかし|だが|だけど|そして|すると|そこで|また|さらに|なお|つまり|要するに|ところで|ちなみに)[、,]\s*", "", s)
        if not s:
            return None
        if any(name in s for name in valid_names):
            return s
        # 「私は/僕は…」の主語を話者名に置き換える（文中でも最初の1つだけ）
        m = re.search(r"(私|僕|俺|あたし|わたし|わたくし|自分)(は|が|も|の)", s)
        if m:
            return s[:m.start()] + actor + s[m.start() + len(m.group(1)):]
        return f"{actor}は{s}"

    content_index = {}  # content -> extracted のインデックス

    def add(cat, sentence, actor):
        content = attribute(sentence, actor, cat)
        if not content:
            return
        key = (cat, content)
        if key in added_pairs:
            return
        added_pairs.add(key)
        prev = content_index.get(content)
        if prev is not None:
            # 同じ文が別カテゴリで取れたときは優先度の高い方だけ残す（例: 約束と好みの両方に当たる）
            prev_cat = extracted[prev]["category"]
            if memory_store.MEMORY_PRIORITY.get(cat, 9) < memory_store.MEMORY_PRIORITY.get(prev_cat, 9):
                extracted[prev]["category"] = cat
            return
        content_index[content] = len(extracted)
        extracted.append({"category": cat, "content": content})

    # 1. calling (呼称・口調)
    #    単なる呼びかけ（お兄ちゃん・様）や「お呼びしました（招致）」の誤爆を防ぐ。
    #    引用助詞を伴う表現、または呼称・口調を明示するメタ語だけを拾う。
    user_calling_kws = memory_store.CALLING_KEYWORDS
    asst_calling_kws = memory_store.CALLING_KEYWORDS
    for s in extract_matching_sentences(user_text, user_calling_kws):
        add("calling", s, user_label)
    for s in extract_matching_sentences(asst_text, asst_calling_kws):
        add("calling", s, char_label)

    # 2. rule (ルール・禁忌)
    # ※「しなければならない」「してはいけない」は地の文（“声を出してはいけないのに…”等）に
    #   必ずヒットし、一過性の情景描写が rule として常時枠を埋める原因になるので入れない（v1.2）
    rule_kws = ("校則", "禁止", "門限", "ルール", "タブー", "掟", "規則", "約束事", "地雷", "絶対ダメ", "絶対だめ", "二人だけの秘密", "二人の秘密", "内緒", "誰にも言", "秘密にして", "秘密ね",
                "常識で当たり前", "当たり前のこと", "決まり事", "暗黙のルール")
    for text, actor, from_user in ((user_text, user_label, True), (asst_text, char_label, False)):
        for s in extract_matching_sentences(text, rule_kws):
            # ユーザーの命令文はそのまま、地の文（“〜のに”“〜てしまう”）は拾わない
            if memory_store.should_skip_rule_extraction(s, from_user=from_user):
                continue
            add("rule", s, actor)

    # 2b. 催眠・暗示（このチャットで後から追加される「永久に効く決まり」）
    # ユーザーの催眠命令はそのまま、キャラ側は「決まりとして語られている」文だけ拾う
    # （「催眠に支配された身体は…」のような情景描写まで拾うと、常時枠がノイズで埋まる）
    hypnosis_kws = ("催眠", "暗示", "刷り込み", "洗脳", "マインドコントロール")
    directive_kws = ("刻まれ", "刷り込まれ", "刷り込む", "書き換え", "常識", "絶対", "命令",
                     "なさい", "ようになる", "義務", "ルール", "決まり", "服従", "当たり前")
    for text, actor, from_user in ((user_text, user_label, True), (asst_text, char_label, False)):
        for s in extract_matching_sentences(text, hypnosis_kws):
            if memory_store.should_skip_rule_extraction(s, from_user=from_user):
                continue   # 「暗示が身体を支配し…止められない」等の反応描写は決まりではない
            if from_user or any(k in s for k in directive_kws):
                add("rule", s, actor)

    # 3. item (アイテム・所持品) / status (状態・痕跡) - 一時的な衣装・下着・リボン等は除外
    item_kws = (
        "プレゼント", "合鍵", "指輪", "ネックレス", "ペンダント", "ブローチ", "アンクレット",
        "首輪", "アナルプラグ", "プラグ", "拘束具", "バイブ", "おもちゃ", "玩具",
        "受け取って", "受け取る", "あげた", "あげるよ", "あげるね", "もらった", "お守り", "ピアス", "ブレスレット"
    )
    trace_kws = ("キスマーク", "傷跡", "傷", "痕", "跡")   # 体に残る痕跡は status
    temp_clothing_kws = ("下着", "ショーツ", "パンツ", "パンティ", "ブラジャー", "ブラ", "リボン", "衣装", "服", "レース")
    persistent_item_kws = ("合鍵", "指輪", "ネックレス", "首輪", "アナルプラグ", "プラグ", "拘束具", "キスマーク", "傷跡", "お守り", "ピアス", "ブレスレット", "ペンダント")

    for text, actor in ((user_text, user_label), (asst_text, char_label)):
        for s in extract_matching_sentences(text, trace_kws):
            add("status", s, actor)
        for s in extract_matching_sentences(text, item_kws):
            if any(cw in s for cw in temp_clothing_kws) and not any(pw in s for pw in persistent_item_kws):
                continue
            if any(tw in s for tw in ("キスマーク", "傷跡")):   # 物ではなく痕跡の文
                continue
            add("item", s, actor)

    # 4. promise (約束・予定)
    promise_kws = ("約束", "予定", "誓約", "誓い", "誓う", "行こう", "会おう", "行く約束", "会う約束", "一緒に行", "一緒にいよう", "デートしよう")
    for text, actor in ((user_text, user_label), (asst_text, char_label)):
        for s in extract_matching_sentences(text, promise_kws):
            add("promise", s, actor)

    # 5. char_fact (キャラの秘密・過去・弱点)
    char_kws = ("トラウマ", "弱点", "コンプレックス", "生い立ち", "体質", "私の秘密", "秘密がある", "過去の傷", "実は昔")
    for s in extract_matching_sentences(asst_text, char_kws):
        add("char_fact", s, char_label)
    # Contextual char_fact: '実は' or '本当は' + personal facts
    for s in extract_matching_sentences(asst_text, ("実は", "本当は")):
        if any(w in s for w in ("苦手", "好き", "秘密", "怖い", "怖く", "寂し", "昔", "家族", "両親", "病気", "体質", "弱点", "隠して", "本当は", "コンプレックス", "生い立ち")):
            add("char_fact", s, char_label)

    # 6. user_fact (ユーザーの好み・個人情報・秘密)
    user_kws = ("好き", "嫌い", "苦手", "趣味", "タイプ", "誕生日", "飼って", "飼い", "飼う", "ペット", "好み", "好物", "俺の秘密", "僕の秘密", "私の秘密")
    for s in extract_matching_sentences(user_text, user_kws):
        add("user_fact", s, user_label)

    # 7. relationship (関係性の変化)
    rel_kws = ("恋人", "付き合", "付き合う", "付き合おう", "仲直り", "信頼", "親密", "両思い", "告白", "愛してる", "愛して", "大切にする", "特別", "大好き", "好きだよ", "好きです", "好きだった", "ずっと一緒", "彼氏", "彼女", "結婚")
    for text, actor in ((user_text, user_label), (asst_text, char_label)):
        for s in extract_matching_sentences(text, rel_kws):
            add("relationship", s, actor)

    # 8. event (重要出来事)
    event_kws = ("初めて", "告白", "助けた", "助けて", "救った", "救って", "出会った", "記念", "事件", "雨宿り", "旅行", "お泊まり", "デート")
    for text, actor in ((user_text, user_label), (asst_text, char_label)):
        for s in extract_matching_sentences(text, event_kws):
            add("event", s, actor)

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

# server.py 側の `from memory_extract import *` で再エクスポートする名前
__all__ = [
    'extract_matching_sentences', 'extract_memories_from_text', 'extract_memories_fallback',
]
