# direct-insert 統一: cup-select / round-select 全アセット移行

## 目標

カップセレクト / ラウンドセレクト で表示される全カスタムアセットを **直接挿入方式** (sprite に書き込まれる resourceId 自体を 0x4000+ に変更) へ統一する。**g_cupId override (現 round_select の Yoshi swap, cup_page3 の g_cupId=17 強制) を廃止**する。

完了状態 (改定): debug_overlay で表示される resourceId が **C-2d を除き** 0x4000+ で統一、cup_page3 の `g_cupId=17` per-frame 強制を廃止、`kBindings[]` は C-2d won't-fix の連鎖で **banner 0x175E 用 1 件のみ残存**。`g_customCupScope` も同じ理由で gate として残存。

---

## Phase A: 偵察 — 完了

### 判明した構造

#### cup-id-indexed テーブルは実質 2 つ
- `DAT_8049af8c` [9 short] cup_id → sub_index
- `DAT_8039b2f4 + 0xe` 系 (3 short × 9 cup, trophy 用)
- 残りは全て **sub_index-indexed**

#### sub_index-indexed テーブル群 (連続 data block 0x8049ad58..0x8049afb0)
- `DAT_8049afa0` [8 short] cup-name (0x16ED..0x1708)
- `DAT_8049aea0` [8 × 16byte] round-thumb (square 0x1A66系 + road 0x19E0系)
- `DAT_8039b308` [8 × 6byte] cup-tile preload (0x1A24, 0x1A00, ...)
- `DAT_8049af78` [9 short] cup banner anim slot (0x1970系)
- 他 anim id テーブル `DAT_8049af30/3c/48/54/60/6c`

#### cup-select cursor-indexed テーブル
- `DAT_8049ad58` [9 entry × 12byte] icon/ribbon/x/y → cup-tile sprite (Sprite_SetAnimParam 経由)
- `DAT_8039b218` [8 short] trophy → wreathSprite paramTable[0x1ea3] (FUN_801c64dc 内)
- `DAT_8049ade4` [cursor*6] cup-name banner top 0x1758系 → **immediate-draw** (clFlowCup_Draw@801c75ac)
- `DAT_8049ade6` [cursor*6] cup-name banner bot 0x1729系 → **immediate-draw** (clFlowCup_Draw@801c75fc)

#### 想定外
- **0x1729 系は sprite ではなく `clFlowCup_Draw` の immediate-mode draw API** (`FUN_801a1174` で毎フレーム直接描画)。custom 化には Draw 内 hook 必要
- **0x175E (banner CUPsel01_a)** は anim asset 内蔵で sprite 経由でない → 当面 vanilla 流用
- テーブル群は連続 block で隣接破壊リスクあり、直接拡張不可

---

## Phase B: 設計 — 確定

### 採用戦略: ハイブリッド (テーブル inject + 部分 hook)

#### cup-select 系 (cursor-indexed テーブル inject)
- page 2 entry で 8 cursor slot 全部に custom 0x4000+ ID inject、page exit で restore
- 対象: `DAT_8049ad58`, `DAT_8039b218`, `DAT_8049ade4`, `DAT_8049ade6`
- 0x174c (cup label 固定値) は clFlowCup_Draw@801c7654 hook で page 2 のとき custom 返す
- 結果: cup_page3 の **g_cupId=17 強制廃止**

#### round-select 系 (sub_index getter hook + sub_index-indexed テーブル inject)
- `DAT_8049af8c[g_cupId]` 参照箇所 ~19 を kmCall でラップ、g_cupId≥17 で alias sub_index (0=Yoshi 借用) を返す
- そのまま vanilla code が sub_index=0 用テーブル (DAT_8049afa0[0], DAT_8049aea0[0..15], etc.) を参照
- それらの sub_index=0 entry に round-select PreInit で custom ID inject、PreDtor で restore
- 結果: round_select の **g_cupId swap 廃止**

#### custom_assets の整理
- `kBindings[]` 全廃
- `g_customCupScope` 削除

#### 0x175E
- anim asset 内蔵の vanilla 描画 → 当面 custom 化せず vanilla 流用

---

## Phase C: 実装

### C-1: cup-select cursor-indexed テーブル inject (icon/ribbon) — **完了**
- [x] custom_assets.h に CupSelectInject struct 追加
- [x] gen_custom_assets_header.py に inject array emitter 追加
- [x] cup_page3.cpp に Apply/Restore 関数 + CupForward/Backward 呼び出し
- [x] clFlowCup_Dtor entry に safety net hook (prologue 4 inst replay → 0x801c8904)
- [x] ビルド成功 (79 hooks)
- [x] Dolphin 動作確認: page 1↔2 で inject/restore 3 回往復 OK、debug_overlay で 0x4000/0x4004 表示確認、vanilla cup 壊れず

### C-2a: cup-name banner bot direct-insert (DAT_8049ade6, 0x1729系) — **完了**
- [x] CupSelectInject に nameBotId 追加 (custom_assets.h)
- [x] gen で yaml `name` から nameBotId を拾う
- [x] cup_page3 の Apply/Restore に DAT_8049ade6 inject 追加
- [x] commit 676afc3 + 実機確認 (test_cup_name.png 表示、dolphin.log で 0x4001 query 発火)
- 注: vanilla の DAT_8049ade4 テーブル初期値は garbage に見えたが、page 0/1 では sprite 経路 (anim 0x18f) で描画され、page 2 のみ immediate-draw が active になる scratch 設計

### C-2b: trophy direct-insert (DAT_8039b218, 0x1EA2系) — **完了**
- 採用: (B) FUN_801c64dc 末尾の trophy 書き込みブロックを wholesale hook 置換
- kmBranch(0x801c6680, Trophy_RewriteHook) + kmPatchExitPoint 0x801c6730
- page 2 + custom cup なら CupSelectInject.trophyId を glyphRight に直書き、
  vanilla の +8/+16 (locked/silver/gold) 罠を回避
- cup-tile sprite refresh: FUN_801c6c0c (= CupTile_StateChange) を Apply 末尾で
  各 cursor の現在 state で再呼び出し → Sprite_SetupAnim 経由で paramTable
  が新 (custom) ID で再構築される
- 全 8 cursor で 0x4000+ 表示確認済み

### C-2c: cup-name banner top direct-insert (DAT_8049ade4, 0x1758系) — **drop (現状不要)**
- 0x1758系 (151×29) は banner block の左隣スペース。test_cup_name.png は nameBot (256×46) 単独で要件を満たすため、現状 customize したい cup yaml が無い
- 機構自体は C-2a と完全対称、必要になった時点で 10 行ほどで再追加可能
- 命名 (`top` 不適切問題) は再追加時に位置/役割を見て決める

### C-2d: cup-select banner (0x175E, anim asset 内蔵) — **closed (won't fix)**
- 0x175E は code 上 immediate 参照が `clFlowCup_Init` の PreloadResource 1 箇所のみ
  (Find_BannerRange.java で全コード検索、2026-04-27)
- 描画は anim asset (disk binary) 内に 0x175E が埋め込まれている形で、ロード後
  runtime が anim data の image-id list を読んで GetResourceEntry を呼ぶ経路
- vanilla の CUP_ASSET_SLOTS で banner は `cup_indexed=False` 単一 entry =
  **vanilla 自体が 0x175E 1 個を 8 cups で共有**、cupNameSprite/cupSubTitleSprite
  を per-cup で重ねて差別化している設計
- 単純に anim file を replace すると全 vanilla cup の banner も test_cup 化け
- per-cup 独自 anim file を作るには anim binary format のリバースが必要 = 数日仕事
- 費用対効果: 得られるのは「最後の 1 件 binding 削除」のみ。現状の scope-aware
  binding (kBindings=1, g_customCupScope=17 のとき発火) が **共有資源設計に対する
  最適な実装**。closed する。

### C-3: cup-select / round-select の kBindings 段階削除 — **部分完了**
- [x] gen で `emit_binding` flag 追加、direct-insert で cover 済の slot は binding 出さない
- [x] kBindings 34→1 (banner 0x175E のみ残、C-2d 未解決のため)
- [x] 実機検証: cup-select page 2 + round-select で test_cup の全 asset 正常表示 (commit 5714226)
- [ ] 最終削除 (= banner 1 件) は C-2d 解決後

### C-4: round-select sub_index=0 entry inject (cup-name 0x16ED 等) — **必要分完了**
- [x] DAT_8049afa0[alias_sub_index] (cup-name strip) の inject
  - CupSelectInject に nameRoundSelectId 追加、PreInit/PreDtor で inject/restore
  - 動作確認済み (0x4005 表示)
- [x] その他 sub_index-indexed asset の必要性確認 — 実機 debug_overlay 視認で「漏れている vanilla ID なし」(2026-04-27)
  - DAT_8039b308 / DAT_8049af78 / DAT_8049af30 系は round-select scene 中に visible 描画されていない (= direct-insert 不要)
  - 将来的に新規 cup を増やして見える要素が変わったら再偵察

### C-5: round-select の binding 削除 — **C-3 と同時に完了**
- [x] gen から round-select 系 binding を除外 (`name_roundselect` slot に `emit_binding=False`)
- [x] 動作確認 (commit 5714226 と同じ実機検証で完了)

### C-6: 後始末 — **部分完了 (残りは C-2d 連鎖で永続保留)**
- [x] cup_page3.cpp から `*(u32*)0x806cf108u = 17u;` per-frame 強制を削除 (commit 729390e: "Retire per-frame g_cupId=17 pin in CupForceGates")
- [x] ApplyBinding 診断 log を削除 (kBindings=1 entry になった時点で過剰、commit 730fe21)
- [x] ビルド + 動作確認 (commit 5714226 / 729390e の実機検証で test_cup/Yoshi 両方 PASS)
- [永続保留] `kBindings[]` 全廃 — C-2d won't-fix で banner 0x175E 用 1 件残存。
  C-2d を将来再開する際 (= viable な 3 経路のいずれかを実装) に同時に削除可能
- [永続保留] `g_customCupScope` 削除 — 上記 1 件が ApplyBinding を呼ぶ getter で
  cup を判定するために使う。kBindings 全廃と表裏一体

### C-7: 実機 E2E 検証 — **完了 (commit 5714226 / 729390e / 0a8a39d で PASS)**
- [x] cup-select page 2 で test_cup の icon/name/trophy/banner/ribbon が正常表示
- [x] round-select で cup name + 4 thumb が正常表示、レース突入 → 完走で問題なし
- [x] vanilla cup (Yoshi 等) が壊れていない (alias 機構の leak 防御 = `RoundIsUnlocked` wrapper)
- [x] debug_overlay (mode 2/3) で test_cup の全 sprite が 0x4000+ ID で表示
       (banner 0x175E のみ vanilla 流用 = C-2d 由来)
- [x] BGM 無音化 fix (commit 0a8a39d: "Fix BGM silence on custom cup race entry" — bgmIdList layout 修正)
- [ ] tasks/lessons.md に学びを追記 (= 直近 1 ヶ月の発見を整理、別タスク)

---

# Blender / hsdraw 周辺の残作業

`m1m3-full-independent-hsd-export` の merge と skill 整理 (mkgp2-new-course / mkgp2-edit-vanilla-course 両方完成、commit `500333c` まで) を経て確認した、未着手の項目を分野別に整理。

セッション境界: **2026-05-09 時点でクローズした作業**は本セクションに含めない (= test_addon の csx/scene.json 撤去、addon README 整理、edit-vanilla skill 新規追加、新 mesh 境界検証、軸変換 + bake helper 整理、Collection 構造実名化 + 最低構成 + 動作確認 + rounds 順序、ローカルブランチ削除、m1m3 todo の `tasks/done/` 移動 — すべて main に commit 済み)。

## A. hsdraw 上流 (別 repo・別 session 案件)

リポジトリ: `~/src/github.com/naari3/hsdraw/` (vendored: `tools/blender/blender_addon_mkgp2_course/vendor/<platform>/hsdraw/hsdraw.pyd`)

- [x] **A-1: 配列 API refactor (per-vertex → numpy bulk) — 完了 (2026-05-11)**
  - hsdraw 上流で `MeshBuilder.from_arrays(positions=, triangles=, normals=, colors=, uvs=)` 実装、両経路 (promote + bundle) 置換済 (commit `f330543`)
  - byte-equivalent + 1.34〜4.8x speedup を確認
  - 詳細: memory `project_hsdraw_array_api_refactor.md` / `project_hsdraw_2026_05_11_refactor_pass.md`
- [ ] **A-2: API consistency (JObj.dobj() getter, DObj.mobj 戻り値統一)**
  - `JObj` には `.child` / `.next` getter があるが `.dobj()` getter が無い (= mkgp2-patch 側が `as_struct().references()` 経由で offset 0x10 を手で引いている)
  - `DObj.mobj` は HsdStruct を raw で返すのに `DObj.next` は DObj wrapper を返す → 呼び側で `MObj.from_struct(s)` する必要があり inconsistent
  - 目標: getter を 1 セット (`jobj.dobj`, `dobj.mobj` → wrapper を返す) で統一
  - 影響: addon の type-check 分岐 (`if isinstance(x, hsdraw.HsdStruct): ... else: ...`) が消せる
- [ ] **A-3: Linux/macOS 用 wheel build**
  - 現状: `vendor/windows_x86_64/hsdraw/hsdraw.pyd` のみ shipping
  - 上流 `hsdraw` 本体は abi3-py37 で multi-platform 対応済 → maturin で各 platform build → vendor に配置すれば addon import が即通る
  - 優先度低 (= 開発者が Windows 中心)、要望が出たら対応

## B. mkgp2-patch リポジトリローカル

- [ ] **B-1: `_bake_vis_textures.py` を addon README に明記**
  - 現状: skill (`mkgp2-new-course`) では「per-mesh pattern が必要なときだけ使う」と書いたが、addon の `README.md` には言及なし
  - 目標: README の「使い方 > vis: 経路」に「BSDF Base Color の単色なら 4x4 fallback で済む / pattern が要るなら `_bake_vis_textures.py` を `Sidebar > MKGP2 > Bake vis: textures` から呼ぶ」を追加
  - operator id / button 配置を実コード (`__init__.py`) で確認してから書く
- [ ] **B-2: `MKGP2_OT_NewCourse` の docstring 整備**
  - 現状: docstring に「vis: と mkgp2: のどちらの collection を生成するか」が明文化されていない
  - 目標: `docstring` に「vis:<name> を生成 (新規コース合成用)、mkgp2:<dat> bundle は別 operator (`MKGP2_OT_ImportHSD`)」を追記。N panel の tooltip も同様
- [x] **B-3: 新 material 受け入れ時の挙動修正 — 完了 (2026-05-09)**
  - 採用案 (c) BSDF から現地構築 (commit `e813fec` + `7156122` + `13f8a8d`)
  - helper 抽出 → `_blender_material.py` (`bsdf_base_color` / `bsdf_image_texture` / `make_textured_mobj` / `blender_to_hsd`)
  - `_export_mkgp2_bundle.py` の grey fallback (`alloc_unlit_color(200,200,200,255)`) を `bm.make_textured_mobj(hsdraw, color, img_tuple)` に置き換え。stats dict に `fresh_materials` カウンタ追加
  - `tools/test_addon_bundle_add_mesh.py` v3 ケースを「色反映 + ad-hoc MObj count」を assert する形に強化 (PASS 確認済)
  - skill `mkgp2-edit-vanilla-course` の境界表を「新材 OK (BSDF 反映)」に更新
- [ ] **B-4: `tools/_blender_headless_promote.py` の引数仕様を README/skill から実機追試**
  - skill では `-- "<output_dat>"` 1 引数と書いた
  - 実コードを再読して invocation 行が現実と一致しているか確認 (もし複数 .blend を支える etc あれば skill 更新)

## C. 動作検証 / 実機確認待ち

- [ ] **C-1: round 3 (my_course) の `start_positions` 値検証**
  - 現在 yaml に書いた値が「Blender 軸変換が正しいか」を実機で確認する宿題
  - 確認方法: `mkgp2-view` で race 開始直後の kart 位置を読み、`_blender_to_hsd` 規則どおりか照合
  - skill の「動作確認手順」に書いた cheatsheet を一巡する作業

## D. 未確定 / 設計判断待ち (実害低、メモ留め)

- [ ] **D-1: vanilla `Auto.bin` の用途特定**
  - 現状コメント: "purpose still unclear" (= `tools/blender/blender_export_auto.py` head 8 行目)
  - 走らせるだけなら不要だが、何の path なのかは未解明 (= mini-map? AI 補助 line? camera path?)
  - PathManager 系ではないことだけ判明
  - 解明したら skill (`mkgp2-new-course` の「最低構成」表) と `mkgp2_course_layout_system.md` (Dolphin docs 側) を更新
- [x] **D-2: hsdraw `MObj.alloc_textured(material, image, **kwargs)` 一発 helper — 完了 (2026-05-11)**
  - hsdraw handoff #5 で上流実装、addon 側で採用 (commit `cd981fc`)
  - `_blender_material.make_textured_mobj` を ~25 行の explicit MObj/TObj/Image 配線から 1-call alloc_textured + 13 kwargs 明示に置換、byte-equivalent 検証済
  - 詳細: memory `project_hsdraw_2026_05_11_refactor_pass.md`
- [x] **format 選択 UI (Material EnumProperty `mkgp2_target_format`) — 完了 (2026-05-09)**
  - 当初は D-2 の関連 task として書いていたが、独立して完結したので別項目に切り出し
  - 採用: 3 択 (RGBA8 default / CMP / RGB5A3)、Material 単位で per-Material 設定、N panel `MKGP2 > Texture format` から
  - commits: `f10c512` helper 拡張 + `4a8fe62` exporter 結線 + `9b74b12` UI panel + EnumProperty + `<this>` test v5 (CMP 経路) + skill 更新
  - 検証: `tools/test_addon_bundle_add_mesh.py:v5` で `mkgp2_target_format = "CMP"` を設定した cube → 出力 .dat 内に CMP-format Image (= 32 byte) が含まれ、decode 後 pixel が orange と tolerance 32 以内で一致 PASS。byte-equiv (test_addon_hsd_export v0==v1 sha=45b73565...) も維持
  - 残る制約: vanilla CMP image を別 format に切り替える経路は無し (= 同 format で reencode のみ)、CMP の 4x4 alignment 違反は silent RGBA8 fallback
