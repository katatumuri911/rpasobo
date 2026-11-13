/**
 * Image Prompt Enhancer Toolbar (Pose / Camera Angle Dropdowns, POV Toggle & Default Persistence, Hybrid Undo)
 *
 * 使い方:
 * 1. チャット画面全体の動的プロンプト欄への自動アタッチ（アプリ初期化時に1回呼ぶだけ）:
 *    PromptToolbar.observe({
 *      selector: "textarea.image-prompt-input, textarea[data-role='image-prompt']",
 *      getSessionContext: () => ({
 *        sessionId: window.currentSession?.id,
 *        channelSetting: window.currentSession?.setting
 *      })
 *    });
 * 2. チャンネル切り替え時に規定セレクト表示を同期する場合:
 *    PromptToolbar.syncWithSession(window.currentSession);
 */
(function (global) {
  "use strict";

  const STORAGE_KEY_POV_MODE = "rpchat_default_pov_mode"; // "auto" | "on" | "off"
  const STORAGE_KEY_POV_KEEP = "rpchat_default_pov_keep"; // "1" | "0"
  const STORAGE_KEY_SMART_REPLACE = "rpchat_prompt_smart_replace"; // "1" | "0"
  const MAX_UNDO_STACK = 25;

  // ---------------------------------------------------------------------------
  // 1. ポーズセット定義（純粋なポーズ・体位・手足動作に最適化した全28種）
  // ---------------------------------------------------------------------------
  const POSE_PRESETS = [
    {
      group: "🔞 まんぐり返し・セルフ開帳",
      items: [
        {
          label: "🤸‍♀️ まんぐり返し（完全折りたたみ）",
          tags: "folded, lying, on back, legs up, legs over head, knees to chest, spread legs, grabbing own legs, ass up",
        },
        {
          label: "🔞 種付けプレス（折りたたみ圧迫）",
          tags: "mating press, folded, lying, on back, legs up, knees to chest, ankles on shoulders, spread legs, arched back",
        },
        {
          label: "🫶 仰向けセルフくぱぁ（秘部開帳）",
          tags: "lying, on back, m legs, spread legs, knees up, spread pussy, spreading own pussy, hands on own crotch, arched back, looking at viewer",
        },
        {
          label: "🍑 四つん這いセルフ尻広げ（後孔開帳）",
          tags: "all fours, top-down bottom-up, ass up, arched back, knees apart, spread ass, grabbing own ass, looking back",
        },
      ],
    },
    {
      group: "🌀 腰へこ・激しい運動・密着体位",
      items: [
        {
          label: "🌀 騎乗位・腰へこグラインド",
          tags: "straddling, girl on top, cowgirl position, squatting, arched back, grinding, pelvic thrust, motion lines, bouncing breasts, hands on another's chest",
        },
        {
          label: "🔁 背面騎乗位・尻振り腰へこ",
          tags: "straddling, reverse cowgirl, girl on top, squatting, ass up, arched back, grinding, pelvic thrust, motion lines, bouncing ass, looking back",
        },
        {
          label: "💥 立ちバック・壁手つき腰突き出し",
          tags: "standing, bent over, leaning forward, hands on wall, ass up, arched back, spread legs, motion lines, bouncing breasts, looking back",
        },
        {
          label: "⚡ 正常位・腰浮かせのけぞり絶頂",
          tags: "lying, on back, spread legs, knees up, arched back, extreme arched back, hips up, bridge, head back, toes curled, trembling, clenched hands",
        },
        {
          label: "💞 だいしゅきホールド（密着脚絡め）",
          tags: "lying, on back, leg lock, legs wrapped around waist, arms around neck, embracing, arched back, hips up",
        },
        {
          label: "🚉 駅弁ホールド（立ち抱え密着）",
          tags: "suspended congress, lifted by another, legs wrapped around waist, leg lock, arms around neck, embracing, arched back",
        },
      ],
    },
    {
      group: "✌️ 下品・変態調教・羞恥ポーズ",
      items: [
        {
          label: "✌️ ガニ股M字開脚・Wピース",
          tags: "squatting, m legs, spread legs, knees apart, leaning back, double v, v, elbows on knees, looking at viewer",
        },
        {
          label: "🐶 四つん這い・メス犬おねだり待機",
          tags: "all fours, dog pose, animalistic pose, paws, wrists together, ass up, arched back, knees apart, looking up, head tilt",
        },
        {
          label: "🙇‍♀️ 頭下げ・土下座尻突き出し（後手）",
          tags: "all fours, top-down bottom-up, face down, cheek on ground, ass up, arched back, knees apart, arms behind back",
        },
        {
          label: "💦 両膝抱え・しゃがみ開脚（放尿・排泄体勢）",
          tags: "squatting, spread legs, knees apart, holding own knees, arms under knees, leaning back, looking down",
        },
        {
          label: "🦵 片脚上げ・立ち開脚誘惑",
          tags: "standing, standing on one leg, one leg up, spread legs, grabbing own leg, arched back, hips forward, looking at viewer",
        },
      ],
    },
    {
      group: "⛓️ 拘束・緊縛・無防備ホールド",
      items: [
        {
          label: "⛓️ 後手縛り・膝立ち胸突き出し",
          tags: "kneeling, knees apart, arms behind back, bound wrists, arched back, breasts out, helpless, looking up",
        },
        {
          label: "🔓 M字開脚拘束・無防備ホールド",
          tags: "sitting, m legs, spread legs, knees up, arms behind back, bound wrists, bound ankles, spreader bar, arched back, helpless",
        },
        {
          label: "🧱 壁押し付け・片脚上げ",
          tags: "against wall, arms up, wrists pinned, one leg lifted, arched back, looking at viewer",
        },
        {
          label: "🐎 騎乗位・前傾密着",
          tags: "straddling, sitting on lap, knees apart, leaning forward, hands on another's chest, arched back",
        },
      ],
    },
    {
      group: "🛏️ 寝姿勢・四つん這い（定番）",
      items: [
        {
          label: "🐾 四つん這い・尻突き出し",
          tags: "all fours, top-down bottom-up, ass up, arched back, knees apart, looking back",
        },
        {
          label: "🛏️ 仰向け・M字開脚",
          tags: "lying, on back, spread legs, knees up, legs apart, arms up, clenched hands",
        },
        {
          label: "🍑 うつ伏せ・尻上げ振り返り",
          tags: "lying, on stomach, ass up, arched back, legs bent, looking back",
        },
      ],
    },
    {
      group: "🧎‍♀️ 座り・跪き・立ち（定番）",
      items: [
        {
          label: "🧎‍♀️ 正座・上目遣い待機",
          tags: "kneeling, seiza, hands on own thighs, knees together, looking up, head tilt",
        },
        {
          label: "🙌 膝立ち・胸張り奉仕姿勢",
          tags: "kneeling, knees apart, arms behind back, arched back, breasts out, looking up",
        },
        {
          label: "🐸 しゃがみ・M字開脚",
          tags: "squatting, spread legs, knees apart, arms between legs, leaning forward, looking at viewer",
        },
        {
          label: "🪑 椅子座り・脚広げ誘惑",
          tags: "sitting, on chair, leaning back, spread legs, knees apart, one leg up",
        },
        {
          label: "💃 振り返り・S字立ち",
          tags: "standing, looking back, hand on own hip, arched back, contrapposto, emphasis on breasts",
        },
        {
          label: "🍒 前屈み・谷間強調",
          tags: "standing, leaning forward, arms behind back, breasts out, cleavage, looking at viewer",
        },
        {
          label: "🩰 I字バランス開脚",
          tags: "standing, standing on one leg, standing split, leg up, holding own leg, flexible",
        },
      ],
    },
  ];

  // ---------------------------------------------------------------------------
  // 2. カメラの映えるアングルセット定義（お尻フォーカスを含む全11種）
  // ---------------------------------------------------------------------------
  const ANGLE_PRESETS = [
    {
      group: "定番・立体感",
      items: [
        {
          label: "🔙 背面・肩越し振り返り",
          tags: "from behind, over-the-shoulder shot, looking back, looking at viewer, back focus",
        },
        {
          label: "📐 ハイアングル・上目遣い",
          tags: "from above, high angle, looking up, looking at viewer, upturned eyes, foreshortening",
        },
        {
          label: "🔭 ローアングル・見下ろし",
          tags: "from below, low angle, worm's-eye view, looking down, looking at viewer, foreshortening",
        },
      ],
    },
    {
      group: "臨場感・ダイナミック",
      items: [
        {
          label: "⚡ 斜めダイナミック構図",
          tags: "dutch angle, dynamic angle, foreshortening, perspective, depth of field",
        },
        {
          label: "🌐 広角・超パース全身",
          tags: "full body, wide shot, fisheye, extreme perspective, foreshortening",
        },
      ],
    },
    {
      group: "部位フォーカス・接写",
      items: [
        {
          label: "🍑 お尻にフォーカス（臀部ドアップ）",
          tags: "from behind, lower body, close-up, ass focus, hip focus, depth of field, blurry background",
        },
        {
          label: "🩲 下半身・股間フォーカス",
          tags: "lower body, close-up, crotch focus, thigh focus, between legs, depth of field, blurry background",
        },
        {
          label: "💖 至近距離・顔＆胸アップ",
          tags: "upper body, close-up, portrait, breast focus, depth of field, blurry background",
        },
        {
          label: "✨ 横アングル・横乳＆脇",
          tags: "from side, profile, upper body, sideboob, armpits, depth of field, blurry background",
        },
      ],
    },
    {
      group: "特殊・映え構図",
      items: [
        {
          label: "🦶 足裏越し・パース構図",
          tags: "from below, low angle, full body, soles, feet in foreground, foreshortening, depth of field",
        },
        {
          label: "🪞 鏡越し・リフレクション",
          tags: "mirror, reflection, from behind, looking at mirror, eye contact in mirror, depth of field",
        },
      ],
    },
  ];

  // ---------------------------------------------------------------------------
  // 3. 8軸分離競合サブグループ定義（画角カスケード対応）
  // ---------------------------------------------------------------------------
  const CONFLICT_AXES = {
    // 1. ポーズ基本体勢・体位・腰の動き軸
    pose_base: new Set([
      "standing",
      "standing on one leg",
      "sitting",
      "on chair",
      "kneeling",
      "seiza",
      "squatting",
      "lying",
      "on back",
      "on stomach",
      "on side",
      "all fours",
      "top-down bottom-up",
      "folded",
      "mating press",
      "suspended congress",
      "lifted by another",
      "straddling",
      "sitting on lap",
      "girl on top",
      "cowgirl position",
      "reverse cowgirl",
      "standing split",
      "flexible",
      "contrapposto",
      "against wall",
      "bent over",
      "leaning forward",
      "leaning back",
      "arched back",
      "extreme arched back",
      "bridge",
      "hips up",
      "hips forward",
      "ass up",
      "dog pose",
      "animalistic pose",
      "face down",
      "cheek on ground",
      "grinding",
      "pelvic thrust",
      "bouncing breasts",
      "bouncing ass",
      "helpless",
      "trembling",
    ]),
    // 2. 脚の開閉・抱え・ホールド軸
    pose_legs: new Set([
      "spread legs",
      "m legs",
      "knees apart",
      "legs apart",
      "knees together",
      "legs together",
      "crossed legs",
      "knees up",
      "legs up",
      "leg up",
      "legs over head",
      "knees to chest",
      "ankles behind head",
      "ankles on shoulders",
      "leg lock",
      "legs wrapped around waist",
      "one leg up",
      "one leg lifted",
      "holding own leg",
      "holding own legs",
      "grabbing own leg",
      "grabbing own legs",
      "holding own knees",
      "legs bent",
      "toes curled",
      "bound ankles",
      "spreader bar",
    ]),
    // 3. 腕・手の配置・セルフ開帳・拘束軸
    pose_arms: new Set([
      "arms up",
      "arms behind back",
      "arms between legs",
      "arms under knees",
      "arms around neck",
      "embracing",
      "elbows on knees",
      "hand on own hip",
      "hands on own thighs",
      "hands on another's chest",
      "hands on wall",
      "hands on own ass",
      "hands on own crotch",
      "grabbing own ass",
      "spread pussy",
      "spreading own pussy",
      "fingers on pussy",
      "spread ass",
      "spreading own ass",
      "paws",
      "wrists together",
      "wrists pinned",
      "bound wrists",
      "clenched hands",
      "grabbing sheets",
      "double v",
      "v",
      "peace sign",
    ]),
    // 4. カメラ方向（前後左右上下・鏡面）軸
    angle_dir: new Set([
      "from behind",
      "from above",
      "high angle",
      "from below",
      "low angle",
      "worm's-eye view",
      "from side",
      "profile",
      "front view",
      "straight-on",
      "over-the-shoulder shot",
      "mirror",
      "reflection",
    ]),
    // 5. 画角・ショットサイズ軸
    angle_shot: new Set([
      "full body",
      "wide shot",
      "upper body",
      "lower body",
      "cowboy shot",
      "close-up",
      "portrait",
    ]),
    // 6. 部位フォーカス軸
    angle_focus: new Set([
      "back focus",
      "ass focus",
      "hip focus",
      "breast focus",
      "crotch focus",
      "thigh focus",
      "between legs",
      "sideboob",
      "armpits",
      "feet in foreground",
      "soles",
    ]),
    // 7. 特殊パース・レンズ効果軸
    angle_persp: new Set([
      "dutch angle",
      "dynamic angle",
      "fisheye",
      "extreme perspective",
      "foreshortening",
      "perspective",
      "depth of field",
      "blurry background",
    ]),
    // 8. 視線・顔の向き軸
    gaze: new Set([
      "looking at viewer",
      "looking back",
      "looking up",
      "looking down",
      "looking down at viewer",
      "looking away",
      "looking to the side",
      "looking at mirror",
      "eye contact in mirror",
      "upturned eyes",
      "head back",
      "head tilt",
    ]),
  };

  // カテゴリごとの主軸（挿入位置アンカー決定用）
  const PRIMARY_POSE_KEYS = new Set([
    ...CONFLICT_AXES.pose_base,
    ...CONFLICT_AXES.pose_legs,
    ...CONFLICT_AXES.pose_arms,
  ]);
  const PRIMARY_ANGLE_KEYS = new Set([
    ...CONFLICT_AXES.angle_dir,
    ...CONFLICT_AXES.angle_shot,
    ...CONFLICT_AXES.angle_focus,
    ...CONFLICT_AXES.angle_persp,
  ]);

  // 背景・照明・末尾系タグ（ポーズ追加時にこれらより手前へ挿入するための判定辞書）
  const TAIL_BACKGROUND_KEYS = new Set([
    "indoors",
    "outdoors",
    "bedroom",
    "bathroom",
    "classroom",
    "school",
    "ruined building",
    "under table",
    "on bed",
    "bed",
    "night",
    "day",
    "dim lighting",
    "steamy",
    "blurry background",
    "simple background",
    "depth of field",
  ]);

  const COUNT_TAG_KEYS = new Set([
    "1girl",
    "2girls",
    "3girls",
    "4girls",
    "5girls",
    "6+girls",
    "multiple girls",
    "1boy",
    "2boys",
    "3boys",
    "4boys",
    "5boys",
    "6+boys",
    "multiple boys",
    "solo",
  ]);

  const QUALITY_TAG_KEYS = new Set([
    "masterpiece",
    "best quality",
    "high quality",
    "ultra-detailed",
    "absurdres",
    "highres",
    "anime style",
    "glossy skin",
    "motion lines",
  ]);

  // ---------------------------------------------------------------------------
  // 4. タグ正規化・POV切替・スマート挿入コア関数群
  // ---------------------------------------------------------------------------

  function normalizeTagKey(tag) {
    let s = String(tag || "").trim();
    if (!s) return "";
    if (s.startsWith("<") && s.endsWith(">")) {
      return s.toLowerCase();
    }
    while (
      s.length >= 2 &&
      "([{".includes(s[0]) &&
      ")]}".includes(s[s.length - 1])
    ) {
      s = s.slice(1, -1).trim();
    }
    s = s.replace(/:[0-9.]+$/, "").trim();
    while (
      s.length >= 2 &&
      "([{".includes(s[0]) &&
      ")]}".includes(s[s.length - 1])
    ) {
      s = s.slice(1, -1).trim();
    }
    return s.replace(/\s+/g, " ").toLowerCase();
  }

  function isCountTagKey(key) {
    return COUNT_TAG_KEYS.has(key) || /^\d+\s*(?:girl|boy)s?$/.test(key);
  }

  function parsePromptTags(promptText) {
    if (!promptText || typeof promptText !== "string") return [];
    return promptText
      .replace(/\r?\n/g, ",")
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
  }

  function joinPromptTags(tags) {
    const out = [];
    const seen = new Set();
    for (const raw of tags) {
      const clean = String(raw || "")
        .trim()
        .replace(/^,+|,+$/g, "")
        .trim();
      if (!clean) continue;
      const key = normalizeTagKey(clean);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(clean);
    }
    return out.join(", ");
  }

  function hasPovTag(promptText) {
    const tags = parsePromptTags(promptText);
    return tags.some((t) => normalizeTagKey(t) === "pov");
  }

  function findCountBlockEnd(tags, limit) {
    let endIdx = -1;
    const scan = Math.min(tags.length, limit);
    for (let i = 0; i < scan; i++) {
      const key = normalizeTagKey(tags[i]);
      if (isCountTagKey(key)) {
        endIdx = i;
      } else if (endIdx >= 0) {
        break;
      }
    }
    return endIdx;
  }

  /**
   * プロンプト内の pov タグを ON / OFF 切り替える（2段階探索対応）
   * 例: "1girl, pov, from behind" <-> "1girl, from behind"
   */
  function setPovInPrompt(promptText, enablePov) {
    const tags = parsePromptTags(promptText);
    const withoutPov = tags.filter((t) => normalizeTagKey(t) !== "pov");

    if (!enablePov) {
      return joinPromptTags(withoutPov);
    }

    let lastCountIdx = findCountBlockEnd(withoutPov, 15);
    if (lastCountIdx < 0) {
      lastCountIdx = findCountBlockEnd(withoutPov, withoutPov.length);
    }

    let lastQualityIdx = -1;
    if (lastCountIdx < 0) {
      const qScan = Math.min(withoutPov.length, 8);
      for (let i = 0; i < qScan; i++) {
        if (QUALITY_TAG_KEYS.has(normalizeTagKey(withoutPov[i]))) {
          lastQualityIdx = i;
        }
      }
    }

    let insertIdx = 0;
    if (lastCountIdx >= 0) {
      insertIdx = lastCountIdx + 1;
    } else if (lastQualityIdx >= 0) {
      insertIdx = lastQualityIdx + 1;
    }

    withoutPov.splice(insertIdx, 0, "pov");
    return joinPromptTags(withoutPov);
  }

  /**
   * カメラアングルまたはポーズのタグセットをプロンプトに追加する（8軸＋画角カスケード対応）
   */
  function insertTagSetIntoPrompt(
    promptText,
    newTagsStr,
    category,
    smartReplace = true
  ) {
    let currentTags = parsePromptTags(promptText);
    const newTags = parsePromptTags(newTagsStr);
    if (newTags.length === 0) return joinPromptTags(currentTags);

    if (!smartReplace) {
      return joinPromptTags([...currentTags, ...newTags]);
    }

    const newKeys = new Set(newTags.map(normalizeTagKey));

    // 追加するプリセットに含まれる競合軸（サブグループ）だけをアクティブ化する
    const activeConflictSet = new Set();
    let hasAngleShot = false;

    for (const [axisName, axisSet] of Object.entries(CONFLICT_AXES)) {
      let axisTriggered = false;
      for (const k of newKeys) {
        if (axisSet.has(k)) {
          axisTriggered = true;
          break;
        }
      }
      if (axisTriggered) {
        if (axisName === "angle_shot") hasAngleShot = true;
        for (const item of axisSet) {
          activeConflictSet.add(item);
        }
      }
    }

    // 画角カスケード規則: 全身・上半身・下半身・接写(angle_shot)が指定された場合、古い部位フォーカス(angle_focus)もクリアする
    if (hasAngleShot) {
      for (const item of CONFLICT_AXES.angle_focus) {
        activeConflictSet.add(item);
      }
    }

    const primarySet =
      category === "angle" ? PRIMARY_ANGLE_KEYS : PRIMARY_POSE_KEYS;

    // 既存の該当軸タグ（および newKeys に含まれるタグ）を抜き取り、主軸の最初の出現位置を記録する
    let primaryRemovedIdx = -1;
    const filtered = [];
    for (let i = 0; i < currentTags.length; i++) {
      const key = normalizeTagKey(currentTags[i]);
      if (activeConflictSet.has(key) || newKeys.has(key)) {
        if (primaryRemovedIdx === -1 && primarySet.has(key)) {
          primaryRemovedIdx = filtered.length;
        }
        continue;
      }
      filtered.push(currentTags[i]);
    }
    currentTags = filtered;

    // CLIP最適挿入位置の決定
    let insertIdx = currentTags.length;
    if (category === "angle") {
      let anchorIdx = -1;
      const scanLimit = Math.min(currentTags.length, 15);
      for (let i = 0; i < scanLimit; i++) {
        const key = normalizeTagKey(currentTags[i]);
        if (
          isCountTagKey(key) ||
          key === "pov" ||
          QUALITY_TAG_KEYS.has(key)
        ) {
          anchorIdx = i;
        } else if (anchorIdx >= 0) {
          break;
        }
      }
      if (anchorIdx >= 0) {
        insertIdx = anchorIdx + 1;
      } else if (primaryRemovedIdx >= 0) {
        insertIdx = Math.min(primaryRemovedIdx, currentTags.length);
      } else {
        insertIdx = 0;
      }
    } else {
      if (primaryRemovedIdx >= 0) {
        insertIdx = Math.min(primaryRemovedIdx, currentTags.length);
      } else {
        const bgIdx = currentTags.findIndex((t) =>
          TAIL_BACKGROUND_KEYS.has(normalizeTagKey(t))
        );
        insertIdx = bgIdx >= 0 ? bgIdx : currentTags.length;
      }
    }

    currentTags.splice(insertIdx, 0, ...newTags);
    return joinPromptTags(currentTags);
  }

  // ---------------------------------------------------------------------------
  // 5. 既定（デフォルト）POV設定の取得・アトミック保存・同期
  // ---------------------------------------------------------------------------

  function resolveContextOptions(options = {}) {
    if (typeof options.getSessionContext === "function") {
      const ctx = options.getSessionContext() || {};
      return { ...options, ...ctx };
    }
    return options;
  }

  function getSavedPovMode(channelSetting) {
    if (
      channelSetting &&
      channelSetting.image &&
      ["auto", "on", "off"].includes(channelSetting.image.pov_mode)
    ) {
      return channelSetting.image.pov_mode;
    }
    const local = localStorage.getItem(STORAGE_KEY_POV_MODE);
    return ["auto", "on", "off"].includes(local) ? local : "auto";
  }

  function getSavedPovKeep(channelSetting) {
    if (
      channelSetting &&
      channelSetting.image &&
      typeof channelSetting.image.pov_keep === "boolean"
    ) {
      return channelSetting.image.pov_keep;
    }
    const local = localStorage.getItem(STORAGE_KEY_POV_KEEP);
    return local === null ? true : local === "1";
  }

  function getSmartReplacePref() {
    const local = localStorage.getItem(STORAGE_KEY_SMART_REPLACE);
    return local === null ? true : local === "1";
  }

  let saveTimer = null;

  async function savePovPreferences(povMode, povKeep, rawOptions = {}) {
    const options = resolveContextOptions(rawOptions);
    localStorage.setItem(STORAGE_KEY_POV_MODE, povMode);
    localStorage.setItem(STORAGE_KEY_POV_KEEP, povKeep ? "1" : "0");

    const st =
      options.channelSetting ||
      (global.currentSession && global.currentSession.setting);
    const sid =
      options.sessionId || (global.currentSession && global.currentSession.id);

    if (st) {
      st.image = st.image || {};
      st.image.pov_mode = povMode;
      st.image.pov_keep = Boolean(povKeep);
    }

    syncAllToolbarsPovDefaultUI(povMode, povKeep);

    if (typeof options.onSaveSetting === "function") {
      try {
        await options.onSaveSetting(povMode, povKeep);
        return;
      } catch (e) {
        console.warn("[PromptToolbar] onSaveSetting error:", e);
      }
    }

    if (!sid) return;

    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        const res = await fetch("/api/session/image_pov", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: sid,
            pov_mode: povMode,
            pov_keep: Boolean(povKeep),
          }),
        });
        if (res.ok) return;
      } catch (_) {
        // フォールバックへ進む
      }

      if (st) {
        try {
          await fetch("/api/setting", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: sid, setting: st }),
          });
        } catch (e) {
          console.warn("[PromptToolbar] Fallback setting save error:", e);
        }
      }
    }, 150);
  }

  function syncAllToolbarsPovDefaultUI(povMode, povKeep) {
    if (typeof document === "undefined" || !document.querySelectorAll) return;
    document.querySelectorAll(".rp-prompt-toolbar").forEach((toolbar) => {
      const modeSelect = toolbar.querySelector(".rp-pov-default-select");
      const keepCheck = toolbar.querySelector(".rp-pov-keep-checkbox");
      if (modeSelect && modeSelect.value !== povMode) {
        modeSelect.value = povMode;
      }
      if (keepCheck && keepCheck.checked !== Boolean(povKeep)) {
        keepCheck.checked = Boolean(povKeep);
      }
      if (typeof toolbar.__refreshToolbarState === "function") {
        toolbar.__refreshToolbarState();
      }
    });
  }

  function syncWithSession(sessionObj) {
    const st = sessionObj && sessionObj.setting ? sessionObj.setting : null;
    const mode = getSavedPovMode(st);
    const keep = getSavedPovKeep(st);
    syncAllToolbarsPovDefaultUI(mode, keep);
  }

  // ---------------------------------------------------------------------------
  // 6. UI コンポーネント生成・ハイブリッドUndo管理・イベントバインディング
  // ---------------------------------------------------------------------------

  function createPresetDropdown(buttonTitle, presets, category, onSelectTags) {
    const wrapper = document.createElement("div");
    wrapper.className = "rp-pt-dropdown";

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "rp-pt-btn rp-pt-dropdown-toggle";
    toggleBtn.innerHTML = `${buttonTitle} <span class="rp-pt-caret">▾</span>`;

    const menu = document.createElement("div");
    menu.className = "rp-pt-dropdown-menu";
    menu.setAttribute("role", "menu");

    presets.forEach((groupObj) => {
      const groupHeader = document.createElement("div");
      groupHeader.className = "rp-pt-menu-header";
      groupHeader.textContent = groupObj.group;
      menu.appendChild(groupHeader);

      groupObj.items.forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "rp-pt-menu-item";
        btn.setAttribute("role", "menuitem");

        const labelSpan = document.createElement("span");
        labelSpan.className = "rp-pt-item-label";
        labelSpan.textContent = item.label;

        const tagsPreview = document.createElement("span");
        tagsPreview.className = "rp-pt-item-tags";
        tagsPreview.textContent = item.tags;

        btn.appendChild(labelSpan);
        btn.appendChild(tagsPreview);

        btn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          wrapper.classList.remove("is-open");
          onSelectTags(item.tags, category);
        });

        menu.appendChild(btn);
      });
    });

    toggleBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const isOpen = wrapper.classList.contains("is-open");
      closeAllDropdowns();
      if (!isOpen) {
        wrapper.classList.add("is-open");
      }
    });

    wrapper.appendChild(toggleBtn);
    wrapper.appendChild(menu);
    return wrapper;
  }

  function closeAllDropdowns() {
    document
      .querySelectorAll(".rp-pt-dropdown.is-open")
      .forEach((el) => el.classList.remove("is-open"));
  }

  if (typeof document !== "undefined" && !global.__rpPromptToolbarGlobalListenersAdded) {
    global.__rpPromptToolbarGlobalListenersAdded = true;
    document.addEventListener("click", (e) => {
      if (!e.target || !e.target.closest || !e.target.closest(".rp-pt-dropdown")) {
        closeAllDropdowns();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeAllDropdowns();
      }
    });
  }

  function attachPromptToolbar(textarea, rawOptions = {}) {
    if (!textarea || !(textarea instanceof (global.HTMLTextAreaElement || Object))) return null;
    if (textarea.dataset && textarea.dataset.rpToolbarAttached === "1") {
      return textarea.previousElementSibling;
    }
    if (textarea.dataset) {
      textarea.dataset.rpToolbarAttached = "1";
    } else if (textarea.setAttribute) {
      textarea.setAttribute("data-rp-toolbar-attached", "1");
    }

    const options = resolveContextOptions(rawOptions);
    const undoStack = [];
    let lastToolbarValue = null;

    const toolbar = document.createElement("div");
    toolbar.className = "rp-prompt-toolbar";

    const smartReplaceCheckbox = document.createElement("input");
    smartReplaceCheckbox.type = "checkbox";
    smartReplaceCheckbox.className = "rp-smart-replace-checkbox";
    smartReplaceCheckbox.checked = getSmartReplacePref();
    smartReplaceCheckbox.addEventListener("change", () => {
      localStorage.setItem(
        STORAGE_KEY_SMART_REPLACE,
        smartReplaceCheckbox.checked ? "1" : "0"
      );
    });

    const undoBtn = document.createElement("button");
    undoBtn.type = "button";
    undoBtn.className = "rp-pt-btn rp-pt-undo-btn";
    undoBtn.textContent = "↩️ 戻す";
    undoBtn.title = "ツールバー操作前のプロンプトに戻します";
    undoBtn.disabled = true;

    function refreshToolbarState() {
      const active = hasPovTag(textarea.value);
      povBtn.classList.toggle("is-active", active);
      povBtn.setAttribute("aria-pressed", active ? "true" : "false");
      povBtn.innerHTML = active
        ? `<span class="rp-pov-dot is-on"></span>👁️ POV: <b>ON</b>`
        : `<span class="rp-pov-dot"></span>👁️ POV: OFF`;
      undoBtn.disabled = undoStack.length === 0;
    }

    toolbar.__refreshToolbarState = refreshToolbarState;

    function applyToolbarValue(newValue) {
      if (textarea.value === newValue) return;
      if (
        undoStack.length === 0 ||
        undoStack[undoStack.length - 1] !== textarea.value
      ) {
        undoStack.push(textarea.value);
        if (undoStack.length > MAX_UNDO_STACK) {
          undoStack.shift();
        }
      }
      textarea.value = newValue;
      lastToolbarValue = newValue;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
      refreshToolbarState();
    }

    function performUndo() {
      if (undoStack.length === 0) return false;
      const prevValue = undoStack.pop();
      textarea.value = prevValue;
      lastToolbarValue = prevValue;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
      refreshToolbarState();
      return true;
    }

    undoBtn.addEventListener("click", (e) => {
      e.preventDefault();
      performUndo();
      textarea.focus();
    });

    // 1. ポーズ追加ドロップダウン
    const poseDropdown = createPresetDropdown(
      "🧘 ポーズ追加",
      POSE_PRESETS,
      "pose",
      (tags, cat) => {
        const updated = insertTagSetIntoPrompt(
          textarea.value,
          tags,
          cat,
          smartReplaceCheckbox.checked
        );
        applyToolbarValue(updated);
        textarea.focus();
      }
    );

    // 2. カメラアングル追加ドロップダウン
    const angleDropdown = createPresetDropdown(
      "🎥 アングル追加",
      ANGLE_PRESETS,
      "angle",
      (tags, cat) => {
        const updated = insertTagSetIntoPrompt(
          textarea.value,
          tags,
          cat,
          smartReplaceCheckbox.checked
        );
        applyToolbarValue(updated);
        textarea.focus();
      }
    );

    // 3. POV 切り替え＆既定維持グループ
    const povGroup = document.createElement("div");
    povGroup.className = "rp-pt-pov-group";

    const povBtn = document.createElement("button");
    povBtn.type = "button";
    povBtn.className = "rp-pt-btn rp-pt-pov-toggle";
    povBtn.title =
      "プロンプト内の pov タグを ON/OFF 切り替えます（例: 1girl, pov, from behind ⇄ 1girl, from behind）";

    const povDefaultSelect = document.createElement("select");
    povDefaultSelect.className = "rp-pov-default-select";
    povDefaultSelect.title =
      "POVの既定（デフォルト）動作：新規プロンプト生成時にこの設定が維持されます";
    [
      { value: "auto", text: "規定: 自動(AI)" },
      { value: "on", text: "規定: 常時POV(ON)" },
      { value: "off", text: "規定: 非POV(OFF)" },
    ].forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.text;
      povDefaultSelect.appendChild(o);
    });
    povDefaultSelect.value = getSavedPovMode(options.channelSetting);

    const keepLabel = document.createElement("label");
    keepLabel.className = "rp-pt-inline-check";
    keepLabel.title =
      "ONにすると、POVボタンを切り替えた際にその状態（ON/OFF）をそのまま規定設定として維持・保存します";
    const keepCheck = document.createElement("input");
    keepCheck.type = "checkbox";
    keepCheck.className = "rp-pov-keep-checkbox";
    keepCheck.checked = getSavedPovKeep(options.channelSetting);
    const keepSpan = document.createElement("span");
    keepSpan.textContent = "📌 規定維持";
    keepLabel.appendChild(keepCheck);
    keepLabel.appendChild(keepSpan);

    // 4. 競合タグ自動整理チェックボックス
    const smartLabel = document.createElement("label");
    smartLabel.className = "rp-pt-inline-check";
    smartLabel.title =
      "ONにすると、ポーズやアングル追加時に同じ軸の古い矛盾タグ（体勢・視線・向き等）のみを自動置換します";
    const smartSpan = document.createElement("span");
    smartSpan.textContent = "✨ 競合整理";
    smartLabel.appendChild(smartReplaceCheckbox);
    smartLabel.appendChild(smartSpan);

    povBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const nextEnable = !hasPovTag(textarea.value);
      const updated = setPovInPrompt(textarea.value, nextEnable);
      applyToolbarValue(updated);

      if (keepCheck.checked) {
        const newMode = nextEnable ? "on" : "off";
        povDefaultSelect.value = newMode;
        await savePovPreferences(newMode, true, rawOptions);
      }
    });

    povDefaultSelect.addEventListener("change", async () => {
      const mode = povDefaultSelect.value;
      await savePovPreferences(mode, keepCheck.checked, rawOptions);
      if (mode === "on" || mode === "off") {
        const updated = setPovInPrompt(textarea.value, mode === "on");
        applyToolbarValue(updated);
      }
    });

    keepCheck.addEventListener("change", async () => {
      await savePovPreferences(
        povDefaultSelect.value,
        keepCheck.checked,
        rawOptions
      );
    });

    textarea.addEventListener("input", refreshToolbarState);
    textarea.addEventListener("focus", refreshToolbarState);
    toolbar.addEventListener("mouseenter", refreshToolbarState);

    textarea.addEventListener("keydown", (e) => {
      if (
        (e.ctrlKey || e.metaKey) &&
        !e.shiftKey &&
        e.key.toLowerCase() === "z"
      ) {
        if (undoStack.length > 0 && textarea.value === lastToolbarValue) {
          e.preventDefault();
          performUndo();
        }
      }
    });

    if (options.applyDefaultOnInit) {
      const initMode = povDefaultSelect.value;
      if (initMode === "on" || initMode === "off") {
        const updated = setPovInPrompt(textarea.value, initMode === "on");
        if (updated !== textarea.value) {
          textarea.value = updated;
          lastToolbarValue = updated;
        }
      }
    }
    refreshToolbarState();

    povGroup.appendChild(povBtn);
    povGroup.appendChild(povDefaultSelect);
    povGroup.appendChild(keepLabel);

    toolbar.appendChild(poseDropdown);
    toolbar.appendChild(angleDropdown);
    toolbar.appendChild(povGroup);
    toolbar.appendChild(smartLabel);
    toolbar.appendChild(undoBtn);

    // テスト環境やDOM未配置時にも参照できるようtoolbarプロパティを保持
    toolbar.poseDropdown = poseDropdown;
    toolbar.angleDropdown = angleDropdown;
    toolbar.povBtn = povBtn;
    toolbar.povDefaultSelect = povDefaultSelect;
    toolbar.keepCheck = keepCheck;
    toolbar.smartReplaceCheckbox = smartReplaceCheckbox;
    toolbar.undoBtn = undoBtn;
    toolbar.applyToolbarValue = applyToolbarValue;
    toolbar.performUndo = performUndo;
    toolbar.getUndoStack = () => [...undoStack];

    if (textarea.parentNode) {
      textarea.parentNode.insertBefore(toolbar, textarea);
    }
    return toolbar;
  }

  function syncTextareaUI(textarea) {
    if (!textarea) return;
    const tb = textarea.previousElementSibling;
    if (tb && typeof tb.__refreshToolbarState === "function") {
      tb.__refreshToolbarState();
    }
  }

  function observePromptTextareas(config = {}) {
    const selector =
      config.selector ||
      "textarea.image-prompt-input, textarea[data-role='image-prompt'], .image-prompt-box textarea";

    function scanAndAttach(root = document) {
      if (!root || !root.querySelectorAll) return;
      root.querySelectorAll(selector).forEach((el) => {
        if (el instanceof (global.HTMLTextAreaElement || Object)) {
          attachPromptToolbar(el, config);
        }
      });
    }

    if (typeof document !== "undefined") {
      scanAndAttach(document);

      if (typeof MutationObserver !== "undefined") {
        const observer = new MutationObserver((mutations) => {
          for (const m of mutations) {
            for (const node of m.addedNodes) {
              if (node.nodeType === 1) { // Node.ELEMENT_NODE
                if (node.matches && node.matches(selector)) {
                  attachPromptToolbar(node, config);
                } else {
                  scanAndAttach(node);
                }
              }
            }
          }
        });

        if (document.body) {
          observer.observe(document.body, { childList: true, subtree: true });
        }
        return observer;
      }
    }
    return null;
  }

  const PromptToolbar = {
    attach: attachPromptToolbar,
    observe: observePromptTextareas,
    syncWithSession,
    syncTextareaUI,
    setPovInPrompt,
    hasPovTag,
    insertTagSetIntoPrompt,
    normalizeTagKey,
    isCountTagKey,
    parsePromptTags,
    joinPromptTags,
    POSE_PRESETS,
    ANGLE_PRESETS,
    CONFLICT_AXES,
  };

  global.PromptToolbar = PromptToolbar;

  if (typeof module !== "undefined" && module.exports) {
    module.exports = PromptToolbar;
  }
})(typeof window !== "undefined" ? window : globalThis);
