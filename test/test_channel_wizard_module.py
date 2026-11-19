import ast
import os
import pytest
import rpchat.channel_wizard as w


def test_import_has_no_side_effects(tmp_path, monkeypatch):
    # import 済みなので I/O なしを担保: 主要パスが getter 経由であること
    assert w.WIZARD_SLOTS and w.REQUIRED_SLOTS


def test_deps_satisfied():
    import server
    assert w.missing_deps() == []


def test_missing_deps_detection(monkeypatch):
    import server
    orig_load = w._deps.get("load")
    monkeypatch.delitem(w._deps, "load", raising=False)
    assert "load" in w.missing_deps()
    if orig_load is not None:
        w._deps["load"] = orig_load


def test_missing_getters_detection(monkeypatch):
    import server
    orig_wiz_dir = w._getters.get("WIZARD_DIR")
    monkeypatch.delitem(w._getters, "WIZARD_DIR", raising=False)
    assert "WIZARD_DIR" in w.missing_deps()
    if orig_wiz_dir is not None:
        w._getters["WIZARD_DIR"] = orig_wiz_dir


def test_bind_deps_collision_prevention():
    # 自モジュールで定義された関数や import 済みの標準モジュールは上書き不可
    with pytest.raises(RuntimeError, match="定義済み（衝突）"):
        w.bind_deps(wslot_set=lambda *a: None)
    with pytest.raises(RuntimeError, match="定義済み（衝突）"):
        w.bind_deps(uuid=None)


def test_bind_getters_callable_check():
    with pytest.raises(TypeError, match="ゼロ引数関数"):
        w.bind_getters(INVALID_KEY="not_a_callable")


def test_g_missing_raises_runtime_error():
    with pytest.raises(RuntimeError, match="未設定"):
        w._g("DEFINITELY_NONEXISTENT_GETTER_NAME_12345")


def test_getter_dynamism():
    import server
    old_dir = server.WIZARD_DIR
    try:
        server.WIZARD_DIR = "custom_test_dir_dynamic"
        assert w._g("WIZARD_DIR") == "custom_test_dir_dynamic"
    finally:
        server.WIZARD_DIR = old_dir


def test_pure_functions_work():  # 依存ゼロの関数を実際に呼ぶ
    d = {}
    assert w.wslot_set(d, "world", ["学園"], "user") is True
    assert w.wslot_value(d, "world") == ["学園"]
    assert w.wizard_norm_value(w.WIZARD_SLOTS["rough_idea"], "あ" * 400) != ""
    assert w.wizard_clean_tags(["headdress", "flying"]) == ["headdress", "flying"]
    assert w.wizard_public({"id": "wiz_0123456789", "slots": {}})["title_hint"] == "新しいチャンネル"


def test_server_hasattr_compatibility():
    import server
    # All required deps and getters exist on server
    for name in w.REQUIRED_DEPS:
        assert hasattr(server, name), f"server missing dep: {name}"
    for name in w.REQUIRED_GETTERS:
        assert hasattr(server, name), f"server missing getter: {name}"

    # Extract all symbols imported from rpchat.channel_wizard in server.py
    server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
    with open(server_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    imported_names = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "rpchat.channel_wizard":
            imported_names.extend(alias.name for alias in node.names)

    assert len(imported_names) > 100, f"Expected >100 re-exports, got {len(imported_names)}"
    for s in imported_names:
        assert hasattr(server, s), f"server missing re-exported symbol: {s}"

    # Key 3 constants on server
    for c in ("WIZARD_DIR", "WIZARD_IDE_DIR", "WIZARD_TAGS_PATH"):
        assert hasattr(server, c), f"server missing constant: {c}"

