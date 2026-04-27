# direct-insert 統一: cup-select / round-select 全アセット移行

## 目標

カップセレクト / ラウンドセレクト で表示される全カスタムアセットを **直接挿入方式** (sprite に書き込まれる resourceId 自体を 0x4000+ に変更) へ統一する。**g_cupId override (現 round_select の Yoshi swap, cup_page3 の g_cupId=17 強制) を廃止**する。

完了状態: debug_overlay で表示される resourceId が 全て 0x4000+ で統一、`g_customCupScope` swap 機構が消え、`kBindings[]` 廃止。

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

### C-6: 後始末
- [ ] cup_page3.cpp から `*(u32*)0x806cf108u = 17u;` を削除
- [ ] custom_assets から `kBindings[]` 全廃 (関連 log/診断コード削除)
- [ ] `g_customCupScope` 削除
- [ ] ビルド + 動作確認

### C-5: 検証
- [ ] cup-select page 2 で test_cup の icon/name/trophy/banner/ribbon が正常表示
- [ ] round-select で cup name + 4 thumb が正常表示、レース突入 → 完走で問題なし
- [ ] vanilla cup (Yoshi 等) が壊れていない
- [ ] debug_overlay (mode 2/3) で全 sprite が 0x4000+ ID で表示
- [ ] dolphin.log に新たな error/warning が出ていない
- [ ] tasks/lessons.md に学びを追記
