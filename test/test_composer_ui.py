import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestComposerUI(unittest.TestCase):
    """コンポーザー（入力欄）・お気に入りチップ・マイクボタンのUI回帰防止テスト"""

    def setUp(self):
        self.app_js_path = os.path.join(ROOT, "public", "app.js")
        self.style_css_path = os.path.join(ROOT, "public", "style.css")
        self.index_html_path = os.path.join(ROOT, "public", "index.html")
        self.server_py_path = os.path.join(ROOT, "server.py")
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            self.app_js = f.read()
        with open(self.style_css_path, "r", encoding="utf-8") as f:
            self.style_css = f.read()
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            self.index_html = f.read()
        with open(self.server_py_path, "r", encoding="utf-8") as f:
            self.server_py = f.read()

    def test_short_model_label_strips_ag_prefix(self):
        """shortModelLabel が 'ag:' プレフィックスを剥がし、空や 'ag:' のときは '会話と同じ' を返すこと"""
        js_code = """
        const models = [];
        const ideModels = { models: [] };
        function modelPoolFor(p) { return []; }
        %s
        if (shortModelLabel("antigravity", "ag:") !== "会話と同じ") {
            throw new Error("'ag:' did not return '会話と同じ', got: " + shortModelLabel("antigravity", "ag:"));
        }
        if (shortModelLabel("antigravity", "") !== "会話と同じ") {
            throw new Error("'' did not return '会話と同じ'");
        }
        if (shortModelLabel("antigravity", null) !== "会話と同じ") {
            throw new Error("null did not return '会話と同じ'");
        }
        if (shortModelLabel("antigravity", "ag:custom-model") !== "custom-model") {
            throw new Error("'ag:custom-model' did not strip 'ag:', got: " + shortModelLabel("antigravity", "ag:custom-model"));
        }
        console.log("OK");
        """
        m = re.search(r"function shortModelLabel\(provider, modelId\) \{[\s\S]*?\n\}", self.app_js)
        self.assertIsNotNone(m, "shortModelLabel が app.js に見つかりません")
        fn_code = m.group(0)
        full_js = js_code % fn_code
        res = subprocess.run(["node", "-e", full_js], capture_output=True, text=True, check=True)
        self.assertIn("OK", res.stdout)

    def test_fav_btn_has_touchend_listener_and_no_pointer_cancel_slop(self):
        """favModelBtn がスマホでタップ可能（touchend リスナーがあり、favBtn 用の TAP_SLOP ガードがない）"""
        m = re.search(r'if \(\$\("favModelBtn"\)\) \{[\s\S]*?\n\}', self.app_js)
        self.assertIsNotNone(m, "favModelBtn の初期化ブロックが見つかりません")
        fav_block = m.group(0)
        self.assertIn('favBtn.addEventListener("touchend"', fav_block)
        self.assertNotIn("TAP_SLOP", fav_block, "favBtn に手ブレでタップが破棄される TAP_SLOP が残っています")

    def test_foot_input_capsule_style(self):
        """コンパクト1行時はカプセル型（border-radius: 9999px）、展開時はカード型角丸であること"""
        foot_input_block = re.search(r"\.foot-input\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(foot_input_block)
        self.assertIn("border-radius: 9999px", foot_input_block.group(0))

        is_expanded_block = re.search(r"\.foot-input\.is-expanded\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(is_expanded_block)
        self.assertIn("border-radius: 22px", is_expanded_block.group(0))

    def test_mic_button_has_transparent_bg_and_offset(self):
        """マイクボタンは円形背景なし（transparent）、アイコンが右にオフセットされていること"""
        mic_block = re.search(r"#micBtn\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(mic_block)
        self.assertIn("background: transparent", mic_block.group(0))

        icon_block = re.search(r"#micBtn \.composer-icon\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(icon_block)
        self.assertIn("transform: translateX(", icon_block.group(0))

    def test_composer_chip_mobile_no_excessive_fixed_width(self):
        """スマホ展開時のお気に入りチップに過大な固定 min-width（120px以上）がなく、自然な max-width が設定されていること"""
        # media query 内の .composer-chip を検索
        matches = re.findall(r"\.foot-input\.is-expanded \.composer-chip\s*\{[^}]*\}", self.style_css)
        self.assertTrue(len(matches) > 0)
        for block_text in matches:
            self.assertNotIn("min-width: 120px", block_text, "過大な固定 min-width が設定されています")
            self.assertIn("max-width:", block_text)

    def test_cache_busting_versions_present(self):
        """index.html で style.css と app.js にバージョンクエリパラメータが付与されていること"""
        self.assertTrue(re.search(r'href="style\.css\?v=\d+"', self.index_html), "style.css のバージョンクエリがありません")
        self.assertTrue(re.search(r'src="app\.js\?v=\d+"', self.index_html), "app.js のバージョンクエリがありません")

    def test_sidebar_model_card_removed_and_quota_badge_in_composer(self):
        """サイドメニューのモデルカードと独立した #agQuotaBadge は廃止し、残量は #sendBtn 内の .send-quota-layer に出す"""
        self.assertNotIn('id="sideAiSummaryRow"', self.index_html)
        self.assertNotIn('id="sideModelContainer"', self.index_html)
        self.assertNotIn('id="agQuotaBadge"', self.index_html)
        send_btn = re.search(r'<button id="sendBtn"[\s\S]*?</button>', self.index_html)
        self.assertIsNotNone(send_btn)
        self.assertIn('class="send-quota-layer"', send_btn.group(0))
        self.assertIn('class="send-quota-pct"', send_btn.group(0))
        # 6カラムグリッドの検証（quota 列の削除）
        grid = re.search(r"\.foot-input\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(grid)
        self.assertNotIn("quota", grid.group(0))
        expanded_grid = re.search(r"\.foot-input\.is-expanded\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(expanded_grid)
        self.assertNotIn("quota", expanded_grid.group(0))
        # モバイル expanded グリッドの検証（6カラム・quota 列なし）
        mobile_match = re.search(r"@media\s*\([^)]*max-width:\s*768px[^)]*\)[\s\S]*?\.foot-input\.is-expanded\s*\{([^}]*)\}", self.style_css)
        self.assertIsNotNone(mobile_match, "モバイル expanded グリッド定義が見つかりません")
        mobile_css = mobile_match.group(1)
        self.assertNotIn("quota", mobile_css)
        cols_match = re.search(r"grid-template-columns:\s*([^;]+);", mobile_css)
        self.assertIsNotNone(cols_match)
        cols = re.findall(r"(?:minmax\([^)]+\)|\S+)", cols_match.group(1))
        self.assertEqual(6, len(cols), f"モバイル expanded は 6 カラムでなければなりません: {cols_match.group(1)}")
        # 送信・停止・クォータで --rp-bg- を使わないこと
        send_css = re.search(r"#sendBtn\s*\{[^}]*\}", self.style_css)
        if send_css:
            self.assertNotIn("--rp-bg-", send_css.group(0))
        layer_css = re.search(r"\.send-quota-layer[^{]*\{[^}]*\}", self.style_css)
        if layer_css:
            self.assertNotIn("--rp-bg-", layer_css.group(0))
        stop_css = re.search(r"#stopBtn\s*\{[^}]*\}", self.style_css)
        if stop_css:
            self.assertNotIn("--rp-bg-", stop_css.group(0))

    def test_ag_quota_badge_peek_state_machine(self):
        """残量バッジは「フォーカスで3.5秒、タイピング後は1秒見せる」。
        時間がたったときはフェードアウトし、フォーカスが外れたときはアニメなしで即座に消える。
        フェードアウト完了後は 25秒クールダウン付きで /api/ag_quota を背景フェッチする"""
        m = re.search(r"const AG_QUOTA_PEEK_MS[\s\S]*?function isAgQuotaVisible\(\) \{[\s\S]*?\n\}", self.app_js)
        self.assertIsNotNone(m, "peekAgQuota / fadeAgQuota / hideAgQuota / isAgQuotaVisible が app.js に見つかりません")
        # 配線も見る（関数だけ正しくても、blur で呼んでいなければバッジが残る）
        i = self.app_js.find('$("messageInput").addEventListener("focus"')
        j = self.app_js.find("});", i)
        focus = self.app_js[i:j + 3] if i >= 0 and j > i else ""
        self.assertIn("peekAgQuota()", focus, "フォーカスでバッジを出していない")
        i = self.app_js.find('$("messageInput").addEventListener("blur"')
        j = self.app_js.find("});", i)
        blur = self.app_js[i:j + 3] if i >= 0 and j > i else ""
        self.assertIn("hideAgQuota()", blur, "blur で即座に消していない（フェードが走って残像になる）")
        self.assertNotIn("fadeAgQuota()", blur, "blur でフェードさせてはいけない（即座に消す）")
        js_code = """
        // 最小の DOM とタイマー代用（見せて→フェード→消す、の状態遷移だけを見る）
        let now = 0, seq = 1, timers = [];
        function setTimeout(fn, ms) { const id = seq++; timers.push({ id, at: now + (ms || 0), fn }); return id; }
        function clearTimeout(id) { timers = timers.filter((t) => t.id !== id); }
        async function advance(ms) {
          const target = now + ms;
          for (;;) {
            timers.sort((a, b) => a.at - b.at);
            if (!timers.length || timers[0].at > target) break;
            const t = timers.shift();
            now = t.at;
            t.fn();
            await new Promise((r) => setImmediate(r));
          }
          now = target;
          await new Promise((r) => setImmediate(r));
        }
        const el = { offsetWidth: 10, _c: new Set(["send-quota-layer", "hidden"]),
          classList: {
            add(c) { el._c.add(c); }, remove(...cs) { cs.forEach((c) => el._c.delete(c)); },
            contains(c) { return el._c.has(c); }, toggle(c, v) { v ? el._c.add(c) : el._c.delete(c); },
          } };
        const getSendQuotaLayer = () => el;
        let fetchedUrls = [];
        const fetch = async (url) => { fetchedUrls.push(url); return { ok: true, json: async () => ({ models: [] }) }; };
        const Date = { now: () => now };
        const state = { setting: { ai_provider: "antigravity" } };
        const updateAgQuotaBadge = () => {};
        __FUNCS__
        const badgeState = () => (el._c.has("is-fading") ? "fading" : el._c.has("is-peek") ? "peek" : "off");

        (async () => {
        // 1) 残量が無い（hidden）ときは何も出さない
        peekAgQuota();
        if (badgeState() !== "off") throw new Error("hidden なのに出た: " + badgeState());

        // 2) フォーカス→3.5秒見せる→フェードアウト→消える
        el._c.delete("hidden");
        peekAgQuota();
        if (badgeState() !== "peek") throw new Error("フォーカスで出ていない: " + badgeState());
        await advance(1900);
        if (badgeState() !== "peek") throw new Error("3.5秒前に消えた: " + badgeState());
        await advance(1700);
        if (badgeState() !== "fading") throw new Error("3.5秒後にフェードしない: " + badgeState());
        await advance(400);
        if (badgeState() !== "off") throw new Error("フェード後に消えない: " + badgeState());
        // フェード完了時に /api/ag_quota を背景フェッチする
        if (fetchedUrls.length !== 1 || fetchedUrls[0] !== "/api/ag_quota") {
          throw new Error("フェード完了後に /api/ag_quota がフェッチされていない: " + JSON.stringify(fetchedUrls));
        }

        // 2-2) 25秒クールダウンの検証: クールダウン内に再度フェードしてもフェッチしない
        peekAgQuota();
        await advance(3600);
        await advance(400);
        if (fetchedUrls.length !== 1) throw new Error("25秒クールダウン内なのに再フェッチされた");

        // 2-3) クールダウン経過（25秒以上）後はフェッチする
        await advance(26000);
        peekAgQuota();
        await advance(3600);
        await advance(400);
        if (fetchedUrls.length !== 2) throw new Error("クールダウン経過後にフェッチされなかった: " + fetchedUrls.length);

        // 3) 3.5秒たつ前にフォーカスが外れたら、アニメなしで即座に消える
        peekAgQuota();
        await advance(800);
        hideAgQuota();
        if (badgeState() !== "off") throw new Error("blur で即座に消えない: " + badgeState());
        await advance(6000);
        if (badgeState() !== "off") throw new Error("消したあとに再び出た: " + badgeState());

        // 4) タイピング後は 1.0 秒でフェードアウトする
        peekAgQuota();
        await advance(500);
        onAgQuotaTyping();
        await advance(800);
        if (badgeState() !== "peek") throw new Error("タイピング後1秒前に消えた: " + badgeState());
        await advance(300);
        if (badgeState() !== "fading") throw new Error("タイピング後1秒でフェードしない: " + badgeState());
        await advance(400);
        if (badgeState() !== "off") throw new Error("タイピング後のフェードで消えない: " + badgeState());

        // 5) 選択肢クリックは 1.0 秒見せてフェードアウトする
        peekAgQuota(1000);
        await advance(800);
        if (badgeState() !== "peek") throw new Error("選択肢クリック後1秒前に消えた: " + badgeState());
        await advance(300);
        if (badgeState() !== "fading") throw new Error("選択肢クリック後1秒でフェードしない: " + badgeState());
        await advance(400);
        if (badgeState() !== "off") throw new Error("選択肢クリック後のフェードで消えない: " + badgeState());

        // 6) 出ていないときの hideAgQuota は何もしない
        hideAgQuota();
        if (badgeState() !== "off") throw new Error("出ていないのに状態が変わった: " + badgeState());

        // 7) フォーカスしなおすとまた 3.5 秒見せる（前のタイマーを引きずらない）
        peekAgQuota();
        await advance(1000);
        peekAgQuota();
        await advance(3000);
        if (badgeState() !== "peek") throw new Error("再フォーカスで3.5秒がリセットされていない: " + badgeState());
        await advance(600);
        if (badgeState() !== "fading") throw new Error("再フォーカス後も3.5秒でフェードしない: " + badgeState());
        await advance(400);
        if (badgeState() !== "off") throw new Error("再フォーカス後のフェードで消えない: " + badgeState());
        console.log("OK");
        })().catch((e) => { console.error(e); process.exit(1); });
        """.replace("__FUNCS__", m.group(0))
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
            f.write(js_code)
            path = f.name
        try:
            res = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, res.returncode, res.stderr)
        finally:
            os.unlink(path)
        self.assertIn("OK", res.stdout)
        # フェードは CSS 側に必要。消すのは blur 時だけ即時
        self.assertTrue(re.search(r"(?:\.send-quota-layer|\.ag-quota)\.is-fading", self.style_css), "フェードアウト用CSSが消えている")

    def test_ag_quota_badge_uses_weekly_quota_only(self):
        """週の残り（weekly）だけを出し、5時間の枠しか無いときは代用、AG 以外は隠す。％記号付きで表示"""
        m = re.search(r"function modelResetText\(r\) \{[\s\S]*?\n\}", self.app_js)
        self.assertIsNotNone(m)
        js_code = """
        const el = { textContent: "", title: "", _c: new Set(["hidden"]),
          style: { setProperty() {} },
          classList: { add(c) { el._c.add(c); }, remove(c) { el._c.delete(c); },
            toggle(c, v) { v ? el._c.add(c) : el._c.delete(c); }, contains(c) { return el._c.has(c); } },
          setAttribute() {}, removeAttribute() {} };
        const choice = { main: "M318" };
        const sb = {
          console, Date,
          state: { setting: { ai_provider: "antigravity" } },
          ideModels: { default: "M318", models: [
            { model: "M318", label: "Gemini 3.8 Flash (High)", left: 0.99,
              weekly: { left: 0.45, reset: "2026-11-21T01:35:23Z" } },
            { model: "M35", label: "Claude Sonnet 4.6 (Thinking)", left: 1,
              weekly: { left: 1, reset: "2026-11-17T00:40:13Z" } } ] },
          currentModelConfig: () => ({ provider: "antigravity", main: choice.main }),
          getSendQuotaLayer: () => el,
          $: (id) => (id === "agQuotaBadge" ? el : null),
        };
        Object.assign(globalThis, sb);
        __FUNCS__
        updateAgQuotaBadge();
        if (el.textContent !== "45%") throw new Error("週の残りを出していない: " + el.textContent);
        if (!el.title.includes("今週")) throw new Error("週だと分からない: " + el.title);
        if (el._c.has("hidden")) throw new Error("AG なのに隠れている");
        choice.main = "M35";
        updateAgQuotaBadge();
        if (el.textContent !== "100%") throw new Error("グループの切替が効いていない: " + el.textContent);
        // 週が無い古い応答では 5 時間の枠で代用する
        sb.ideModels.models[0].weekly = null;
        choice.main = "M318";
        updateAgQuotaBadge();
        if (el.textContent !== "99%") throw new Error("5時間の枠で代用していない: " + el.textContent);
        sb.state.setting.ai_provider = "google";
        updateAgQuotaBadge();
        if (!el._c.has("hidden") || el.textContent !== "") throw new Error("AG 以外で隠れていない");

        // 15% 以下のときは赤 (#f87171)、15% 超のときは青 (#38bdf8)
        sb.state.setting.ai_provider = "antigravity";
        let props = {};
        el.style.setProperty = (k, v) => { props[k] = v; };
        sb.ideModels.models[0].weekly = { left: 0.15, reset: "" };
        choice.main = "M318";
        updateAgQuotaBadge();
        if (el.textContent !== "15%") throw new Error("15% が表示されていない: " + el.textContent);
        if (!el._c.has("is-low")) throw new Error("15% なのに is-low が付いていない");
        if (props["--quota-color"] !== "#f87171") throw new Error("15% なのに赤 (#f87171) になっていない: " + props["--quota-color"]);

        sb.ideModels.models[0].weekly = { left: 0.16, reset: "" };
        updateAgQuotaBadge();
        if (el.textContent !== "16%") throw new Error("16% が表示されていない: " + el.textContent);
        if (el._c.has("is-low")) throw new Error("16% なのに is-low が付いている");
        if (props["--quota-color"] !== "#38bdf8") throw new Error("16% なのに青 (#38bdf8) になっていない: " + props["--quota-color"]);

        sb.ideModels.models[0].weekly = { left: 0.0, reset: "" };
        updateAgQuotaBadge();
        if (el.textContent !== "0%") throw new Error("0% が表示されていない: " + el.textContent);
        if (!el._c.has("is-low")) throw new Error("0% なのに is-low が付いていない");
        if (props["--quota-color"] !== "#f87171") throw new Error("0% なのに赤 (#f87171) になっていない: " + props["--quota-color"]);
        console.log("OK");
        """.replace("__FUNCS__", m.group(0) + "\n" + re.search(r"function modelQuota\(r\) \{[\s\S]*?\n\}", self.app_js).group(0)
                 + "\n" + re.search(r"function modelQuotaText\(r\) \{[\s\S]*?\n\}", self.app_js).group(0)
                 + "\n" + (re.search(r"function getSendQuotaLayer\(\) \{[\s\S]*?\n\}", self.app_js).group(0) if "function getSendQuotaLayer" in self.app_js else "")
                 + "\n" + re.search(r"function updateAgQuotaBadge\(\) \{[\s\S]*?\n\}", self.app_js).group(0))
        # Windows の node -e は引数を cp932 で解釈するので、UTF-8 の一時ファイルから実行する
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
            f.write(js_code)
            path = f.name
        try:
            res = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, res.returncode, res.stderr)
        finally:
            os.unlink(path)
        self.assertIn("OK", res.stdout)

    def test_ag_quota_server_route(self):
        """server.py に /api/ag_quota エンドポイントが定義されていること"""
        self.assertIn('"/api/ag_quota"', self.server_py)
        self.assertTrue(re.search(r'p\s+(?:==|in)\s+.*?"/api/ag_quota"', self.server_py))

    def test_send_btn_quota_tap_interaction(self):
        """クォータ表示中に送信ボタンを押したときの挙動:
        - 入力が空なら送信せずフォーカスを維持（preventDefault）
        - 入力があれば通常どおり送信する
        app.js の実コードを抽出して実行・検証する
        """
        self.assertIn('$("sendBtn").addEventListener("pointerdown"', self.app_js)
        self.assertIn('$("sendBtn").addEventListener("mousedown"', self.app_js)
        self.assertIn('$("sendBtn").addEventListener("touchstart"', self.app_js)
        m = re.search(r'if \(\$\("sendBtn"\)\) \{[\s\S]*?\n\}', self.app_js)
        self.assertIsNotNone(m, "sendBtn 初期化ブロックが見つかりません")
        send_block = m.group(0)

        js_code = """
        let prevented = false, sent = false, focused = false;
        let inputValue = "";
        let quotaVisible = false;
        const isAgQuotaVisible = () => quotaVisible;
        const send = () => { sent = true; };
        const attaching = [];
        const ta = {
          get value() { return inputValue; },
          set value(v) { inputValue = v; },
          focus() { focused = true; }
        };
        const listeners = {};
        const sendBtnEl = {
          addEventListener(evt, fn) { listeners[evt] = fn; },
          onclick: null,
        };
        const document = { activeElement: null };
        const $ = (id) => (id === "messageInput" ? ta : id === "sendBtn" ? sendBtnEl : null);

        // app.js から抽出した本物のコードを実行
        __SEND_BLOCK__

        const trigger = (type) => {
          prevented = false;
          const e = { preventDefault() { prevented = true; } };
          if (listeners[type]) listeners[type](e);
          return prevented;
        };
        const click = () => {
          prevented = false;
          const e = { preventDefault() { prevented = true; } };
          if (sendBtnEl.onclick) sendBtnEl.onclick(e);
          return prevented;
        };

        // 1) クォータ表示中・入力が空のとき: pointerdown/touchstart で preventDefault され、送信は走らない
        quotaVisible = true; inputValue = ""; sent = false; focused = false;
        if (!trigger("pointerdown")) throw new Error("空入力時に pointerdown で preventDefault されていない");
        if (!trigger("touchstart")) throw new Error("空入力時に touchstart で preventDefault されていない");
        click();
        if (sent) throw new Error("空入力・クォータ表示中に送信されてしまった");

        // 2) クォータ表示中・入力があるとき: preventDefault されず通常どおり送信される
        quotaVisible = true; inputValue = "こんにちは"; sent = false;
        if (trigger("pointerdown")) throw new Error("入力があるのに pointerdown で preventDefault された");
        if (trigger("touchstart")) throw new Error("入力があるのに touchstart で preventDefault された");
        click();
        if (!sent) throw new Error("入力があるのに送信されなかった");

        // 3) クォータ非表示・入力が空のとき: preventDefault は走らず通常どおり send が呼ばれる（send 側で空チェック）
        quotaVisible = false; inputValue = ""; sent = false;
        if (trigger("pointerdown")) throw new Error("クォータ非表示なのに preventDefault された");
        click();
        if (!sent) throw new Error("通常経路で send が呼ばれなかった");

        console.log("OK");
        """.replace("__SEND_BLOCK__", send_block)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
            f.write(js_code)
            path = f.name
        try:
            res = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, res.returncode, res.stderr)
        finally:
            os.unlink(path)
        self.assertIn("OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
