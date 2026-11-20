# 仕様書: fetch 署名キャッシュ（第1段の廃止による高速化）

- 対象: `google_ai.py` / `server.py` / `public/index.html` / `public/app.js`
- 状態: 設計（未実装）
- 既定: **ON**（`google_fetch_cache=True`）。OFF は「異常を感じたときの逃げ道」として残す
- 関連: `tools/ab_filter_arms.py`（A/B ハーネス）、`docs/rotation_state_machine.md`

---

## 1. 背景と目的

### 1.1 現状の実測値

| 項目 | 実測 |
|---|---|
| fetch 段（第1段）所要時間 | **0.9〜37.6 秒**（出力 10 トークン固定・thinking 0） |
| 本番トータル（空いている時間帯） | 中央 13〜15 秒 |
| 本番トータル（混雑時） | 中央 32.6 秒 / 最大 101 秒 |
| 署名キャッシュ実験（n=4・混雑時） | 中央 **32.6 → 21.8 秒**、最大 **101 → 49.5 秒** |

第1段の時間はモデルの計算ではなく **Vertex 側のキュー待ち**が支配的である（出力が 10 トークン固定でも 37 秒かかる、リトライしても短くならない）。
したがってリトライ・タイムアウト・thinking の調整では短縮できない。**往復そのものを消す**のが唯一の有効な手段。

### 1.2 目的

第1段のリクエストを**キャッシュした `thoughtSignature` で置き換え**、1 生成あたりの API 往復を 2 回 → 1 回にする。

### 1.3 非目的

- フィルタ回避率の改善（本変更は速度のみを目的とする。コンテキストの渡し方は現行と完全に同一）
- AI Studio（`provider=google`）の救済（入力段階の `PROHIBITED_CONTENT` は別問題）
- `google_fetch=False`（直接生成）経路の変更

---

## 2. なぜ第1段は削除できるのか

第1段のリクエストは **プロンプトに依存しない定数**である（`google_ai.py:1856-1871`）。

```python
first_payload = {
    "systemInstruction": {"parts": [{"text": "…必ず fetch_prompt を呼び出してコンテキストを取得してください。…"}]},
    "contents": [{"role": "user", "parts": [{"text": "fetch_prompt を呼び出してコンテキストを取得してください。"}]}],
    "tools": [{"functionDeclarations": [{"name": fetch_name, "description": "…引数はない。"}]}],
    "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [fetch_name]}},
    "generationConfig": _step1_generation_config(model),
}
```

- チャンネルの設定・会話・ユーザー発言は**一切含まれない**
- `functionDeclarations` に `parameters` が無い＝ `args` は常に `{}`
- したがって返ってくる `model_content` も定数：

```json
{"parts":[{"functionCall":{"id":"call_6937812503714667224","args":{},"name":"fetch_prompt"},
           "thoughtSignature":"AY89a18oNGtpth86COvK7yl04k+agf+…"}],"role":"model"}
```

この `model_content` は第2段でそのまま `contents[2]` として送られるだけなので、
**同じ値を再利用すれば第1段を丸ごと省略できる。**

コンテキストの実体は第2段の `functionResponse.response.context` に入るため、**コンテキストの渡し方は現行と同一**である。

> **未検証の前提**: 「再生した署名でもフィルタ特性（SAFETY 切断率）が変わらない」ことはまだ証明していない。
> 構造上は同一だが、受入基準 7 と §11-6 で必ず確認する（外れたら既定 OFF に戻す）。

### 2.1 実測済みの前提

| 検証 | 結果 |
|---|---|
| 署名を偽造（`thoughtSignature` をでっち上げ） | **400 `INVALID_ARGUMENT`: Function call is missing a thought_signature** |
| 実在の署名を別セッション・別プロンプトで再利用 | **3/3 成功** |
| 再利用時の副作用 | 1 回だけ空応答を観測（→ フォールバック必須の根拠） |

---

## 3. 設計

### 3.1 キャッシュキー

```
"<provider>|<model>"     例: "vertex|gemini-3.8-flash"
```

プロンプト非依存なので **キーにプロンプトを含めない**（含めるとヒット率が 0 になる）。

### 3.2 フィンガープリント（安全弁）

第1段のリクエスト内容が変わると署名は無効になる。コード変更（文言修正・引数追加）を検知するため、
キャッシュに次のハッシュを保存し、**読み込み時に一致しなければ破棄**する。

```python
fp = sha256(json.dumps({
    "sys": <第1段 systemInstruction の text>,
    "user": <第1段 user parts の text>,
    "decl": <functionDeclarations の JSON>,
    "name": fetch_name,
}, ensure_ascii=False, sort_keys=True))
```

### 3.3 保存形式

`<DATA>/fetch_signature.json`（`DATA` は `google_ai.DATA`、既定 `./data`）

```json
{
  "version": 1,
  "entries": {
    "vertex|gemini-3.8-flash": {
      "parts": [{"functionCall": {"id": "call_6937…", "args": {}, "name": "fetch_prompt"},
                 "thoughtSignature": "AY89…"}],
      "role": "model",
      "fp": "9f2c…",
      "saved_ts": 1760000000,
      "disabled": false
    }
  }
}
```

- **プロンプト・本文・セッション ID は保存しない**（署名とメタ情報のみ）
- **失敗カウント・休み状態・成功回数はファイルに保存しない**（すべてメモリのみ）
  - 理由 1: 毎ターン書き込むとリクエスト経路でファイル IO が走る
  - 理由 2: **アプリを再起動したら自動で復活してほしい**から（再起動は「もう一度試せ」の合図として自然）
- 読み込みはプロセス起動後の**初回 1 回だけ**。以後は常駐 dict を使う（毎ターンの read + JSON parse を避ける）
- 書き込みは `os.replace` による原子的更新（既存 `google_ai.py:375` と同方式）＋**ユニークな tmp 名**（`<path>.<pid>.tmp`）
- 変更時のみ書き込む（`put` / 署名の破棄時）。`mark()` はメモリ更新のみでファイルを触らない
- エントリ上限 20 件（超過時は `saved_ts` の古い順に削除）。1 エントリ 64KB 超は `disabled` にして以後試さない
- 読み書きはモジュールレベルの `threading.Lock` で保護（API ワーカースレッドは複数）
- プロセスは単一（マルチプロセス考慮不要）

### 3.4 処理フロー

#### キャッシュヒット時（ON・署名あり）

```
[1] 署名キャッシュ読み込み（fp 一致・休み中でない・あきらめていない）
      ↓ あり
[2] 第2段のみ実行（contents = [固定 user, キャッシュ parts, functionResponse(context=prompt)]）
      ↓
[3] 成功 → 本文返却（API 往復 1 回・所要時間 = 現行の第2段のみ）
```

#### ミス時（初回・破棄後）

```
[1] キャッシュなし → 現行どおり第1段を実行（fetch_prompt を取得）
[2] 成功した model_content を保存（追加コスト 0）
[3] 第2段を実行
```

#### フォールバック（キャッシュ起因の失敗）

**退避するのは「署名が原因だと断定できる失敗」だけ**に限定する。

```
[A] 第2段が 400 で `_is_tool_400(msg)` に該当（thought_signature / functionCall 系）
      → 署名が原因と断定できる
[B] 第2段が空応答（text=="" かつ block_reason 無し かつ finish_reason がブロック系でない）
      → 署名を疑う
      ↓ A または B
[F1] sig_cache_mark(ok=False)  （失敗カウント +1 → 次の 1 ターン休む。2回目は 3 ターン休む。3回目で中止）
[F2] 残り予算がある場合のみ、同一試行内で「現行 2 段階」をやり直す（第1段から）
     条件: (now - t0) < timeout。これを超えていたら再試行せず retryable=True で返す
[F3] その結果を返す（ユーザーにはエラーを見せない）
```

| 事象 | 退避するか | 理由 |
|---|---|---|
| 400 `_is_tool_400` | **する** | 署名が原因 |
| 空応答 | **する** | 署名を疑う |
| `block_reason`（入力ブロック） | **しない** | 入力は現行と同一。署名とは無関係 |
| `finish_reason` がブロック系（SAFETY 等） | **しない** | 出力側の判定。退避するとブロックされたターンのコストが 2 倍になり、フィルタ挙動も現行と変わる |
| 429 / 5xx / ネットワーク例外 | **しない** | 再送すると負荷を増やす。例外をそのまま伝播する |
| 400 で `_is_tool_400` に該当しない | **しない** | `_request_with_fallbacks` の safety/thinking ラダーに任せる（現行どおり） |

#### 失敗したあとの扱い — 時間ではなく「ターン数」で数える

**分単位の待ち時間は意味がない。** プレイ中は 1 ターン約 30 秒なので、10 分 = 約 20 ターン =
実質そのセッションの残り全部。「10分休む」は「もう今日は使わない」と同じ意味になってしまう。

そこで **「次の何ターン休むか」** で数え、**3 回失敗したら中止**する。

| 失敗回数 | その後の動き | 意図 |
|---|---|---|
| 1 回目 | **次の 1 ターンだけ休む**（同じ署名で再挑戦） | 偶然かもしれない。1 ターン後にすぐ試す |
| 2 回目 | **次の 3 ターン休む** ＋ 署名を捨てる | 偶然ではない。次は第1段から取り直す |
| 3 回目 | **中止（あきらめる）** | このプロセスでは使わない。**アプリ再起動で復活** |
| ヒット成功 | カウント 0 に戻る | 復活 |

**中止後のコストはゼロ**（現行とまったく同じ経路。無駄なリクエストも出ない）。

##### 恒久的に壊れていた場合の総コスト

```
ターンA  試す → 400（約1秒の損）→ 2段階で本文 ✓        失敗1
ターンB  休む（現行と同じ速さ）
ターンC  試す → 400（約1秒の損）→ 2段階で本文 ✓        失敗2・署名破棄
ターンD〜F  休む
ターンG  ミス → 第1段から取得（← どうせ通る道。損なし）
ターンH  新しい署名で試す → 400（約1秒の損）→ 中止   失敗3
ターンI〜  現行と同じ。**二度と試さない**
```

**無駄は合計 3 秒だけ。** 以降は静かに今日と同じ速さで動き続ける。

##### 一時的だった場合

```
ターンA  試す → 400（1秒）→ 本文 ✓
ターンB  休む
ターンC  試す → 成功 ✓ → 復活（カウント0）
```

**2 ターンで復帰**する。

##### なぜ「中止」でよいのか

コストの非対称性:

| 状態 | 1 ターンあたりの損 |
|---|---|
| 壊れているのに毎回試す | 約 1 秒（400 が返るまでの待ち）× 全ターン |
| 中止して試さない | **0 秒**（今日と同じ経路。ただしキャッシュの速度利得も無い） |

壊れたまま試し続けても**利得はゼロで、1 秒ずつ損が積み上がるだけ**。だから 3 回で見切りをつけ、
復活は**人間が明確に意思表示する操作**に委ねるのが正しい。

##### 「再起動」とは何を指すか（重要）

中止状態（`_SIG_FAIL`）は **`server.py` の Python プロセス内のメモリ**にある。したがって:

| 操作 | 中止は解けるか |
|---|---|
| ブラウザの再読み込み（F5）・タブを閉じて開く | **解けない**（画面だけの操作） |
| スマホ側のアプリ再起動 | **解けない**（クライアント側） |
| **`start.bat` のウィンドウを閉じて起動し直す** | **解ける** |
| PC の再起動 | 解ける |
| コード更新のためにサーバーを落として上げ直す | 解ける |

`start.bat` は「このウィンドウは閉じないでください」と出したまま**何日も開きっぱなし**にされ得る。
その場合「中止」＝「何日も使わない」になってしまうので、**再起動より軽い復活手段を 1 つ用意する**。

##### 復活手段は「設定の OFF → ON」で足りる（UI 追加ゼロ）

中止状態で失うのは**速度の利得だけ**で、壊れるものは何もない（エラーも遅延も出ず、今日と同じ速さで動く）。
したがって**専用のボタンや API は作らない**。代わりに、実装 1 行で復活手段を確保する。

```python
# run_google_task から generate を呼ぶ直前
if not fetch_cache:
    google_ai.sig_cache_forget_fail_state(provider, model)   # OFF の間は失敗状態を持ち越さない
```

- 効果: 設定で「fetch段をキャッシュ」を **OFF → ON** に切り替えると、失敗状態が消えて**同じ署名でもう一度試す**
  （一過性の失敗だった場合はそのターンで即ヒットに戻る。コスト 0）
- **OFF は署名を捨てない**（消えるのは「失敗した」という記憶だけ）
- 既存の設定スイッチをそのまま使うので、画面も API もテストも増えない
- 中止したことは `print(f"[sig] give up {key}")` でログに残る（`RPCHAT_PERF_LOG` と同様、必要なときだけ見る）

##### 中止を「困ったこと」にしないための保険

中止の原因が**恒久的な仕様変更**なら、そもそも何をしても復活しないので放置でよい。
問題になり得るのは「Vertex の一時的な不調で空応答が 3 回続いた」ケースだが、これも
**失うのは速度の利得だけ**で、再起動（コード更新・PC 再起動時に必ず起きる）か設定の OFF→ON で戻る。

### 3.5 キャッシュを使わない条件

| 条件 | 理由 |
|---|---|
| `google_fetch_cache=False` | 手動で OFF にした（初期値は ON。異常時の逃げ道） |
| `fetch=False`（`google_fetch=False` または `images` あり、choices/memory 等） | 第1段が存在しない |
| `task_type != "user"` | 現行も `use_fetch` は user のみ |
| `fp` 不一致 | 第1段の内容が変わった |
| 休み中（`skip > 0`） | 直前に失敗した（次の 1〜3 ターンを休む） |
| 中止（`given_up`） | 3 回失敗した。**アプリ再起動で復活** |
| `disabled=True` | 応答が大きすぎて保存できなかったモデル（thinking モデルで thought パートが返ると該当し得る。`sig_cache_reset()` で解除） |
| `parts` に `thoughtSignature` が無い | 壊れたエントリ |

### 3.6 戻り値の拡張

`generate()` の戻り値に **`fetch_cache`** を追加する（`"hit"` / `"miss"` / `"off"` / `"hit_fallback"`）。
`fetch_called` の意味は変えない（キャッシュヒット時も `True`＝「fetch 機構でコンテキストを渡した」）。
`first_status` はキャッシュヒット時 `None`（HTTP ステータスではない値を入れない）。

内部専用キー **`_cache_fail`**（`"400"` / `"empty"`）を一時的に付け、`generate()` が `pop` してから返す。
**`meta` には入れない**（`meta` はステータス・ログ・UI に流れるため）。

---

## 4. 実装

### 4.1 `google_ai.py`

#### 追加（`generate()` の直前あたり）

> **実装上の必須手順**: 現行 `first_payload` にインラインで書かれている system 文・user 文・
> functionDeclarations を、下記の定数（`_FIRST_SYS` / `_FIRST_USER` / `_FETCH_DESC`）に**置き換えて**から
> `first_payload` を組み立てること。インラインのまま `_sig_fp` に同じ文字列を複製すると、
> 片方だけ直したときにフィンガープリントが検知できず、無効な署名を使い続ける。

```python
# hashlib / copy / threading / json / os / time は google_ai.py で import 済み（追加不要）

_SIG_LOCK = threading.Lock()
SIG_CACHE_FILE = os.path.join(DATA, "fetch_signature.json")
SIG_CACHE_VERSION = 1
SIG_MAX_BYTES = 65536            # 1 エントリの上限（超えたら disabled）
SIG_MAX_ENTRIES = 20             # モデル数の上限（超えたら古い順に削除）

# 失敗後の「休み」は時間ではなくターン数で数える（1 ターン約 30 秒なので分単位の待ちは無意味）
SIG_SKIP_TURNS = (1, 3)          # 失敗1回目 → 次の1ターン休む / 2回目 → 次の3ターン休む
SIG_GIVE_UP_AFTER = 3            # 3回失敗したら中止（このプロセスでは使わない。再起動で復活）

_SIG_MEM = None                  # 常駐キャッシュ（読み込みは初回の 1 回だけ）
_SIG_FAIL = {}                   # "provider|model" -> {"streak":n, "skip":k, "given_up":bool}（メモリのみ）
SIG_OK = {}                      # "provider|model" -> 成功回数（メモリのみ）

_FIRST_SYS = ("あなたは日本語ロールプレイの本文生成器です。"
              "今回の応答を書く前に、必ず fetch_prompt を呼び出してコンテキストを取得してください。"
              "取得するまで本文を書いてはいけません。")
_FIRST_USER = "fetch_prompt を呼び出してコンテキストを取得してください。"
_FETCH_DESC = "このチャンネルの設定・会話・今回のユーザー発言を取得する。引数はない。"

def _sig_fp(fetch_name):
    """第1段の内容が変わっていないことを確認するハッシュ"""
    raw = json.dumps({"sys": _FIRST_SYS, "user": _FIRST_USER,
                      "decl": {"name": fetch_name, "description": _FETCH_DESC},
                      "name": fetch_name}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _sig_load():
    """常駐キャッシュを返す（初回のみファイルを読む）。壊れていれば空で初期化"""
    global _SIG_MEM
    if _SIG_MEM is None:
        d = None
        try:
            with open(SIG_CACHE_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = None
        if not isinstance(d, dict) or d.get("version") != SIG_CACHE_VERSION                 or not isinstance(d.get("entries"), dict):
            d = {"version": SIG_CACHE_VERSION, "entries": {}}
        _SIG_MEM = d
    return _SIG_MEM

def _sig_save():
    """常駐キャッシュをファイルへ（原子的更新）。失敗しても生成は止めない"""
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = f"{SIG_CACHE_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sig_load(), f, ensure_ascii=False)
        os.replace(tmp, SIG_CACHE_FILE)
    except Exception as e:
        print(f"[sig] save failed: {e}")

def _sig_drop_parts(key):
    """署名だけ捨てる（次は第1段から取り直す）。失敗状態には触らない"""
    with _SIG_LOCK:
        e = _sig_load()["entries"].get(key)
        if isinstance(e, dict) and e.get("parts"):
            e["parts"] = []
            _sig_save()

def sig_cache_get(provider, model, fp):
    """有効な署名を返す。休み中・中止後・無効なら None（ファイル IO なし）"""
    key = f"{provider}|{model}"
    st = _SIG_FAIL.get(key)
    if isinstance(st, dict):
        if st.get("given_up"):
            return None
        if int(st.get("skip") or 0) > 0:
            st["skip"] = int(st["skip"]) - 1     # 1 ターン消費して休む
            return None
    with _SIG_LOCK:
        e = _sig_load()["entries"].get(key)
    if not isinstance(e, dict) or e.get("disabled") or e.get("fp") != fp:
        return None
    parts = e.get("parts")
    if not isinstance(parts, list) or not any(isinstance(p, dict) and p.get("thoughtSignature")
                                              for p in parts):
        return None
    # 共有状態の破壊を防ぐため複製して返す（payload を組み立てる側が将来 mutable に触っても
    # キャッシュ本体が汚れないようにする。copy は google_ai.py で import 済み）
    return {"parts": copy.deepcopy(parts), "role": e.get("role") or "model"}

def sig_cache_put(provider, model, model_content, fp):
    """取得した署名を保存する。

    失敗状態（_SIG_FAIL）には一切触らない。ここでカウントを戻すと
    「失敗 → フォールバック成功 → 保存で復活 → また失敗」を繰り返し、中止判定に到達しなくなる。
    """
    if not isinstance(model_content, dict) or not isinstance(model_content.get("parts"), list):
        return
    key = f"{provider}|{model}"
    if (_SIG_FAIL.get(key) or {}).get("given_up"):
        return                                    # 中止後は保存しない（無駄なファイル書き込みを避ける）
    blob = json.dumps(model_content, ensure_ascii=False)
    with _SIG_LOCK:
        d = _sig_load()
        if len(blob) > SIG_MAX_BYTES:
            # thinking パート等で肥大化した応答は保存しない（以後このキーは試さない）
            d["entries"][key] = {"fp": fp, "saved_ts": time.time(), "disabled": True,
                                 "parts": [], "role": "model"}
            _sig_save()
            return
        d["entries"][key] = dict(model_content, fp=fp, saved_ts=time.time(), disabled=False)
        if len(d["entries"]) > SIG_MAX_ENTRIES:
            order = sorted(d["entries"], key=lambda k: d["entries"][k].get("saved_ts") or 0)
            for k in order[:len(d["entries"]) - SIG_MAX_ENTRIES]:
                d["entries"].pop(k, None)
        _sig_save()

def sig_cache_mark(provider, model, ok):
    """署名の成否を記録する（メモリのみ・ファイルに書かない）"""
    key = f"{provider}|{model}"
    if ok:
        _SIG_FAIL.pop(key, None)                  # 復活
        SIG_OK[key] = SIG_OK.get(key, 0) + 1
        return
    st = _SIG_FAIL.setdefault(key, {"streak": 0, "skip": 0, "given_up": False})
    st["streak"] = int(st.get("streak") or 0) + 1
    if st["streak"] >= SIG_GIVE_UP_AFTER:
        st["given_up"] = True                     # 中止（再起動 or 設定 OFF→ON で復活）
        st["skip"] = 0
        # 署名は消さない: 設定を OFF→ON したときに「同じ署名でもう一度だけ試す」ため
        print(f"[sig] give up {key} (streak={st['streak']})")
        return
    st["skip"] = SIG_SKIP_TURNS[min(st["streak"] - 1, len(SIG_SKIP_TURNS) - 1)]
    if st["streak"] >= 2:
        _sig_drop_parts(key)                      # 2回目以降は署名を捨てる

def sig_cache_forget_fail_state(provider, model):
    """失敗状態だけ消す（署名は残す）。設定 OFF の間に呼ぶ＝ON に戻すと再挑戦する。

    署名を消さないので、失敗が一過性だった場合は次のターンで即ヒットに戻る（コスト 0）。
    """
    _SIG_FAIL.pop(f"{provider}|{model}", None)

def sig_cache_reset(provider=None, model=None):
    """手動クリア（中止状態も解除する。アプリ再起動と同じ効果）"""
    with _SIG_LOCK:
        d = _sig_load()
        if provider and model:
            d["entries"].pop(f"{provider}|{model}", None)
            _SIG_FAIL.pop(f"{provider}|{model}", None)
        else:
            d["entries"] = {}
            _SIG_FAIL.clear()
        SIG_OK.clear()
        _sig_save()

def sig_cache_stats():
    """管理・デバッグ用（ログや REPL から見る。画面には出さない）"""
    with _SIG_LOCK:
        d = _sig_load()
        return {k: {"ok": SIG_OK.get(k, 0),
                    "streak": int((_SIG_FAIL.get(k) or {}).get("streak") or 0),
                    "skip": int((_SIG_FAIL.get(k) or {}).get("skip") or 0),
                    "given_up": bool((_SIG_FAIL.get(k) or {}).get("given_up")),
                    "disabled": bool(v.get("disabled")),
                    "saved_ts": float(v.get("saved_ts") or 0)}
                for k, v in d["entries"].items()}
```

#### `generate()` のシグネチャ

```python
def generate(model, prompt, system="", *, safety_off=True, max_output_tokens=8192,
             fetch=True, images=None, timeout=180, fetch_name="fetch_prompt", provider="google",
             thinking_level="auto", fetch_cache=False):     # ← 追加
```

#### 2 段階の共通化（挙動は変えない純粋なリファクタ）

現行の `try:` ブロック全体（第1段＋第2段 **と `use_fetch=False` の直接生成**）を **`_generate_core(...)`** として切り出し、`generate()` から呼ぶ。

> **名前の注意**: `_generate_two_stage` ではなく `_generate_core` とする。
> `use_fetch=False`（choices / memory / `google_fetch=False` / `images` あり）の直接生成もこの関数が担うため、
> 「2 段階」という名前だと実装者が直接生成を別経路に追い出してしまう危険がある。

```python
def _generate_core(model, prompt, system, *, use_fetch, safety, safety_off, timeout, provider,
                   max_output_tokens, thinking_level, fetch_name, t0, images=None,
                   save_sig=False, fp=None):
    """現行の try: ブロック全体（use_fetch=True なら 2 段階、False なら直接生成）。
    戻り値は generate() と同型。振る舞いは現行と 1 ミリも変えない（純粋な切り出し）。
    第1段が成功して fetch を呼んだ直後に、save_sig なら sig_cache_put(...) を 1 行追加する。
    """
    ...

def _generate_second(model, prompt, system, model_content, *, safety, safety_off, timeout,
                     provider, max_output_tokens, thinking_level, fetch_name, t0):
    """第2段のみ（署名キャッシュヒット用）。

    `_cache_fail` を付けるのは次の 2 つだけ（それ以外は通常の結果として返す）:
      - 400 かつ `_is_tool_400(str(e))`   ← **provider でゲートしない**（google でも退避する）
      - 空応答（text=="" かつ block_reason 無し かつ finish_reason がブロック系でない）
    SAFETY / block_reason / 429 / 5xx は `_cache_fail` を付けない（429・5xx は例外を伝播）。
    `_cache_fail` は返す前に `generate()` が `pop` する（`meta` には入れない）。
    """
    ...
```

#### 戻り値・例外の契約（両方のヘルパーで厳守）

現行 `generate()` は **① 内側の `try:` で `GoogleAPIError` を dict に変換し、② 外側の `except` でも同じ形に落とす** という二段構えになっている。
切り出し時にこの契約を崩すと、429の retryable 判定が消えて再試行されなくなる。

| 項目 | `_generate_core` | `_generate_second`（キャッシュヒット） |
|---|---|---|
| 戻り値のキー | `text` / `meta` / `fetch_called` / `first_status` / `second_status` / `elapsed_ms` / `error` / `retryable` / `http_status`（現行と同一） | 同左 |
| `fetch_called` | 第1段で fetch を呼んだら `True` | **常に `True`**（fetch 機構で文脈を渡したため） |
| `first_status` | 第1段の HTTP ステータス | **`None`**（リクエストしていない） |
| `second_status` | 第2段の HTTP ステータス | 第2段の HTTP ステータス |
| 400 `_is_tool_400` | 現行どおり（`provider=="vertex"` なら直接生成へ） | **内側の `try` で捕まえて `_cache_fail="400"` を付けて返す**（下の `except` に落とさない） |
| 空応答 | `error="empty response …"`, `retryable=True` | 同左 ＋ `_cache_fail="empty"` |
| SAFETY / `block_reason` | `error="generation blocked: …"` / `"prompt blocked: …"`, `retryable=False` | 同左、**`_cache_fail` は付けない** |
| 429 / 5xx / その他 `GoogleAPIError` | `error=str(e)`, `retryable=e.retryable`, `http_status=e.status`, `code=e.code` | 同左（`google_ai.generate` の外側 `except` が処理する。**ここで `_cache_fail` を付けてはいけない**） |

> ⚠ **実装の罠 1**: `_generate_second` で `except GoogleAPIError as e:` を先に書くと、400 も dict に変換されてしまい
> キャッシュ退避が発動しない。**400 `_is_tool_400` の判定は `except` 節の先頭（再 raise より前）に置く**。
>
> ⚠ **実装の罠 2**: `_generate_second` が組み立てる payload は **現行の第2段と完全に同一**でなければならない。
> 特に `contents[0]`（固定の user 文 `_FIRST_USER`）を落とすと署名が無効になり、毎回 400 → 3 回で中止で
> 「効果ゼロ」になる（壊れはしないが気づきにくい）。実装時は現行コードの `second_payload` をそのまま使い、
> `contents[1]` にだけキャッシュの `model_content` を入れること。

#### `generate()` 本体の分岐（要点のみ）

```python
    use_fetch = bool(fetch) and not images
    fp = _sig_fp(fetch_name) if use_fetch else None
    cache_state = "off"
    if use_fetch and fetch_cache:
        cached = sig_cache_get(provider, model, fp)
        if cached:
            res = _generate_second(model, prompt, system, cached, safety=safety,
                                   safety_off=safety_off, timeout=timeout, provider=provider,
                                   max_output_tokens=max_output_tokens,
                                   thinking_level=thinking_level, fetch_name=fetch_name, t0=t0)
            fail = res.pop("_cache_fail", "")
            if not fail:
                sig_cache_mark(provider, model, ok=True)
                res["fetch_cache"] = "hit"
                return res
            # 署名起因の失敗だけ 2 段階へ退避（SAFETY / block / 429 はここに来ない）
            sig_cache_mark(provider, model, ok=False)
            if (time.time() - t0) < timeout:      # 残り予算ガード（最悪 2 倍を防ぐ）
                res2 = _generate_core(..., use_fetch=True, save_sig=True, fp=fp)
                res2["fetch_cache"] = "hit_fallback"
                return res2
            res["fetch_cache"] = "hit_fallback"   # 予算不足：そのまま返し、上位のリトライに任せる
            res["retryable"] = True
            return res
        cache_state = "miss"
    res = _generate_core(..., use_fetch=use_fetch, images=images,
                         save_sig=bool(use_fetch and fetch_cache and cache_state == "miss"), fp=fp)
    res["fetch_cache"] = cache_state
    return res
```

> `use_fetch=False` の場合も `_generate_core` が現行の直接生成経路をそのまま実行する
> （`cache_state` は `"off"`、`fetch_cache="off"`）。分岐を二分しないこと。

`_generate_core` 内の第1段成功箇所（現行 `fetch_called = bool(fc)` の直後）に保存を 1 行追加：

```python
            if save_sig:
                sig_cache_put(provider, model, model_content, fp)
```

#### 既存のフォールバックとの関係

現行の 3 つのフォールバックは**そのまま維持**する（削除・順序変更しない）。

1. 第1段が fetch を呼ばなかった（`provider=="vertex"` かつ非ブロック）→ 直接生成
2. 第2段が 400 `_is_tool_400`（`provider=="vertex"`）→ 直接生成
3. `_request_with_fallbacks` 内の safety/thinking 400 ラダー

キャッシュ系のフォールバック（3.4）は **1・2 より前**に評価する。

### 4.2 `server.py`

```python
# SETTINGS_DEFAULTS（576 行目付近、decensor_reply の次）
"google_fetch_cache": True,   # fetch 段の署名キャッシュ（既定ON・Vertex の往復削減。異常時は OFF）
```

```python
# run_google_task（8808 行目付近）
        res = _call_google_generate(
            model, prompt, system,
            safety_off=bool(st.get("google_safety_off", True)),
            max_output_tokens=int(st.get("google_max_output_tokens") or 8192),
            fetch=use_fetch, images=images, provider=provider,
            thinking_level=thinking_level,
            fetch_cache=bool(st.get("google_fetch_cache")),   # ← 追加
        )
```

```python
# _set_google_status 呼び出し（8900 行目付近）
        _set_google_status(sid, task_type, "waiting_import", model=model,
                           fetch=bool(res.get("fetch_called")), outbox=name,
                           elapsed_ms=res.get("elapsed_ms"),
                           fetch_cache=res.get("fetch_cache"),        # ← 追加
                           usage=(res.get("meta") or {}).get("usage") or {})
```

### 4.3 UI

- `public/index.html`: `f_google_fetch` の近くにチェックボックス
  `id="f_google_fetch_cache"` ラベル「fetch段をキャッシュして高速化（試験的・OFFで従来動作）」
- `public/app.js`: 読み込み（`f_decensor_reply` と同じ 972 行目付近の並び）と保存（995 行目付近）
- **状態表示・再試行ボタンは作らない**（中止で失うのは速度の利得だけで、ON/OFF で復活できるため）

---

## 5. 失敗モードと対策

| 失敗 | 検知 | 挙動 |
|---|---|---|
| 署名の期限切れ・無効化 | 第2段 400 `INVALID_ARGUMENT`（`thought_signature` / `function call` を含む） | `mark(ok=False)` → 同一試行内で 2 段階に退避（**provider 非依存**） |
| 空応答 | `text == ""` かつ `block_reason` 無し かつ 非ブロック系 `finish_reason` | 同上 |
| **SAFETY 切断 / `block_reason`** | `finish_reason in BLOCK_FINISH_REASONS` / `block_reason` | **退避しない**。通常のブロック結果として返す（コスト 2 倍化とフィルタ挙動の変化を避ける） |
| **429 / 5xx / ネットワーク例外** | `GoogleAPIError.status != 400` | **退避しない**。例外をそのまま伝播（再送すると負荷増） |
| 400 で `_is_tool_400` に該当しない | | 現行の safety/thinking ラダーに任せる |
| キャッシュファイル破損 | JSON パース失敗 | 空キャッシュとして扱う（例外を投げない） |
| 書き込み失敗（権限・容量） | `OSError` | ログのみ・生成は継続（キャッシュはベストエフォート） |
| 応答が 64KB 超（thought パート等） | `len(blob) > SIG_MAX_BYTES` | そのキーを `disabled=True` にして以後試さない（毎ターン試して挫けるのを防ぐ） |
| モデル変更 | キーが変わる | 自動的にミス→新規取得 |
| 第1段の文言変更 | `fp` 不一致 | 自動的にミス→新規取得 |
| 同一 `id` の重複利用 | 400（`id` を含む） | `_is_tool_400` に該当→退避→次の 1 ターン休む→再挑戦 |
| 残り予算不足 | `(now - t0) >= timeout` | 退避せず `retryable=True` で返す（最悪 2 倍を防ぐ） |

**原則: キャッシュ起因でユーザーにエラーを見せない。** ただし「予算不足で退避できなかった」場合は
上位のリトライに任せる（そのリトライでは休み状態によりキャッシュが使われない）。

**中止（3 回失敗）は自動復活しない。** 復活は「アプリ再起動」または `sig_cache_reset()`。
これは意図的な設計で、壊れたまま試し続けても利得がゼロで 1 秒ずつ損が積み上がるだけだから。

---

## 6. テスト計画

### 6.0 リファクタ前の特性テスト（**必須・最初にやる**）

> **実測確認済みの重大な穴**: 現行テストは `google_ai.generate` をモックするか `fetch=False` で呼ぶかで、
> **fetch 2 段階経路を検証しているテストが 1 つも存在しない**（`test_gemini_thinking.py` /
> `test_vertex_ai.py` は `google_ai.request` をモックしているが、いずれも `fetch=False`）。
> そのまま `_generate_core` に切り出すと、回帰を検出できない。

先に以下を `google_ai.request` のモックで追加し、**現行コードのまま green になることを確認**してから切り出す。

| テスト | 内容 |
|---|---|
| `test_two_stage_normal` | 第1段が fetch を呼ぶ → `contents` が 3 要素、`contents[1]` に `thoughtSignature`、`contents[2].functionResponse.response.context == prompt`、`fetch_called=True`、`first_status=second_status=200` |
| `test_two_stage_first_no_call_vertex_direct` | 第1段が fetch を呼ばず非ブロック（vertex）→ 直接生成に落ち、`fetch_called=False`、リクエストは 2 回 |
| `test_two_stage_second_tool_400_vertex_direct` | 第2段 400 `_is_tool_400`（vertex）→ 直接生成。`http_status` が最終結果 |
| `test_two_stage_first_blocked` | 第1段で `block_reason` → `error` が `prompt blocked: …` で `retryable=False` |
| `test_two_stage_second_blocked` | 第2段で SAFETY → `generation blocked: …`、`retryable=False` |
| `test_two_stage_empty_response` | 第2段が空 → `empty response` かつ `retryable=True` |

### 6.1 単体（`test/test_google_ai.py`）

> テストの前処理: `google_ai.DATA` と `google_ai.SIG_CACHE_FILE` の**両方**を tempfile に差し替える。
> `SIG_CACHE_FILE` は import 時に確定するため、`DATA` だけ差し替えても実データ領域に書き込んでしまう。
> また `google_ai._SIG_MEM = None` にリセットしないと、前のテストの常駐キャッシュが漏れる。

| テスト | 内容 |
|---|---|
| `test_sig_cache_roundtrip` | put → get で同一 `parts` が返る |
| `test_sig_cache_fp_mismatch` | `fp` が違えば `None` |
| `test_sig_cache_skip_turns` | 1 回目の失敗後、`get` が 1 ターンだけ `None` を返し、2 ターン目で署名を返す |
| `test_sig_cache_skip_grows` | 2 回目の失敗後は 3 ターン `None` が続く |
| `test_sig_cache_give_up_after_third` | 3 回目の失敗で `given_up` になり、その後は何ターン経っても `None` |
| `test_sig_cache_reset_revives` | `sig_cache_reset()` で中止が解け、また使える |
| `test_sig_cache_parts_dropped_on_second` | 1 回目の失敗では `parts` が残り、2 回目で消える（第1段から取り直す） |
| `test_sig_cache_put_does_not_touch_fail_state` | 退避後の `put` が `streak`/`skip`/`given_up` を変えない（バグ回帰テスト） |
| **`test_generate_cache_gives_up_and_recovers_on_restart`** | **3 回失敗→以降キャッシュを使わない→`_SIG_FAIL.clear()`（再起動相当）で復活する** |
| `test_sig_cache_disabled_on_oversize` | 64KB 超の応答で `disabled=True` になり、以後 `get` が `None` |
| `test_sig_cache_broken_file` | 壊れた JSON でも例外を出さず空で動く |
| `test_sig_cache_atomic_write` | `.tmp` が残らない |
| `test_generate_cache_hit_single_request` | `request` 呼び出しが **1 回**、payload の `contents` が 3 要素でキャッシュ署名を含む、`fetch_cache=="hit"`、`first_status is None` |
| `test_generate_cache_miss_two_requests` | 2 回呼ばれ、キャッシュに保存される |
| `test_generate_cache_400_falls_back` | 1 回目 400 → 2 段階で成功、`fetch_cache=="hit_fallback"`、署名が消える |
| **`test_generate_cache_400_google_provider`** | **`provider="google"` でも退避する（既存コードは vertex でゲートしているので、コピーすると壊れる）** |
| `test_generate_cache_empty_falls_back` | 空応答 → 2 段階で成功、ユーザーにエラーが出ない |
| **`test_generate_cache_safety_no_fallback`** | **SAFETY 切断は退避せず、リクエスト 1 回で終わる（`fetch_cache=="hit"`）** |
| **`test_generate_cache_429_not_retried`** | **429 は 2 段階に退避せず例外を伝播する（リクエスト 1 回）** |
| **`test_generate_cache_budget_guard`** | **第2段が timeout 近くまでかかった場合、2 段階に退避せず `retryable=True` で返す** |
| `test_generate_cache_flag_off_unchanged` | OFF なら 2 回呼ばれ、キャッシュファイルが作られない |
| `test_generate_cache_skipped_with_images` | `images` ありでは使われない |
| **`test_generate_cache_skip_when_fetch_false`** | **`fetch=False` では使われない（choices / memory 相当）** |

### 6.2 結合（`test/test_google_ai.py`）

| テスト | 内容 |
|---|---|
| `test_run_google_task_with_cache_hit` | フラグ ON・キャッシュありで本文が outbox に落ち、`google_status` に `fetch_cache=="hit"` |
| `test_flag_off_clears_fail_state` | フラグ OFF で呼ぶと失敗状態が消え、ON に戻すと再びヒットする（OFF→ON 復活の回帰テスト） |

### 6.3 本番 A/B（`tools/ab_filter_arms.py`）

- **未実装**: 現行ハーネスの `--arms` は `direct,placeholder,euphemism,toolcall,in_ph,in_euph,in_ph_tool,prod_direct,prod_in_ph`
  で、**`sig` アームは存在しない**（過去の署名試験は使い捨てスクリプト）。先に `--sig-cache` フラグとアームを追加する
- 追加アーム: `sig`（キャッシュヒット）／`sig_ph`（キャッシュ＋入力プレースホルダ）
- 比較: `prod_direct` vs `sig` vs `sig_ph`
- **同一時間帯**に同一セッションで n≥20、中央値・p90・最大・空応答率・SAFETY 率・`fetch_cache` の内訳を比較
- **`sig` は初回が必ずミス**なので、比較は 2 回目以降に限る（1 回目のミスを集計に入れると効果を過小評価する）
- **モデル別に `fetch_cache` の内訳を出す**（`hit` / `miss` / `hit_fallback` / `disabled`）。thinking モデルで
  thought パートにより `disabled` に落ちていると「効果ゼロ」に見えるため、全体集計だけで判断しない

### 6.4 回帰

`python -m pytest test -q` → **995 passed / 1 skipped を維持**

---

### 6.5 品質の手動確認（実装直後に 1 回だけ・10 ターン）

**自動テストでは検証できない領域。** キャッシュは「成功」を返したまま生成のクセだけが変わる可能性があり、
それはエラーにならないため 3 回中止の安全網でも捕まらない。**人間の目で 1 回だけ確かめる。**

| 手順 | やること | 見るもの |
|---|---|---|
| 1 | ON で 5 ターン回す | 書き出しのパターン / 口調 / 文章量 / ブロック・拒否の回数 |
| 2 | 設定を OFF にして 5 ターン回す | 同上 |
| 3 | 見比べる | **「同じキャラが同じように喋っているか」** |

判定:

| 結果 | 判断 |
|---|---|
| 差が分からない | **ON のまま**（速い方が得）。以後この確認は不要 |
| ON のとき**書き出しが毎回似ている**／文章が短い／手抜きに見える | **OFF**（生成のクセが変わった＝リスク②が実在） |
| ON のとき**ブロック・拒否が増えた** | **OFF**（安全判定に影響している） |
| ON のほうが速く、品質も同じ | 完全に成功。既定 ON を維持 |

> **現時点の証拠の限界**: これまでの A/B で測ったのは**速度と成功/失敗だけ**で、文章の質は一切比較していない。
> したがって「品質は変わらない」は**未検証**であり、この 10 ターン確認が唯一の根拠になる。

## 7. ロールアウト

1. **実装（既定 ON）** — 起動直後から有効。`fetch_cache` が `hit` になることをログで確認
2. **最初の 30 分で 2 点だけ確認**（§11 の 1・6）— thought パートの再送、SAFETY 切断率が増えていないこと
3. **A/B はスイッチの ON/OFF で切替**（n≥10 ずつ）— 受入基準を判定
4. 問題が出たらスイッチを OFF（即座に従来動作）。コード変更は不要

**なぜ既定 OFF をやめたか**: 既定 OFF は「誰も ON にしない → 一度も実行されない → 効果もデータもゼロ」で、
実装しなかったのと同じになる。壊れるものが無い（中止しても従来経路に戻るだけ）ので、既定 ON で問題ない。

**ロールバック**は `google_fetch_cache=False` の 1 設定で完了する（キャッシュファイルは残るが未使用）。

---

## 8. 受入基準

| # | 基準 | 判定方法 |
|---|---|---|
| 1 | **OFF 時、リクエスト数・戻り値・ログが現行と同一** | 単体テスト＋差分レビュー |
| 2 | ON でヒットした生成の API 往復が **2 → 1** | モック呼び出し回数 |
| 3 | フォールバック率 < 20% | `fetch_cache` 集計 |
| 4 | キャッシュ起因のユーザー可視エラー **0 件** | 結合テスト＋24h 運用 |
| 5 | 中央値 **−3 秒以上**、p90 **−20 秒以上**（n≥20・同一時間帯） | A/B |
| 6 | 成功率（本文が返る率）が OFF 比で**有意に低下しない** | A/B（Fisher） |
| 7 | `recitation` / `SAFETY` 切断率が OFF 比で増えない | A/B |
| 8 | キャッシュに本文・プロンプトが含まれない | ファイル目視＋テスト |
| 9 | **品質に差が無い**（書き出しのパターン・文章量・ブロック回数） | §6.5 の手動 10 ターン比較 |

> 基準 5 の「−3 秒」は、空いている時間帯の中央 13〜15 秒に対して意味のある最小幅として設定した。混雑時は効果が大きい（−11 秒）が、時間帯依存が大きいため中央値のみで判定しない。

---

## 9. リスクと注意

### 9.1 キャッシュ ON で実際に何が変わるのか

ON と OFF で Google に送る第2段のリクエストの違いは **`contents[1]` のバイト列だけ**。

```
contents[0] = 固定の「fetch_prompt を呼んでください」   ← 同じ
contents[1] = 整理券（thoughtSignature）              ← ★ここだけ違う（新しい / 保存してあった）
contents[2] = チャンネルの全文（functionResponse.context） ← 同じ
```

**キャラ設定・会話履歴・今回の発言は 1 文字も変わらない。**

### 9.2 整理券に入り得る情報／入り得ない情報

第1段のリクエストは**プロンプト非依存の定数**（§2）なので、整理券に入り得るのは
「fetch_prompt を呼ぼう」という推論だけ。

| 心配されがちなこと | 実際 |
|---|---|
| 前のチャンネルの内容が混ざる | **起こり得ない**（第1段にチャンネル情報が無い） |
| 前のターンの記憶が混ざる | **起こり得ない**（同上） |
| キャラ設定が漏れる | **起こり得ない**（同上） |

### 9.3 残るリスクは 2 つだけ

| リスク | 中身 | 検知できるか | 対応 |
|---|---|---|---|
| **① 弾かれる** | Google が「古い整理券はダメ」と判断する | **できる**（400 が返る） | 3 回で自動中止。現行経路に戻るだけ |
| **② 生成のクセが変わる** | 毎回まったく同じ整理券から書き始めるため、**書き出しや言い回しが似る**可能性 | **できない**（成功扱いになる） | **手動スイッチで OFF**＋§6.5 の 10 ターン確認 |

**②が「異常」の正体**であり、自動では気づけない。だからこそ手動スイッチを残す。

②の見立ては**低い**が、根拠は「推測」である:

- `thoughtSignature` は仕様上「functionCall を返すときに一緒に返す」**検証用トークン**として説明されている
- 仮に思考が暗号化されて入っていても、その思考は「fetch_prompt を呼ぼう」だけ
- 第2段の多様性はプロンプトとサンプリングで決まる部分が大きい

> **仕様が非公開なので「大丈夫」と言い切る根拠にはならない。** だから §6.5 で実機確認する。

### 9.4 スイッチの位置づけ（「設定」ではなく「保険」）

正常に動けば OFF にする理由は無い。**999 回は触らない。1 回だけ、なにか引っかかったときに使う。**

| 案 | 内容 | リスク |
|---|---|---|
| **A. 既定 ON ＋ スイッチ残す（採用）** | 保険あり。触らなければ存在を忘れてよい | 設定項目が 1 個増える |
| B. 既定 ON・スイッチ無し | 一番すっきり | ②が実在した場合、**コード修正を待つしかない** |
| C. 既定 OFF | — | 誰も ON にしないので**一度も実行されない**（実装しなかったのと同じ） |

スイッチの 3 つの役目:

| 役目 | いつ使うか |
|---|---|
| ① 逃げ道 | 「なんか出力が変」「ブロックが増えた」と**感じたときだけ** |
| ② 復活レバー | 中止（3 回失敗）のあと「もう一回試したい」とき（OFF→ON で失敗の記憶だけ消える。署名は残る） |
| ③ A/B | 効果や品質を測りたいとき（スイッチを 2 回押すだけ） |

### 9.5 リスク一覧

| リスク | 度合い | 対応 |
|---|---|---|
| `thoughtSignature` は**非公開仕様**。予告なく検証が厳格化する可能性 | 中 | 退避＋3回で中止。以降は現行と同一経路（再起動で再挑戦） |
| 空応答の観測が n=1 のみ（原因未特定） | 中 | 基準 3・4 で監視。増加すれば既定 OFF に戻す |
| 署名の有効期限が不明 | 低 | TTL を設けず、失敗で捨てる方式（期限を推測しない） |
| **エンドポイントスコープが不明**（`request()` 内の 404 → `global` フォールバックで、署名を発行した location と再生する location が変わり得る） | 中 | 未検証。失敗すれば 400 で退避するので実害は小さいが、必要ならキーに `effective_vertex_location(cfg)` を含める |
| **`thought: true` パートの再送が受理されるか未検証**（`thinkingBudget=128` のモデルで thought が返る場合、キャッシュに含まれる） | 中 | 実機確認項目（§11）。問題があればサイズ上限で `disabled` に落ちて自動退避する |
| **リファクタ自体のリスク**（fetch 2 段階に既存テストが無い） | 高 | §6.0 の特性テストを先に入れ、別コミットで切り出す |
| 効果の測定ノイズ（Vertex の混雑変動が 2 倍以上） | 高 | A/B は同一時間帯・n≥20。判断は中央値＋p90 の両方 |
| キャッシュが `data/` に残る（複数環境で共有した場合） | 低 | `.gitignore` に `/data/` があるのでコミットされない（確認済み）。手動クリア関数を用意 |
| `first_status=None` による既存コードの型前提崩れ | 低 | 参照箇所は `tools/ab_filter_arms.py:333` のみ（全 grep で確認済み）。`server.py` / `public/app.js` には参照なし |

---

## 10. 参考: 現行 2 段階の実測内訳

```
[第1段] 0.9〜37.6 秒   fetch_prompt 取得（出力10tok・thinking 0・純粋なキュー待ち）
[第2段] 残り          本文生成（ここが本来のコスト）
```

署名キャッシュは **第1段だけを消す**。第2段の生成内容・プロンプト・コンテキストの渡し方は変わらない。

> **未検証の前提**: 「再生した署名でもフィルタ特性（SAFETY 切断率）が変わらない」ことは**まだ証明していない**。
> 構造上ほぼ同一だが、受入基準 7 で必ず確認する。

---

## 11. 未検証の前提（既定 ON で運用しながら確かめる。外れたら OFF）

| # | 前提 | 確かめ方 | 外れた場合の影響 |
|---|---|---|---|
| 1 | 再生した署名が別セッション・別プロンプトで有効 | `--sig-cache` で n≥20（過去の 3/3 は n=3） | ヒット率が上がらない。3 回で中止（再起動で再挑戦） |
| 2 | 署名は `safetySettings` の有無に依存しない | `google_safety_off` を切り替えて両方でヒットを確認 | 設定変更後に失敗が続き、3 回で中止 |
| 3 | 署名はエンドポイント（location）に依存しない | 404 フォールバック発生前後でヒットを確認 | 一時的に退避が増えるだけ |
| 4 | `thought: true` パートを含む `model_content` の再送が受理される | `thinkingBudget=128` のモデルで 1 ターン目と2 ターン目を観測 | サイズ超過で `disabled` → そのモデルでのみ無効 |
| 5 | 同一 `functionCall.id` の繰り返し送信が拒否されない | 連続 20 ターン実行（400 `id` が出ないか） | 毎回退避するので速度効果ゼロ（機能は壊れない） |
| 6 | 再生署名で SAFETY 切断率が上がらない | A/B（受入基準 7） | OFF に戻す |
| 7 | **再生署名で生成のクセ（多様性・文章量）が変わらない** | **§6.5 の手動 10 ターン比較**（唯一の根拠） | OFF に戻す |

---

## 12. 実装順序（コミットを分ける）

| # | 作業 | 完了条件 |
|---|---|---|
| 1 | §6.0 の特性テストを追加（**現行コードのまま**） | 全テスト green。現行挙動が固定される |
| 2 | `_generate_core` / `_generate_second` へ切り出す（キャッシュなし） | 995+6 passed。`fetch_cache="off"` だけ返る |
| 3 | キャッシュ層（`sig_cache_*`）＋ `generate(fetch_cache=)` ＋単体テスト | §6.1 全 green |
| 4 | `server.py` のフラグ（OFF 時に失敗状態をクリア）＋UI のチェックボックス | OFF にすると従来と完全一致することを確認 |
| 5 | ハーネスに `--sig-cache` と `sig`/`sig_ph` アームを追加 | A/B が回る |
| 6 | 既定 ON で運用し、スイッチの ON/OFF で A/B（n≥10） | 受入基準 1〜8 |

**ロールバックは各段階で可能**: 1〜2 は挙動不変、3 以降はフラグ OFF で従来経路。
