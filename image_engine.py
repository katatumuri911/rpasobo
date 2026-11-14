"""画像タグ辞書・プロンプトのサニタイズ・POV・固定キャラ・state整形・ComfyUI ワークフロー（server.py から切り出し）"""
import json
import os
import re
import sys
import threading
import time
import uuid

import memory_store

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
CHAR_PRESETS = os.path.join(DATA, "char-presets.json")

# DATA / TAG_DICT_PATH はテストが server.DATA / server.TAG_DICT_PATH を差し替えるので、
# 値のコピーではなく「呼び出し時に最新値を返す関数」を server.py から注入できるようにする。
_data_ref = None
_tag_dict_path_ref = None


def bind_paths(data=None, tag_dict_path=None):
    global _data_ref, _tag_dict_path_ref
    if data is not None:
        _data_ref = data
    if tag_dict_path is not None:
        _tag_dict_path_ref = tag_dict_path


def data_dir():
    return _data_ref() if _data_ref else DATA


def tag_dict_path():
    return _tag_dict_path_ref() if _tag_dict_path_ref else TAG_DICT_PATH


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id():
    return uuid.uuid4().hex[:10]


def compact_on():
    return os.environ.get("RPCHAT_COMPACT", "1") != "0"


def read_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


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


def state_tag_guidelines_text():
    """full モード時に注入する state（pose / action / expression / background / camera）用の
    Danbooru 実在タグ変換規則（約1.7KB）。
    image_prompt_guidelines_text（13,131バイト）の『AIが image.prompt を直接書く』という
    旧仕様の記述を排し、ポーズ・装具・排泄等の変換表のみを提供する。"""
    return (
        "【state（pose / action / expression / background / camera）のDanbooruタグ変換規則】\n"
        "- image.prompt はサーバーが state から自動生成するため出力不要（image.characters のみ指定）。\n"
        "- pose / action / expression / background / camera は造語や日本語を避け、実在の Danbooru 英語タグで記述すること（outfit / outerwear / underwear / equipped_items / body_fluids / situation / inner は日本語で記述）。\n"
        "- 体勢(pose): I字バランス→standing split, leg up / M字開脚→spread legs, knees up, sitting / V字バランス→sitting, legs up / 四つん這い→all fours / 逆立ち→handstand / うつ伏せ→lying, on stomach / 仰向け→lying, on back / 正座→seiza / 片足立ち→standing on one leg / お姫様抱っこ→princess carry\n"
        "- 行為・装具・排泄(action): 口淫→fellatio, deepthroat / 挿入→vaginal penetration, anal penetration / アナルゼリー→lubricant, anal, anal object insertion / 浣腸→enema, anal object insertion / アナルビーズ→anal beads / アナルプラグ→butt plug / バイブ→vibrator / 緊縛→bondage, rope / 目隠し→blindfold / 手錠→handcuffs / 口枷→gag / 放尿→peeing, urine / 脱糞→feces, pooping\n"
        "- 解剖・終了処理: 露出・接触中の部位（pussy, anus, erect nipples）は action 等に反映し、画面内に男性器が写る時のみ penis_present: true にする。終了した行為・脱いだ服・外した装具は必ず「なし」（equipped_items は []）で上書きすること。"
    )


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
    (("m字開脚", "m字"), ("m legs", "spread legs", "knees up", "sitting")),
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
        p = os.path.join(data_dir(), "item_tag_misses.log")
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
        p = os.path.join(data_dir(), "state_tag_misses.log")
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


# ---------------------------------------------------------------- 本文・チャットの未登録語収集
# state は英語タグで書く決まりなので、辞書拡張に使えるのは本文とユーザー発言の日本語。
# 辞書キーに一致しない名詞だけを text_tag_misses.log に記録する。
_text_miss_lock = threading.Lock()


_TAG_KEY_CACHE = None


def _text_miss_log_path():
    return os.path.join(data_dir(), "text_tag_misses.log")


_TEXT_MISS_STOPWORDS = {
    "私", "僕", "俺", "あたし", "あなた", "君", "彼", "彼女", "自分", "誰", "何",
    "これ", "それ", "あれ", "ここ", "そこ", "あそこ", "こちら", "そちら", "あちら",
    "今回", "前回", "次回", "今日", "昨日", "明日", "今", "時", "前", "後", "中", "上", "下",
    "感じ", "気持ち", "表情", "顔", "目", "手", "声", "身体", "体", "心", "頭",
    "言葉", "話", "事", "こと", "もの", "ため", "よう", "さん", "ちゃん", "くん",
    "笑顔", "涙", "汗", "息", "胸", "脚", "足", "腕", "指", "肌", "髪",
    "する", "なる", "いる", "ある", "行く", "来る", "見る", "思う", "言う", "感じる",
    "思える", "見える", "聞こえる", "できる", "しまう", "くれる", "もらう", "あげる",
    "いい", "よい", "ない", "すごい", "とても", "少し", "もう", "まだ",
    # 本文に頻出する一般語（タグになりにくいもの）
    "指揮", "近く", "大量", "瞬間", "温もり", "一滴", "必死", "世界", "次", "火", "異常", "変",
    "気分", "用意", "感触", "刺激", "我慢", "限界", "力", "検査", "左右", "格好", "朝", "午前",
    "綺麗", "任務", "準備", "無防備", "今度", "直接", "衝撃", "回路", "真っ白", "両方", "底", "全部",
    "通り", "想い", "嵐", "背骨", "痺れ", "余裕", "波", "速度", "威力", "最奥", "強烈", "電流",
    "仕方", "問題", "合格", "毎日", "小指", "炭酸", "悲鳴", "反応", "容赦", "お昼", "噴出", "勢い",
    "解放", "不公平", "指先", "圧", "立派", "従順", "幸福", "褒美", "合間", "手のひら", "強打", "腹の中",
    "タイル", "エキス", "セリフ", "データ", "パートナー", "ゴミ", "ニケ", "ダッシュ", "パニック", "ボーイッシュ",
    "健康診断", "お願い", "大好き", "約束", "毎日", "一緒", "安心", "確認", "完全", "勝手", "理性", "痛み",
    "主人", "夕方", "近所", "専用", "筋肉", "連続", "静寂", "夕暮れ", "匂い", "陽光", "茜色", "柔らか",
    "幸せ", "気配", "反則", "鼓動", "安堵", "満足", "無邪気", "知性", "重み", "肩口", "口元", "大粒",
}


_SMALL_KANA = set("ァィゥェォヵヶャュョッぁぃぅぇぉゃゅょっ")


def _is_tag_like(word):
    """本文由来の語をタグ候補らしい形だけに絞る（助詞・オノマトペ・断片を除く）"""
    if not word:
        return False
    if any(ch in word for ch in " \t\r\n、。！？!?❤♡…「」『』（）()【】"):
        return False
    # 純ひらがなは助詞・オノマトペ・断片がほとんど。辞書にある語は既知として先に弾かれる
    if re.fullmatch(r"[ぁ-んー]+", word):
        return False
    if len(word) == 1:
        return False
    # カタカナのオノマトペ・断片を除外（カチカチ / ピクピク / ジュルジュル 等）
    if re.fullmatch(r"[ァ-ヶー]+", word):
        if len(word) < 3:
            return False
        if any(ch in _SMALL_KANA for ch in word):
            return False
        if re.fullmatch(r"(.{1,2})\1+", word):
            return False
    return True


def _tag_dictionary_keys():
    """辞書の日本語キー（全カテゴリ）をまとめた集合。辞書リロード時に破棄する。"""
    global _TAG_KEY_CACHE
    if _TAG_KEY_CACHE is not None:
        return _TAG_KEY_CACHE
    keys = set()
    for rows in (POSE_TAG_MAP, PROP_TAG_MAP, ITEM_TAG_MAP, EXPRESSION_TAG_MAP, CAMERA_TAG_MAP):
        for row in rows:
            keys.update(k for k in row[0] if isinstance(k, str) and k)
    for row in PLACE_TAG_MAP:
        keys.update(k for k in row[0] if isinstance(k, str) and k)
    for rows in (GENERAL_TAG_MAP, ATMOSPHERE_TAG_MAP):
        for row in rows:
            keys.update(k for k in row[0] if isinstance(k, str) and k)
    _TAG_KEY_CACHE = frozenset(k.strip() for k in keys if k.strip())
    return _TAG_KEY_CACHE


def _text_tokens(text):
    """本文から候補語（名詞の連続をまとめたもの）を取り出す。Janome が無ければ漢字/カタカナ連続で代替。"""
    try:
        tokenizer = memory_store.get_tokenizer()
        run = []
        for tok in tokenizer.tokenize(text):
            pos = tok.part_of_speech.split(",")
            p0 = pos[0]
            p1 = pos[1] if len(pos) > 1 else ""
            surface = tok.surface
            is_noun = p0 == "名詞" and p1 not in ("非自立", "代名詞", "数", "接尾", "接続詞的", "副詞可能")
            # ひらがなだけの語・オノマトペ・結合文字は語の連続を切る（助詞や断片を巻き込まない）
            if is_noun and (
                re.fullmatch(r"[ぁ-んー]+", surface)
                or re.search(r"[\u3099\u309a]", surface)
                or (re.fullmatch(r"[ァ-ヶー]+", surface) and (
                    len(surface) < 2
                    or any(ch in _SMALL_KANA for ch in surface)
                    or re.fullmatch(r"(.{1,2})\1+", surface)
                ))
            ):
                is_noun = False
            if is_noun:
                run.append(surface)
                continue
            if run:
                yield "".join(run)
                run = []
        if run:
            yield "".join(run)
    except Exception:
        for m in re.finditer(r"[一-龥ァ-ヶー]{2,}", text):
            yield m.group(0)


def _text_names(setting):
    """キャラ名・ユーザー名は未登録語として拾わない"""
    names = []
    if isinstance(setting, dict):
        ch = setting.get("character")
        if isinstance(ch, dict):
            names.append(ch.get("name"))
        names.append(setting.get("user_name"))
        for c in setting.get("chars") or []:
            if isinstance(c, dict):
                names.append(c.get("name"))
    return [n.strip() for n in names if isinstance(n, str) and n.strip()]


def _is_registered_text_token(word, keys, names):
    if not word or word in names:
        return True
    if not _is_tag_like(word):
        return True
    if word in _TEXT_MISS_STOPWORDS or word in memory_store.STOP_WORDS:
        return True
    # 辞書キーの完全一致・部分一致（既知の語の一部なら既知とみなす）
    for k in keys:
        if word in k:
            return True
    return False


def collect_text_tag_misses(text, source, setting=None):
    """本文（AI）とユーザー発言から、辞書に無い日本語の語を text_tag_misses.log に記録する"""
    if not text or not isinstance(text, str):
        return
    try:
        keys = _tag_dictionary_keys()
        names = set(_text_names(setting))
        seen = set()
        candidates = []
        for word in _text_tokens(text):
            word = word.strip()
            if not word or word in seen:
                continue
            seen.add(word)
            if _is_registered_text_token(word, keys, names):
                continue
            candidates.append(word)
        if not candidates:
            return
        with _text_miss_lock:
            p = _text_miss_log_path()
            existing = set()
            try:
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        for line in f:
                            parts = line.rstrip("\n").split(" ", 2)
                            if len(parts) >= 3:
                                existing.add((parts[1], parts[2]))
            except OSError:
                pass
            new_lines = [f"{now()} [{source}] {w}\n" for w in candidates if (f"[{source}]", w) not in existing]
            if new_lines:
                with open(p, "a", encoding="utf-8") as f:
                    f.writelines(new_lines)
    except Exception as e:
        print(f"[tag_miss] collect error: {e}")


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


_COUNT_TAG_KEYS = {
    "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls", "multiple girls",
    "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys", "multiple boys", "solo",
}


_QUALITY_TAG_KEYS = {
    "masterpiece", "best quality", "high quality", "ultra-detailed",
    "absurdres", "highres", "anime style", "glossy skin", "motion lines",
}


def normalize_tag_key(tag: str) -> str:
    """比較用の正規化キーを返す（括弧・重み・余分な空白を除去し小文字化。LoRA構文はそのまま保持）"""
    s = str(tag or "").strip()
    if not s:
        return ""
    if s.startswith("<") and s.endswith(">"):
        return s.lower()
    while len(s) >= 2 and s[0] in "([{" and s[-1] in ")]}":
        s = s[1:-1].strip()
    s = re.sub(r":[0-9.]+$", "", s).strip()
    while len(s) >= 2 and s[0] in "([{" and s[-1] in ")]}":
        s = s[1:-1].strip()
    return re.sub(r"\s+", " ", s.lower())


def is_count_tag_key(key: str) -> bool:
    return key in _COUNT_TAG_KEYS or bool(re.match(r"^\d+\s*(?:girl|boy)s?$", key))


def parse_prompt_tags(prompt_text: str) -> list[str]:
    """カンマまたは改行区切りのプロンプト文字列をタグのリストに正規化分割する"""
    if not prompt_text or not isinstance(prompt_text, str):
        return []
    normalized = prompt_text.replace("\r\n", ",").replace("\n", ",")
    return [t.strip() for t in normalized.split(",") if t and t.strip()]


def join_prompt_tags(tags: list[str]) -> str:
    """タグのリストを重複排除（正規化キー基準）しつつカンマ区切り文字列に結合する"""
    out = []
    seen = set()
    for t in tags:
        clean = str(t or "").strip().strip(",")
        if not clean:
            continue
        key = normalize_tag_key(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return ", ".join(out)


def _find_count_block_end(tags: list[str], limit: int) -> int:
    """指定範囲内で最初の人数タグブロックの末尾インデックスを返す（見つからなければ -1）"""
    end_idx = -1
    scan = min(len(tags), limit)
    for i in range(scan):
        key = normalize_tag_key(tags[i])
        if is_count_tag_key(key):
            end_idx = i
        elif end_idx >= 0:
            break
    return end_idx


def toggle_pov_in_prompt(prompt_text: str, enable_pov: bool) -> str:
    """プロンプト文字列の pov タグを ON/OFF 切り替える。
    例:
      enable_pov=False: '1girl, pov, from behind' -> '1girl, from behind'
      enable_pov=True : '1girl, from behind' -> '1girl, pov, from behind'
    """
    tags = parse_prompt_tags(prompt_text)
    filtered = [t for t in tags if normalize_tag_key(t) != "pov"]

    if not enable_pov:
        return join_prompt_tags(filtered)

    # 1. 先頭15トークン以内で人数タグブロックを探し、なければ全体から探す（2段階探索）
    last_count_idx = _find_count_block_end(filtered, 15)
    if last_count_idx < 0:
        last_count_idx = _find_count_block_end(filtered, len(filtered))

    # 2. 人数タグがない場合は先頭8トークン以内の品質・スタイルタグの直後を探す
    last_quality_idx = -1
    if last_count_idx < 0:
        for i in range(min(len(filtered), 8)):
            if normalize_tag_key(filtered[i]) in _QUALITY_TAG_KEYS:
                last_quality_idx = i

    if last_count_idx >= 0:
        insert_idx = last_count_idx + 1
    elif last_quality_idx >= 0:
        insert_idx = last_quality_idx + 1
    else:
        insert_idx = 0

    filtered.insert(insert_idx, "pov")
    return join_prompt_tags(filtered)


def apply_pov_mode_to_prompt(prompt_text: str, setting: dict) -> str:
    """チャンネル設定の setting['image']['pov_mode'] ('auto'|'on'|'off') をプロンプトへ反映する"""
    if not prompt_text or not isinstance(prompt_text, str):
        return prompt_text or ""
    img_cfg = (setting or {}).get("image") if isinstance(setting, dict) else {}
    if not isinstance(img_cfg, dict):
        return prompt_text
    pov_mode = str(img_cfg.get("pov_mode") or "auto").strip().lower()
    if pov_mode == "on":
        return toggle_pov_in_prompt(prompt_text, True)
    if pov_mode == "off":
        return toggle_pov_in_prompt(prompt_text, False)
    return join_prompt_tags(parse_prompt_tags(prompt_text))


def apply_pov_mode_to_state_camera(state_list: list, setting: dict) -> list:
    """state 内の camera タグ（配列または文字列）にも pov_mode ('on'|'off') を同期する"""
    if not isinstance(state_list, list):
        return state_list
    img_cfg = (setting or {}).get("image") if isinstance(setting, dict) else {}
    pov_mode = str((img_cfg or {}).get("pov_mode") or "auto").strip().lower()
    if pov_mode not in ("on", "off"):
        return state_list

    for idx, ch_state in enumerate(state_list):
        if not isinstance(ch_state, dict) or "camera" not in ch_state:
            continue
        cam = ch_state.get("camera")
        if isinstance(cam, str):
            cam_list = parse_prompt_tags(cam)
        elif isinstance(cam, list):
            cam_list = [str(c).strip() for c in cam if str(c).strip()]
        else:
            continue

        cam_clean = [c for c in cam_list if normalize_tag_key(c) != "pov"]
        if pov_mode == "on" and idx == 0:
            cam_clean.insert(0, "pov")
        ch_state["camera"] = cam_clean
    return state_list


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
    p = path or tag_dict_path()
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
    global _TAG_KEY_CACHE
    _TAG_KEY_CACHE = None
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
        return os.path.getmtime(tag_dict_path())
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
        # 辞書に無い衣装・体液は英語タグとして拾う（例: outfit="blue dress" → blue dress）。
        # 日本語で辞書にヒットした項目は二重に拾わない。
        for _k in ("outfit", "outerwear", "underwear", "body_fluids"):
            for _v in state_values(_gchars, _k):
                _src = normalize_pose_keyword_text(_v)
                if not _src:
                    continue
                if any(any(k in _src for k in keys) for keys, _ in GENERAL_TAG_MAP):
                    continue
                injected_general_tags += parse_ascii_tags(_v, field=_k)
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


def equipped_names(states):
    """state（キャラの配列）から装着物の名前を取り出す。表記揺れ（括弧・空白）は落として比べる"""
    return [re.sub(r"[（(].*?[）)]|\s", "", e)
            for c in (states or []) if isinstance(c, dict)
            for e in (c.get("equipped_items") or []) if isinstance(e, str)]


def state_block(state):
    if not state:
        return "（まだない。場面に出ているキャラの分を、応答の state で書き始める）"
    if compact_on():
        return json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(state, ensure_ascii=False, indent=1)


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
            cleaned_scene = apply_pov_mode_to_prompt(cleaned_scene, setting)
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

# server.py 側の `from image_engine import *` で再エクスポートする名前
__all__ = [
    'image_prompt_guidelines_text', 'state_tag_guidelines_text', 'COUNT_TAG',
    'PROHIBITED_ESCAPE_TAGS', 'CORE_ANATOMY_TAGS', 'POSE_TAG_MAP', 'POSE_TAG_ALIASES',
    'POSE_SORT_TAGS', 'PROP_TAG_MAP', 'PROP_TAG_ALIASES', 'ITEM_TAG_MAP',
    'OBJECT_INSERTION_MARKERS', 'log_item_tag_miss', 'log_state_tag_miss', '_text_miss_lock',
    '_TAG_KEY_CACHE', '_text_miss_log_path', '_TEXT_MISS_STOPWORDS', '_SMALL_KANA',
    '_is_tag_like', '_tag_dictionary_keys', '_text_tokens', '_text_names',
    '_is_registered_text_token', 'collect_text_tag_misses', '_PENIS_VISIBLE_TAGS',
    'CENSOR_RELATED_TAGS', 'ORAL_ACT_KEYWORDS', 'ORAL_AMBIGUOUS_KEYWORDS', 'normalize_tag',
    '_COUNT_TAG_KEYS', '_QUALITY_TAG_KEYS', 'normalize_tag_key', 'is_count_tag_key',
    'parse_prompt_tags', 'join_prompt_tags', '_find_count_block_end', 'toggle_pov_in_prompt',
    'apply_pov_mode_to_prompt', 'apply_pov_mode_to_state_camera', 'image_tag_context',
    'normalize_pose_keyword_text', 'EXPRESSION_TAG_MAP', 'PLACE_TAG_MAP', 'CAMERA_TAG_MAP',
    'POSTURE_CONFLICT_TAGS', 'PLACE_NEVER_STRIP', 'PLACE_COMPATIBLE', 'PLACE_SORT_TAGS',
    'CAMERA_ANGLE_TAGS', 'CAMERA_FRAMING_TAGS', 'CAMERA_SORT_TAGS', 'EXPRESSION_GROUPS',
    'TAG_DICT_PATH', '_tag_dict_lock', '_tag_dict_mtime', '_DEFAULT_POSE_TAG_MAP',
    '_DEFAULT_POSE_TAG_ALIASES', '_DEFAULT_PROP_TAG_MAP', '_DEFAULT_PROP_TAG_ALIASES',
    '_DEFAULT_ITEM_TAG_MAP', '_DEFAULT_EXPRESSION_TAG_MAP', '_DEFAULT_PLACE_TAG_MAP',
    '_DEFAULT_CAMERA_TAG_MAP', '_DEFAULT_POSTURE_CONFLICT_TAGS', '_DEFAULT_PLACE_NEVER_STRIP',
    '_DEFAULT_PLACE_COMPATIBLE', '_DEFAULT_CAMERA_ANGLE_TAGS', '_DEFAULT_CAMERA_FRAMING_TAGS',
    '_DEFAULT_EXPRESSION_GROUPS', 'GENERAL_TAG_MAP', 'ATMOSPHERE_TAG_MAP', '_valid_tag_rows',
    '_load_tag_dictionary', '_apply_tag_dictionary', 'reload_tag_dictionary',
    '_tag_dict_mtime_now', 'maybe_reload_tag_dictionary', '_TAG_DICT_LOADED', 'iter_state_chars',
    'state_values', 'first_state_value', 'iter_turn_chars', 'turn_by_name', 'image_person_count',
    'parse_ascii_tags', 'lookup_state_tag', '_state_without_pose', 'is_censor_related_tag',
    'is_prohibited_escape_tag', 'sanitize_prompt', 'char_key', 'split_tags', 'find_char',
    'APPEARANCE_WORDS', 'get_char_match_terms', 'char_presets', 'merge_chars', 'chars_block',
    'resolve_image_chars', 'clean_state', 'merge_state', 'equipped_names', 'state_block',
    '_count_tag_from_names', 'generate_prompt_from_state', 'build_prompt', 'build_workflow',
]
