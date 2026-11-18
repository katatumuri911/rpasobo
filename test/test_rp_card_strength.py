import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRpCardStrength(unittest.TestCase):
    """背景表示「カードの残り具合」スライダーの仕様準拠テスト（T1〜T16, P1〜P7）"""

    def setUp(self):
        self.app_js_path = os.path.join(ROOT, "public", "app.js")
        self.style_css_path = os.path.join(ROOT, "public", "style.css")
        self.index_html_path = os.path.join(ROOT, "public", "index.html")

        with open(self.app_js_path, "r", encoding="utf-8") as f:
            self.app_js = f.read()
        with open(self.style_css_path, "r", encoding="utf-8") as f:
            self.style_css = f.read()
        with open(self.index_html_path, "r", encoding="utf-8") as f:
            self.index_html = f.read()

        m = re.search(r"/\* == RP-CARD-BEGIN == \*/([\s\S]*?)/\* == RP-CARD-END == \*/", self.app_js)
        self.assertIsNotNone(m, "/* == RP-CARD-BEGIN == */ マーカーが public/app.js に見つかりません")
        self.rp_card_js = m.group(1)

    def _run_node_script(self, test_body_js):
        """Node 上で DOM/タイマーのスタブ環境を整えて JS テストを実行する"""
        harness = """
        class ClassList {
            constructor() { this._classes = new Set(); }
            add(...args) { args.forEach(c => this._classes.add(c)); }
            remove(...args) { args.forEach(c => this._classes.delete(c)); }
            toggle(c, force) {
                if (force === undefined) force = !this._classes.has(c);
                force ? this._classes.add(c) : this._classes.delete(c);
                return force;
            }
            contains(c) { return this._classes.has(c); }
        }

        class ElementStub {
            constructor(id) {
                this.id = id;
                this.classList = new ClassList();
                this.style = {
                    _props: {},
                    setProperty(k, v) { this._props[k] = String(v); },
                    removeProperty(k) { delete this._props[k]; },
                    getPropertyValue(k) { return this._props[k] || ""; }
                };
                this.attributes = {};
                this.listeners = {};
                this.disabled = false;
                this.value = "50";
                this.textContent = "";
            }
            setAttribute(k, v) { this.attributes[k] = String(v); }
            getAttribute(k) { return this.attributes[k]; }
            removeAttribute(k) { delete this.attributes[k]; }
            addEventListener(event, fn, opts) {
                (this.listeners[event] = this.listeners[event] || []).push(fn);
            }
            dispatchEvent(event) {
                const fns = this.listeners[event.type] || [];
                for (const fn of fns) fn(event);
            }
        }

        const elements = {
            rpCardStrength: new ElementStub("rpCardStrength"),
            rpCardValue: new ElementStub("rpCardValue"),
            sideOpacityRow: new ElementStub("sideOpacityRow"),
            chatLog: new ElementStub("chatLog"),
            rpToggle: new ElementStub("rpToggle"),
            sideRpToggle: new ElementStub("sideRpToggle")
        };
        const body = new ElementStub("body");
        const docListeners = {};
        const winListeners = {};

        const documentStub = {
            body,
            hidden: false,
            activeElement: null,
            addEventListener(event, fn, opts) {
                (docListeners[event] = docListeners[event] || []).push(fn);
            },
            dispatchEvent(event) {
                const fns = docListeners[event.type] || [];
                for (const fn of fns) fn(event);
            }
        };

        const windowStub = {
            addEventListener(event, fn, opts) {
                (winListeners[event] = winListeners[event] || []).push(fn);
            },
            dispatchEvent(event) {
                const fns = winListeners[event.type] || [];
                for (const fn of fns) fn(event);
            }
        };

        const storage = {};
        const localStorageStub = {
            getItem(k) { return k in storage ? storage[k] : null; },
            setItem(k, v) { storage[k] = String(v); },
            removeItem(k) { delete storage[k]; }
        };

        let nowTime = 1000;
        const activeTimers = [];
        let timerSeq = 1;

        const setTimeoutStub = (fn, delay) => {
            const id = timerSeq++;
            activeTimers.push({ id, fn, triggerAt: nowTime + (delay || 0), cleared: false });
            return id;
        };

        const clearTimeoutStub = (id) => {
            const t = activeTimers.find(x => x.id === id);
            if (t) t.cleared = true;
        };

        function advanceTime(ms) {
            nowTime += ms;
            for (const t of activeTimers) {
                if (!t.cleared && t.triggerAt <= nowTime) {
                    t.cleared = true;
                    t.fn();
                }
            }
        }

        let mockMobileLayout = true;
        let rpMode = true;

        const globalStubs = {
            console,
            Date: {
                now: () => nowTime
            },
            setTimeout: setTimeoutStub,
            clearTimeout: clearTimeoutStub,
            document: documentStub,
            window: windowStub,
            localStorage: localStorageStub,
            $: (id) => elements[id] || null,
            getStorage: (k, fb = "") => (k in storage ? storage[k] : fb),
            setStorage: (k, v) => { storage[k] = String(v); },
            isMobileLayout: () => mockMobileLayout,
            rpMode
        };
        Object.assign(globalThis, globalStubs);

        // --- Execute Module Code ---
        __MODULE_CODE__

        // --- Execute Test Body ---
        try {
            __TEST_BODY__
            console.log("PASS");
        } catch (err) {
            console.error(err && err.stack ? err.stack : String(err));
            process.exit(1);
        }
        """.replace("__MODULE_CODE__", self.rp_card_js).replace("__TEST_BODY__", test_body_js)

        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
            f.write(harness)
            path = f.name
        try:
            res = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, res.returncode, f"Node test failed:\nStdout: {res.stdout}\nStderr: {res.stderr}")
            self.assertIn("PASS", res.stdout)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    # -------------------------------------------------------------------------
    # 振る舞いテスト（T1〜T16）
    # -------------------------------------------------------------------------

    def test_t1_default_50_matches_c(self):
        """T1: 50% で各段が C に一致（text=.38 / user=.88 / code=.35 等）"""
        js = """
        const tiers = window.__rpCard.RP_TIERS;
        for (const [key, tier] of Object.entries(tiers)) {
            const alpha = window.__rpCard.calcRpTierAlpha(tier, 0.5);
            if (Math.abs(alpha - tier.C) > 0.001) {
                throw new Error(`Tier ${key} at 50% got ${alpha}, expected C=${tier.C}`);
            }
        }
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["text"], 0.5) - 0.38) > 0.001) throw new Error("text!=0.38");
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["user"], 0.5) - 0.88) > 0.001) throw new Error("user!=0.88");
        """
        self._run_node_script(js)

    def test_t2_floor_0_matches_f(self):
        """T2: 0% で各段が F に一致（text=0 / user=.12 / tool=.40 / input=.60）"""
        js = """
        const tiers = window.__rpCard.RP_TIERS;
        for (const [key, tier] of Object.entries(tiers)) {
            const alpha = window.__rpCard.calcRpTierAlpha(tier, 0.0);
            if (Math.abs(alpha - tier.F) > 0.001) {
                throw new Error(`Tier ${key} at 0% got ${alpha}, expected F=${tier.F}`);
            }
        }
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["text"], 0.0) - 0.00) > 0.001) throw new Error("text!=0");
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["user"], 0.0) - 0.12) > 0.001) throw new Error("user!=0.12");
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["tool"], 0.0) - 0.40) > 0.001) throw new Error("tool!=0.40");
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["input"], 0.0) - 0.60) > 0.001) throw new Error("input!=0.60");
        """
        self._run_node_script(js)

    def test_t3_ceiling_100_matches_u(self):
        """T3: 100% で各段が U に一致（text=.95 / user=.98）"""
        js = """
        const tiers = window.__rpCard.RP_TIERS;
        for (const [key, tier] of Object.entries(tiers)) {
            const alpha = window.__rpCard.calcRpTierAlpha(tier, 1.0);
            if (Math.abs(alpha - tier.U) > 0.001) {
                throw new Error(`Tier ${key} at 100% got ${alpha}, expected U=${tier.U}`);
            }
        }
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["text"], 1.0) - 0.95) > 0.001) throw new Error("text!=0.95");
        if (Math.abs(window.__rpCard.calcRpTierAlpha(tiers["user"], 1.0) - 0.98) > 0.001) throw new Error("user!=0.98");
        """
        self._run_node_script(js)

    def test_t4_monotonic_increase_by_step_5(self):
        """T4: 0→100 が単調増加（5刻み）"""
        js = """
        const tiers = window.__rpCard.RP_TIERS;
        for (const [key, tier] of Object.entries(tiers)) {
            let prev = -1;
            for (let v = 0; v <= 100; v += 5) {
                const alpha = window.__rpCard.calcRpTierAlpha(tier, v / 100);
                if (alpha < prev) {
                    throw new Error(`Tier ${key} is not monotonic: at ${v}% got ${alpha} < prev ${prev}`);
                }
                prev = alpha;
            }
        }
        """
        self._run_node_script(js)

    def test_t5_out_of_range_clamped(self):
        """T5: 範囲外（-20 / 140）がクランプされる"""
        js = """
        if (window.__rpCard.clampRpCardStrength(-20) !== 0) throw new Error("-20 not clamped to 0");
        if (window.__rpCard.clampRpCardStrength(140) !== 100) throw new Error("140 not clamped to 100");
        """
        self._run_node_script(js)

    def test_t6_default_empty_and_200_clamp(self):
        """T6: 未設定・空文字の既定が 50、200 は 100 にクランプ"""
        js = """
        if (window.__rpCard.clampRpCardStrength(null) !== 50) throw new Error("null not default 50");
        if (window.__rpCard.clampRpCardStrength(undefined) !== 50) throw new Error("undefined not default 50");
        if (window.__rpCard.clampRpCardStrength("") !== 50) throw new Error("'' not default 50");
        if (window.__rpCard.clampRpCardStrength("   ") !== 50) throw new Error("'   ' not default 50");
        if (window.__rpCard.clampRpCardStrength(200) !== 100) throw new Error("200 not clamped to 100");
        """
        self._run_node_script(js)

    def test_t7_0_percent_decorations_and_crisp(self):
        """T7: 0% で --rp-bg-text / --rp-edge / --rp-shadow が alpha 0、rp-crisp が付かない"""
        js = """
        window.__rpCard.applyRpCardStrength(0);
        const style = document.body.style;
        const textVal = style.getPropertyValue("--rp-bg-text");
        const edgeVal = style.getPropertyValue("--rp-edge");
        const shadowVal = style.getPropertyValue("--rp-shadow");

        if (!textVal.includes("0)") && !textVal.includes("0.000)") && !textVal.includes(" 0)")) {
            throw new Error("--rp-bg-text alpha not 0: " + textVal);
        }
        if (!edgeVal.includes("0)") && !edgeVal.includes("0.000)") && !edgeVal.includes(" 0)")) {
            throw new Error("--rp-edge alpha not 0: " + edgeVal);
        }
        if (!shadowVal.includes("0)") && !shadowVal.includes("0.000)") && !shadowVal.includes(" 0)")) {
            throw new Error("--rp-shadow alpha not 0: " + shadowVal);
        }
        if (document.body.classList.contains("rp-crisp")) throw new Error("rp-crisp attached at 0%");
        if (!document.body.classList.contains("rp-thin")) throw new Error("rp-thin NOT attached at 0%");
        """
        self._run_node_script(js)

    def test_t8_100_percent_text_and_crisp(self):
        """T8: 100% で --rp-bg-text が rgba(16,16,24,0.95)、rp-crisp が付く"""
        js = """
        window.__rpCard.applyRpCardStrength(100);
        const style = document.body.style;
        const textVal = style.getPropertyValue("--rp-bg-text").replace(/\\s+/g, "");
        if (textVal !== "rgba(16,16,24,0.95)") throw new Error(`--rp-bg-text not rgba(16,16,24,0.95): ${textVal}`);
        if (!document.body.classList.contains("rp-crisp")) throw new Error("rp-crisp NOT attached at 100%");
        if (document.body.classList.contains("rp-thin")) throw new Error("rp-thin attached at 100%");
        """
        self._run_node_script(js)

    def test_t9_input_focus_set_at_50_percent(self):
        """T9: 50% で --rp-bg-input-focus が設定される（①の回帰）。色は input と同じ（フォーカスで色を変えない）"""
        js = """
        window.__rpCard.applyRpCardStrength(50);
        const style = document.body.style;
        const focusVal = style.getPropertyValue("--rp-bg-input-focus");
        if (!focusVal || !focusVal.includes("43, 43, 51")) {
            throw new Error(`--rp-bg-input-focus invalid or missing: ${focusVal}`);
        }
        if (style.getPropertyValue("--rp-bg-inputFocus")) {
            throw new Error("Found camelCase --rp-bg-inputFocus in body style!");
        }
        """
        self._run_node_script(js)

    def test_t10_change_saves_to_storage(self):
        """T10: change で localStorage に保存"""
        js = """
        const slider = $("rpCardStrength");

        // 75 に変更して change を発火
        slider.value = "75";
        slider.dispatchEvent({ type: "change" });
        if (localStorage.getItem("rpCardStrength") !== "75") {
            throw new Error("localStorage rpCardStrength not 75: " + localStorage.getItem("rpCardStrength"));
        }
        if ($("rpCardValue").textContent !== "75%") {
            throw new Error("rpCardValue text not 75%: " + $("rpCardValue").textContent);
        }
        """
        self._run_node_script(js)

    def test_t11_no_peek_without_pointer(self):
        """T11: ポインタ無しの input で peek が出ない（キーボード対策）"""
        js = """
        const slider = $("rpCardStrength");
        slider.value = "60";
        slider.dispatchEvent({ type: "input" });
        if (document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek triggered without pointer!");
        }
        """
        self._run_node_script(js)

    def test_t12_peek_with_pointer_and_pointer_id_check(self):
        """T12: ポインタ有りで peek が出る。別ポインタの pointerup では解除されない"""
        js = """
        const slider = $("rpCardStrength");
        slider.dispatchEvent({ type: "pointerdown", pointerId: 42 });
        slider.value = "60";
        slider.dispatchEvent({ type: "input" });

        if (!document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek did not trigger with pointer!");
        }

        // 別の pointerId 99 の pointerup では解除されない
        window.dispatchEvent({ type: "pointerup", pointerId: 99 });
        if (!document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek dismissed by mismatched pointerId 99!");
        }
        """
        self._run_node_script(js)

    def test_t13_min_duration_150ms_prevents_flicker(self):
        """T13: 解除直後（150ms 未満）は rp-peek が残る（点滅しない）"""
        js = """
        const slider = $("rpCardStrength");
        slider.dispatchEvent({ type: "pointerdown", pointerId: 10 });
        slider.dispatchEvent({ type: "input" });

        if (!document.body.classList.contains("rp-peek")) throw new Error("peek did not start");

        // 20ms 後に離す
        advanceTime(20);
        window.dispatchEvent({ type: "pointerup", pointerId: 10 });

        // 解除直後は 150ms 経過していないので peek が残る
        if (!document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek dismissed prematurely (<150ms)!");
        }

        // 残り 130ms (合計 160ms) 進めると解除される
        advanceTime(140);
        if (document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek NOT dismissed after 150ms elapsed!");
        }
        """
        self._run_node_script(js)

    def test_t14_retouch_within_150ms_cancels_timer(self):
        """T14: 150ms 以内の再タッチでタイマーが解除され、ドラッグ中に落ちない（③の回帰）"""
        js = """
        const slider = $("rpCardStrength");
        slider.dispatchEvent({ type: "pointerdown", pointerId: 11 });
        slider.dispatchEvent({ type: "input" });

        // 20ms 後に離す（timer が 130ms 後にセットされる）
        advanceTime(20);
        window.dispatchEvent({ type: "pointerup", pointerId: 11 });

        // 30ms 後（開始から 50ms 後）に再タッチ
        advanceTime(30);
        slider.dispatchEvent({ type: "pointerdown", pointerId: 12 });
        slider.dispatchEvent({ type: "input" });

        // さらに 150ms 進める（最初のタイマー発火予定時刻を通過）
        advanceTime(150);

        // 前回のタイマーで peek が解除されてはならない
        if (!document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek dropped during active retouch drag due to stale timer!");
        }
        """
        self._run_node_script(js)

    def test_t15_desktop_no_peek(self):
        """T15: デスクトップ（isMobileLayout()=false）で peek が出ない"""
        js = """
        mockMobileLayout = false;
        const slider = $("rpCardStrength");
        slider.dispatchEvent({ type: "pointerdown", pointerId: 20 });
        slider.dispatchEvent({ type: "input" });

        if (document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek triggered on desktop layout!");
        }
        """
        self._run_node_script(js)

    def test_t16_rpmode_off_disables_and_cleans_up(self):
        """T16: rpMode OFF で disabled になり、変数が removeProperty され、rp-peek が消える"""
        js = """
        const slider = $("rpCardStrength");
        slider.dispatchEvent({ type: "pointerdown", pointerId: 30 });
        slider.dispatchEvent({ type: "input" });
        if (!document.body.classList.contains("rp-peek")) throw new Error("peek not active");

        // rpMode を OFF にして再適用
        rpMode = false;
        window.__rpCard.syncRpCardStrengthUI();

        if (!slider.disabled) throw new Error("slider not disabled when rpMode is OFF");
        if ($("rpCardReset") && !$("rpCardReset").disabled) throw new Error("reset button not disabled when rpMode is OFF");
        if (!$("sideOpacityRow").classList.contains("is-off")) throw new Error("sideOpacityRow missing .is-off");
        if (document.body.classList.contains("rp-peek")) throw new Error("rp-peek remaining after rpMode OFF");

        const style = document.body.style;
        if (style.getPropertyValue("--rp-bg-text")) throw new Error("--rp-bg-text not removed");
        if (style.getPropertyValue("--rp-edge")) throw new Error("--rp-edge not removed");
        if (style.getPropertyValue("--rp-shadow")) throw new Error("--rp-shadow not removed");
        if (document.body.classList.contains("rp-crisp")) throw new Error("rp-crisp remaining");
        if (document.body.classList.contains("rp-thin")) throw new Error("rp-thin remaining");
        """
        self._run_node_script(js)

    def test_t17_continuous_drag_longer_than_150ms_dismisses_immediately(self):
        """T17: 150ms 以上の連続ドラッグ後は指を離した瞬間に即座に peek が解除される（タイマー待ちなし）"""
        js = """
        const slider = $("rpCardStrength");
        slider.dispatchEvent({ type: "pointerdown", pointerId: 45 });

        // 0ms〜200ms にかけて 40ms ごとに input が発火する連続ドラッグ
        for (let t = 0; t <= 200; t += 40) {
            advanceTime(40);
            slider.dispatchEvent({ type: "input" });
        }
        if (!document.body.classList.contains("rp-peek")) throw new Error("peek not active during drag");

        // 最後の input から 10ms 後（合計 250ms）に指を離す
        advanceTime(10);
        window.dispatchEvent({ type: "pointerup", pointerId: 45 });

        // 150ms 以上経過しているため即座に解除され、タイマーも残らない
        if (document.body.classList.contains("rp-peek")) {
            throw new Error("rp-peek did not dismiss immediately after >150ms drag!");
        }
        if (window.__rpCard.getPeekTimer() !== null) {
            throw new Error("rpPeekTimer unexpectedly scheduled after >150ms drag!");
        }
        """
        self._run_node_script(js)

    def test_t18_sync_during_pointer_drag_does_not_clobber_active_value(self):
        """T18: ドラッグ操作中（pointerdown 中）に syncRpCardStrengthUI が走っても入力値が上書きされない"""
        js = """
        const slider = $("rpCardStrength");
        // 初期状態 50
        if (slider.value !== "50") throw new Error("initial value not 50");

        // ドラッグ開始して 85 に変更
        slider.dispatchEvent({ type: "pointerdown", pointerId: 50 });
        slider.value = "85";
        slider.dispatchEvent({ type: "input" });

        if (slider.value !== "85") throw new Error("slider not 85 after input");

        // localStorage はまだ 50 のまま（change 前）
        // この間にバックグラウンド poll() 等で syncRpCardStrengthUI() が呼ばれたとする
        window.__rpCard.syncRpCardStrengthUI();

        // ドラッグ中なので 85 が保持され、localStorage の 50 で巻き戻らない
        if (slider.value !== "85") {
            throw new Error("slider value was clobbered by sync during active drag: " + slider.value);
        }

        // 指を離して change が発火すると 85 が保存される
        window.dispatchEvent({ type: "pointerup", pointerId: 50 });
        slider.dispatchEvent({ type: "change" });
        if (localStorage.getItem("rpCardStrength") !== "85") {
            throw new Error("localStorage not saved as 85 after change");
        }
        """
        self._run_node_script(js)

    def test_t19_sync_during_keyboard_focus_does_not_clobber_active_value(self):
        """T19: キーボードフォーカス操作中に syncRpCardStrengthUI が走っても入力値が巻き戻らない"""
        js = """
        const slider = $("rpCardStrength");
        document.activeElement = slider;

        // キーボード矢印キーで 70 に変更
        slider.value = "70";
        slider.dispatchEvent({ type: "input" });

        // バックグラウンド同期が走っても巻き戻らない
        window.__rpCard.syncRpCardStrengthUI();
        if (slider.value !== "70") {
            throw new Error("slider value was clobbered by sync during keyboard focus: " + slider.value);
        }
        """
        self._run_node_script(js)

    def test_t20_change_event_applies_and_clamps(self):
        """T20: change イベントが直接発火した場合もスタイル適用・クランプ・保存が同期される"""
        js = """
        const slider = $("rpCardStrength");
        slider.value = "90";
        slider.dispatchEvent({ type: "change" });

        if ($("rpCardValue").textContent !== "90%") throw new Error("label text not 90%");
        if (slider.getAttribute("aria-valuetext") !== "90%") throw new Error("aria-valuetext not 90%");
        if (localStorage.getItem("rpCardStrength") !== "90") throw new Error("storage not 90");
        """
        self._run_node_script(js)

    # -------------------------------------------------------------------------
    # 静的スタイル・構造検証テスト（P1〜P7）
    # -------------------------------------------------------------------------

    def test_p1_css_surfaces_use_rp_bg_variables(self):
        """P1: CSS: 本文/自分/コード/添付/入力欄/入力欄フォーカスが var(--rp-bg-*) を使っている"""
        self.assertIn("var(--rp-bg-text", self.style_css)
        self.assertIn("var(--rp-bg-user", self.style_css)
        self.assertIn("var(--rp-bg-code", self.style_css)
        self.assertIn("var(--rp-bg-inline", self.style_css)
        self.assertIn("var(--rp-bg-file", self.style_css)
        self.assertIn("var(--rp-bg-input,", self.style_css)
        self.assertIn("var(--rp-bg-input-focus", self.style_css)
        self.assertIn("var(--rp-bg-chip", self.style_css)
        self.assertIn("var(--rp-bg-attach", self.style_css)

    def test_p2_css_controls_do_not_use_rp_bg(self):
        """P2: CSS: 送信・停止・クォータ・FAB が --rp-bg- を使っていない"""
        btn_stop = re.findall(r"\.btn-stop-wait\s*\{[^}]*\}", self.style_css)
        self.assertTrue(len(btn_stop) > 0)
        for b in btn_stop:
            self.assertNotIn("--rp-bg-", b, ".btn-stop-wait contains --rp-bg-")

        quota = re.findall(r"\.ag-quota[^{]*\{[^}]*\}", self.style_css)
        for q in quota:
            self.assertNotIn("--rp-bg-", q, ".ag-quota contains --rp-bg-")

        fab = re.findall(r"\.mobile-fab[^{]*\{[^}]*\}", self.style_css)
        for f in fab:
            self.assertNotIn("--rp-bg-", f, ".mobile-fab contains --rp-bg-")

    def test_p3_touch_hover_reset_uses_rp_bg_tool(self):
        """P3: CSS: タッチ用ホバー復帰が --rp-bg-tool を使っている（⑤）

        `.choices-toggle-btn` は以前 `tool-alt`（0.70）という別段だったが、
        隣の 🔁/🎨/⋯ と濃さが違って見えるため `tool`（0.85）に統合した。
        """
        blocks = re.findall(r"@media\s*\(\s*hover:\s*none\s*\)[^{]*\{([\s\S]*?)(?:@media|\Z)", self.style_css)
        target = [b for b in blocks if "choice-item-btn" in b]
        self.assertTrue(len(target) > 0, "選択肢・ツールのタッチ端末用 @media ブロックが見つかりません")
        block = target[0]
        self.assertIn("var(--rp-bg-tool,", block)
        self.assertIn("#chatLog.rp .choices-toggle-btn:hover", block)
        self.assertNotIn("--rp-bg-tool-alt", self.style_css, "別基調の段は廃止したので残さない")
        self.assertNotIn("tool-alt", self.app_js, "RP_TIERS からも廃止済みであること")

    def test_p4_html_side_opacity_row_outside_bottom_section(self):
        """P4: HTML: #sideOpacityRow が #sideBottomSection の外にある"""
        self.assertIn('id="sideOpacityRow"', self.index_html)
        idx_bottom = self.index_html.find('id="sideBottomSection"')
        idx_close = self.index_html.find('</div>', idx_bottom)
        idx_opacity = self.index_html.find('id="sideOpacityRow"')

        self.assertTrue(idx_bottom >= 0 and idx_close >= 0 and idx_opacity >= 0)
        self.assertGreater(idx_opacity, idx_close, "#sideOpacityRow は #sideBottomSection の外（閉じタグの後）に配置される必要があります")

        idx_aside_close = self.index_html.find('</aside>', idx_opacity)
        self.assertTrue(idx_aside_close > idx_opacity, "#sideOpacityRow は #sidebar の内側に配置される必要があります")

    def test_p5_slider_touch_action_and_no_drawer_swipe(self):
        """P5: CSS: .rop-range に touch-action: none、行に data-no-drawer-swipe"""
        range_css = re.search(r"\.rop-range\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(range_css)
        self.assertIn("touch-action: none", range_css.group(0))

        self.assertIn('id="sideOpacityRow" class="side-opacity-row" data-no-drawer-swipe', self.index_html)
        input_tag = re.search(r'<input[^>]*id="rpCardStrength"[^>]*>', self.index_html)
        self.assertIsNotNone(input_tag)
        self.assertIn("data-no-drawer-swipe", input_tag.group(0))

    def test_p6_peek_block_no_display_none(self):
        """P6: CSS: peek ブロックに display: none が無い／#sidebar > :not(#sideOpacityRow) を使う"""
        peek_block = re.search(r"body\.rp-peek\s*#sidebar\s*\{[\s\S]*?body\.rp-peek\s*#sideOpacityRow\s*\{[^}]*\}", self.style_css)
        self.assertIsNotNone(peek_block, "peek スタイルブロックが見つかりません")
        content = peek_block.group(0)
        self.assertNotIn("display: none", content, "peek スタイルに display: none を含めてはなりません")
        self.assertIn("#sidebar > :not(#sideOpacityRow)", content)

    def test_p7_cache_busting_versions_updated(self):
        """P7: HTML: CSS/JS に ?v=<整数> のキャッシュバスターが付いている
        数を固定すると正当な更新のたびに落ちる（＝テストが「前回の作業メモ」になる）ので、形だけを見る"""
        for asset in ("style.css", "app.js"):
            m = re.search(r'(?:href|src)="%s\?v=(\d+)"' % re.escape(asset), self.index_html)
            self.assertIsNotNone(
                m, f"{asset} に ?v=<整数> が付いていない（更新してもブラウザが古いファイルを使い続ける）")


if __name__ == "__main__":
    unittest.main()
