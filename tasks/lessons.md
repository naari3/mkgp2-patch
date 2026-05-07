# Lessons (mkgp2-patch)

プロジェクト開発で得た教訓を記録。同じ失敗を繰り返さないよう、次回セッション開始時にレビューする。

---

## 2026-04-24: custom_assets の sibling binding は推測で追加するな

**失敗**: ページ3カーソル移動時の 1-frame vanilla フラッシュを消そうとして、ログ (`MKGP2: cup17 GroupKey queries 0x...`) で cup=17 中にクエリされてる未バインドID (0x1780, 0x178B, 0x1788, 0x1751, 0x176B, ...) を片っ端から `0x9000`/`0x9001`/`0x9002` にバインドした。

**結果**:
- 謎の位置にカップが重複表示
- 座標ズレ
- FPS 激落ち (PreloadResource が 129 IDs 分の DVD open を走らせる)

**根本原因**: ログに現れる ID はあくまで「`g_cupId==17` 中に resource getter が呼ばれた」というだけで、ページ3カップタイルと無関係な他UI要素 (help text, subtitle, BG atlas, subtitle banner, etc.) も含む。それらを cup icon (`0x9000`) に route すると、本来 148×64 サイズの banner slot に 128×128 RGBA32 を読ませて座標破綻 + 同じテクスチャが複数箇所に重なる。

**次回からの判断基準**:
- 「特定 UI 要素を差し替える」ためのバインドは、その要素の vanilla 描画パスを decompile / Ghidra で追跡し、実際にその要素の描画で使われる ID のみを対象にする。ログの ID 一覧は探索のヒント止まり。
- 未知の resource ID を bulk で bind するのは絶対 NG。せめて 1 個ずつバインド→ビルド→目視で「これを差し替えると何が変わるか」を確認する。
- Chain walk の next_id リダイレクトは alpha mask 等の内部フォーマットが違うので `RGBA32` に差し替えると blend が破綻する。chain を終端 (`-1`) で切るのが安全。

**MVP 判断**:
- ページ3の8タイルが test_cup を表示する目標は達成 (25 bindings)。
- カーソル移動時の 1-frame flash は未解決だが、機能的にはカップ選択・ラウンド遷移・レース全て動く。cosmetic 優先度は低。必要なら将来 Ghidra でタイル描画パスを decompile して正確に対応する。

---

## 2026-04-26: 'main game loop' を MainGameLoop と決めつけて hook した

**失敗**: debug_overlay 機能で、毎フレームのデバッグ HUD を出すために `MainGameLoop @ 0x8002dd58` 内の `bl FUN_80121120` (0x8002e4ec) を kmCall した。起動シーケンスの最初の数フレーム (~50ms) だけ HUD が出て、PCB ID チェック画面以降ずっと出ない現象になった。

**結果**:
- HUD は boot 直後の 3 frame だけ表示
- PCB ID check 画面 / title / cup select いずれでも一切出ない
- 一方で custom_assets の getter hook は cup select でも fire し続けてた

**根本原因**: `MainGameLoop` という名前と中の `while (iVar6 == 2) { ... FUN_80121120() ... }` の構造から「これが per-frame の game loop」と決めつけた。実際にはこれは **card task 待機 loop** (`iVar6 == 2` = card task in progress)。card 読込が完了すると loop を抜けて、PCB check loop → notice loop と進んで MainGameLoop は return する。

実際の per-frame ループは `FUN_800ac894` が `MainGameLoop` 後に呼ぶ **`FUN_8002c5e8`** の `do { ... } while(true)`。中身に `DebugOverlay_Dispatch(param_1)` (vanilla 側のデバッグオーバーレイ呼び出し) や `bl 0x8002cd9c` (scene draw) が入っているので「これが本物」と判断できた。BL site は 0x8002c678 (main path) と 0x8002caf0 (cleanup)。

**次回からの判断基準**:
- 関数名や Ghidra の命名 (`MainGameLoop` 等) を信用しすぎない。caller chain (`get_function_callers`) を辿って、wrapper の構造を確認する。
- "per-frame loop" の判定は **vanilla がそこで debug overlay / scene draw / フレームカウンタ等を呼んでいるか** で確認。FUN_8002c5e8 は `DebugOverlay_Dispatch` を呼んでいたので確実。
- 候補が見えたら **動作で検証**: 診断 log を仕込んで、cup select 等の active scene でも fire するか確認 (今回これで気付いた)。
- 「複数候補がある hook 先」(今回 FUN_80121120 の callers が 7 個)のうち、どれが "normal gameplay frame" かは caller の構造を Ghidra で読んで決める。

---

## 2026-04-26: custom resource ID を 0x9xxx に置いた

**失敗**: round-select の direct-insert 用に custom resource ID として `0x9000`/`0x9001`/`0x9002` を割り当てた。binding 経由 (vanilla ID → custom ID の table 引き) では問題なく動いていたが、direct-insert 方式に切り替えたら sprite が透明になる症状が出た。

**結果**: 該当 cursor のタイル / banner が描画されない。slot registry には resourceId が登録済みなのに lookup miss する。

**根本原因**: vanilla の `Sprite_SetAnimParam(sprite, paramId, short value)` (@0x801a0374) は値を **signed short (16-bit)** で受ける。direct-insert で resource id を sprite anim param に値として渡すと paramTable に short として格納され、consumer 側が `(short)` で読み戻したとき sign-extend する。`0x9006` が `0xFFFF9006` に化けて、slot registry の `0x00009006` と比較ミス。binding 方式は slot key が vanilla ID (< 0x8000) のままなので顕在化していなかった。

**次回からの判断基準**:
- custom resource ID は **必ず < 0x8000 (sign-safe)** の範囲から取る。MKGP2 では `CUSTOM_ID_BASE = 0x4000` を採用。vanilla 未使用 (`0x2B04..0x7FFF`) かつ signed positive を満たすこと。
- `0x9xxx`/`0xAxxx` 系を使うのは安易すぎる。binding 経由なら動いてしまうので debug 困難。
- 既存範囲を変えるときは: `custom_assets.h`, `gen_custom_assets_header.py` の base 定数、`custom_assets.cpp` の `ResourceSlot_Load_BranchHook` 範囲、`TryPreload` group_key 範囲、`round_select.cpp` の preload skip 閾値を全部追従する。

---

## 2026-04-27: 実機検証の渡し方で log が混線 / .map アドレスが伝わらない

**失敗**: ビルド後にユーザーへ「実機で起動して挙動確認お願い」と渡したターンで、`dolphin.log` を truncate せずに渡した。さらに、実機で書き換えてほしい debug flag (`g_dbgOverlayMode` 等) のアドレスを報告に含めなかった。ユーザー側で過去セッションの "first fire: 0x..." と今回ログが混在し、誤読を引き起こした。

**結果**:
- 過去セッションのログ行を今回ターン由来と誤認、間違った仮説を立てた
- patch-bin 内シンボル (Kamek 動的配置) はゲームの symbol DB / Ghidra どちらにも出ないので、ユーザーが mkgp2-view / Cheat / RAM Watch で値書き換えしたくてもアドレスが分からず手が止まる

**根本原因**: パッチ領域シンボルの authoritative source は `mkgp2_patch.map` (Kamek 出力、リポジトリ root) のみ。これを毎回ユーザーに開かせるのは無駄。ログは Dolphin が追記モードで吐くので前回値が残る。

**次回からの判断基準**: 「実機検証お願い」と渡すターンで以下 2 点を毎回セットで実行:
1. `: > "/c/Users/naari/Documents/Dolphin Emulator/Logs/dolphin.log"` で truncate (backup 不要、明示済)
2. `mkgp2_patch.map` を grep して、検証中ユーザーが触る/読む可能性のある変数 (volatile flag, static config, counter) のアドレス `0x80xxxxxx` を報告本文に併記
- 例: `g_dbgOverlayMode = 0x806F5084 (mode 2/3 で sprite id 表示)`
- function symbol も map に入っているので breakpoint 用にも使える

---

## 2026-04-27: vanilla buffer を借用して u16/u32 を取り違えた

**失敗**: 自前 `unsigned short[4]` を vanilla の bgmIdList state slot に inject。値として `{21, 21, 22, 22}` を書き込んで「vanilla layout 互換」と謳った。レース突入時に BGM 無音化、原因は数日掴めず。

**結果**: custom cup race 突入で BGM silence。bound check 側で弾かれて再生開始されない。

**根本原因**: 消費側の `FUN_8016b32c` は `lwz` (32-bit load) で読んでいた。vanilla の bgmIdList は実は `u32 [long, short]` の 2 entry レイアウト。`u16[4]` で書くと最初の 2 word が `0x00150015 / 0x00160016` の巨大値に化け、bound check (おそらく BGM table size 比較) で reject。

**次回からの判断基準**:
- vanilla の state slot に自前 buffer を inject する前に、**消費関数の load 命令幅 (lwz/lhz/lbz) を Ghidra で disassemble して確認**してから buffer の型を決める。
- 名前や周辺コメントから型を推測しない。`unsigned short bgmList[4]` のような書き方をしたいなら、その根拠 (どの関数の何行目で lhz 確認した) を同じコメント内に残す。
- vanilla per-cup table が rodata に居るなら `read_u32` / `read_bytes` で 1 entry dump、値の範囲 (例: 0x11 = 17) と消費側 bound check で型を確定させる。

---

## 2026-04-27: static table を「dead」と誤判定して inject 機会を逃した

**失敗**: `kCupNameBannerTable` (DAT_8049ade4) を実機メモリ dump で確認、cursor=0 が全 0、cursor=7 で `short[1]=0x0009` 程度の garbage しか見えず、Ghidra xref が WRITE 0 件 / READ のみだったので「draw 経路 dead」と即断、cup-name banner inject (C-2a, commit 676afc3) も「効かないので close したい」と user に誤報告した。

**結果**: 実機で実際に inject してみたら、page 2 cursor=7 で正しく描画され `dolphin.log` に `0x4001 GroupKey queries (#29)` が記録されていた。close を撤回。

**根本原因**: vanilla の page 0/1 では別経路 (cupNameSprite anim 0x18f) で cup-name が描画されるため、テーブル経由の `FUN_801a1174((short)(&DAT_8049ade4)[cursor*6], 4, ...)` 呼び出しは毎フレーム実行されているが garbage 値で resource lookup miss して no-op になっていた。我々が追加した page 2 では sprite 経路が "ura sprite reuse" のまま再 init されないので、テーブル経路が事実上 page 2 専用 banner draw として alive になる。

**次回からの判断基準**: vanilla static table を「dead」と判定するときは以下を順に確認:
1. Ghidra で **READ 経路の draw コード** を decompile し、毎フレーム実行されるかを見る (条件分岐の中なら条件確認)
2. 実機で **inject 後** の dolphin.log を取り、custom id (`0x4xxx`) が getter で query されているか確認
3. テーブル初期値が garbage なのは「vanilla で読まれないから初期化不要」と解釈、**inject scratch としては理想的**
4. memory dump の "値が変" だけでは dead 根拠にならない。draw 経路の `if` 条件 / mask / 比較を asm レベルで読み、本当に hit しないことを示せた時のみ dead 断定

---

## 2026-04-27: Sprite_SetAnimParam を単発で呼んで paramTable を更新したつもりになった

**失敗**: cup-tile sprite の resource id を切り替えるために、`Sprite_SetAnimParam(sprite, paramId, customId)` を単発で呼んだ。1 回目は値が反映されたように見えたが、次回同じ paramId で呼んでも 0 を返してきて挙動が再現しない。

**結果**: paramTable が破壊済みで、以降の SetAnimParam 呼び出しが全て no-op。custom ID への切り替えが永続化しない。

**根本原因**: `Sprite_SetAnimParam` (@0x801a0374) は名前と異なり paramTable を **破壊的に書き換える scan 関数**。内部実装は (1) paramTable を頭から走査して `paramId` と一致する slot を見つけ、(2) その同じ slot に `value` を書き込む (= paramId 自体を value で上書き)。次回同じ paramId で呼んでも slot が破壊済みなのでマッチせず 0 返り。

**次回からの判断基準**:
- `Sprite_SetAnimParam` の単発呼び出しで paramTable を「永続的に」更新できると思ってはいけない。
- vanilla code は `Sprite_SetupAnim(sprite, animId, 0, 0)` (= anim definition から paramTable を再構築) → 直後に `Sprite_SetAnimParam` で個別 param を上書き、というペアで使う (FUN_801c6c0c 等)。
- 自前で paramTable を更新したいときは、まず `Sprite_SetupAnim` で再構築するか、vanilla の state 経路を再実行させる。
- `SpriteHandleSlot.resourceId` (offset 0x10) は paramTable とは別 field。SetAnimParam で paramTable を触っても resourceId は更新されない。

---

## 2026-04-27: cup-tile sprite を直接書いて refresh されない問題に詰まった

**失敗**: cup-select page 2 で per-cup table (DAT_8049ad58 等) を direct-insert で書き換えた後、未 hover の cursor は古い vanilla ID のまま描画され続けた。「テーブル書いたんだから次フレームで反映されるはず」と思い込んで原因究明に時間をかけた。

**結果**: hover 中の cursor だけ custom asset、他 cursor は vanilla のままの混在表示。

**根本原因**: vanilla の `FUN_801c6c0c` (CupTile_StateChange @ 0x801c6c0c) は cursor の **state 変化時にしか sprite を再 setup しない**。テーブル書き換えだけでは paramTable に反映されず、state 変化が起こるまで古い ID で描画され続ける。

**次回からの判断基準**:
- 「table を書き換えたら次フレームで描画に反映される」とは限らない。vanilla 側がテーブル値を **どのタイミングで sprite paramTable に転写しているか** を Ghidra で確認する。
- 解決策: Apply 直後に各 cursor の現在 state (`*(int*)(scene + 0x20 + cursor*0x10)`) を読み出し、同じ state を再渡しして `CupTile_StateChange(scene, cursor, state)` を強制呼び出しする state-replay を行う。state>=2 経路は内部で `Sprite_SetupAnim` → `Sprite_SetAnimParam` を再実行するので paramTable が新 ID で再構築される (state 1 / 0 は skip)。
- scene 内 cup-tile entry layout: `piVar5 = scene + cursor*0x10 + 0x20`、`piVar5[0] = state (1..6)`、`piVar5[3] = sprite handle`。

---

## 2026-05-07: HSD texture を Blender に持ち込んで色チャンネルが入れ替わった

**失敗**: HSDLib の `GetDecodedImageData()` 結果をそのまま ImageSharp `Rgba32` に渡して PNG 出力した。Blender で見るとテクスチャの赤と青が入れ替わっている。

**結果**: コース mesh の色味が全体的におかしい。MR_highway 系の青系材料が赤、赤系が青で表示される。

**根本原因**: HSDLib の `GetDecodedImageData()` は **CMP / RGBA8 だけ BGRA**、他 format は RGBA を返す。format 別に channel order が違うが doc には書かれていない。

**次回からの判断基準**:
- 外部ライブラリの「decoded image data」系 API は format 別に channel order が違うことがある。盲目的に `Rgba32` 等に流し込まない。
- csx 側で format チェック (CMP / RGBA8) して R↔B swap を入れる。
- decode 結果が怪しいときはまず単色テクスチャ (例: 赤の plain PNG) を vanilla 経由で表示させ、Blender 出力と比較する。
- 関連トラップ (HSD pipeline で踏んだ他の罠 4 件: unlit Emission shader 必須 / `useVertexColor` の alpha pre-multiply / `TObj.Blending` を読まないと BLEND が白被り / alias root の struct dedup) は `mkgp2docs/hsd_to_blender_visual_pipeline.md` を参照。

