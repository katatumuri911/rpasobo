import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestComposerUI(unittest.TestCase):
    """コンポーザー（入力欄）・お気に入りチップ・マイクボタンのUI回帰防止テスト"""

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


if __name__ == "__main__":
    unittest.main()
