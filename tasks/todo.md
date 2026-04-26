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

### C-2a: cup-name banner bot direct-insert (DAT_8049ade6, 0x1729系)
- [ ] CupSelectInject に nameBotId 追加 (custom_assets.h)
- [ ] gen で yaml `name` から nameBotId を拾う
- [ ] cup_page3 の Apply/Restore に DAT_8049ade6 inject 追加
  - 同テーブルの top (DAT_8049ade4 / 0x1758系) は当面 vanilla 流用 (yaml schema 未対応 → C-2c)
  - immediate-mode draw (clFlowCup_Draw 内 FUN_801a1174) なので debug_overlay には出ない
  - binding でも結果は同じだが、binding 廃止のため direct-insert 化
- [ ] ビルド + Dolphin 動作確認

### C-2b: trophy direct-insert (DAT_8039b218, 0x1EA2系) — Ghidra 偵察必要
- **罠**: FUN_801c64dc 内で `glyphIdxRight = DAT_8039b218[cursor*2] + (unlocked ? 8 : 0)`。
  custom_id を書き込んでも +8 で別 custom_id を参照する必要があり、IDS_PER_CUP=16 では
  slot 2 + 8 = slot 10 が round[2].thumb と必然衝突。
- 対策候補:
  - (A) IDS_PER_CUP=32 拡張 + slot 再配置 (trophy_locked=0x4010, unlocked=0x4018 等)
  - (B) FUN_801c64dc の +8 加算自体を hook で潰し、custom cup なら trophy_unlocked custom_id を別途返す
  - (B) のほうが slot 配置の自由度が残る。先に Ghidra MCP で FUN_801c64dc を decompile して判断
- [ ] FUN_801c64dc decompile + +8 加算箇所特定
- [ ] (A)/(B) 選択
- [ ] yaml schema に trophy_unlocked 追加 (なければ trophy で alias)
- [ ] CupSelectInject に trophyId / trophyUnlockedId 追加
- [ ] cup_page3 の Apply/Restore 拡張
- [ ] ビルド + Dolphin 動作確認

### C-2c: cup-name banner top direct-insert (DAT_8049ade4, 0x1758系)
- [ ] yaml に `name_top` key 追加 (or `name` を top/bot 兼用にする方針決定)
- [ ] CupSelectInject に nameTopId 追加
- [ ] cup_page3 の Apply/Restore 拡張
- [ ] ビルド + Dolphin 動作確認

### C-2d: cup-select banner (0x175E, anim asset 内蔵) — 当面保留
- vanilla の anim asset 内蔵描画なので sprite 経由でなく direct-insert 不可
- 必要なら anim 自体を replace する別アプローチ要 (大規模)

### C-3: cup-select の kBindings entry 段階削除
- [ ] gen で cup-select 系 binding (icon/ribbon/trophy/banner/name) を出さないオプション追加
- [ ] 実行: 削除して動作確認 (= direct-insert のみで動くか)
- [ ] OK なら最終削除

### C-4: round-select sub_index=0 entry inject (cup-name 0x16ED 等)
- [ ] DAT_8049afa0[0] (cup-name) の inject
- [ ] DAT_8039b308[0..7], DAT_8049af78[0] 等の他 sub_index 0 entry も round-select scope で inject
- [ ] 既存 InjectRoundThumbs に統合
- [ ] ビルド + 動作確認

### C-5: round-select の binding 削除
- [ ] gen から round-select 系 binding を除外
- [ ] 動作確認

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
