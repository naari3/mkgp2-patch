# vanilla / mod アセット描画パイプライン

カスタム描画の **アーキテクチャ** をまとめた doc。  
具体的なカップ追加手順は `docs/custom_assets.md`、vanilla 側の resource システム
内部仕様は `mkgp2docs/mkgp2_resource_asset_system.md` を参照。

ここでは「**新しい mod 機構が同じ pipeline に乗る**ために知っておくべき構造」を
整理する。custom_assets / round_select 以外の機能 (新しい UI 領域 / 新しい
sprite カテゴリ等) を後付けする際の出発点。

---

## 1. vanilla resource pipeline (前提)

vanilla の sprite / texture は次の 4 ステージで描画される:

```
   caller (gameplay logic)
        │
        │ resourceId (int)
        ▼
   ┌──────────────────────────────────┐
   │ IsValidResourceId  @0x80122b90   │ id < 0x2b04 のみ通す (gate)
   └──────────────────────────────────┘
        │
        ▼
   ┌──────────────────────────────────┐
   │ PreloadResource   @0x80120d80    │ slot を allocate / hit
   └──────────────────────────────────┘
        │
        ▼
   ┌──────────────────────────────────┐
   │ ResourceSlot_Load @0x8011dca4    │ TPL を DVD/in-mem から load
   │   bge @0x8011dccc                │
   │   ├ THEN: filename path          │ id < 0x2b00 → ResourceEntry.group_key
   │   │       → kResourcePathTable[]  │   → DVD load
   │   └ ELSE: in-mem buffer          │ id >= 0x2b00 → DisplayBuffer_GetByIndex
   └──────────────────────────────────┘
        │
        ▼
   ┌──────────────────────────────────┐
   │ slot registry @0x806573e8        │ 600 slots × 28 bytes
   │   slot[0] resourceId             │
   │   slot[1] groupKey               │
   │   slot[2] resourceDataPtr (TPL)  │
   │   slot[3..6] sub-data            │
   └──────────────────────────────────┘
        │
        ▼
   ┌──────────────────────────────────┐
   │ 8 getter family @0x801223e8..    │ ResourceEntry のフィールドを 1 個ずつ
   │   GetFlagsByte                   │ 返す API。sprite 描画パスがこれで
   │   GetScaleXY  / GetSizeXY        │ texture rect / atlas tile / next chain
   │   GetOffsetXY / GetGroupKey      │ 等を引く
   │   GetFilePathPtr / GetSlotIndex  │
   │   GetChainNextId                 │
   │   IsValidResourceId               │
   └──────────────────────────────────┘
```

### resourceId の意味別レンジ

| range | 用途 | テーブル | 経路 |
|---|---|---|---|
| `0x0000..0x2AFF` | vanilla main resource | `kResourceTableMain @0x80422208` (11008 entries × 40 byte) | filename (THEN) |
| `0x2B00..0x2B03` | vanilla extended resource | `kResourceTableExt @0x8048da08` (4 entries) | in-mem (ELSE) |
| `0x2B04..0x3FFF` | **未使用** (mod が使ってよい) | — | — |
| `0x4000..0x7FFF` | **mod direct-insert 推奨域** (sign-safe) | `kCustomResourceTable[]` | filename (THEN, hook で強制) |
| `0x8000..0xFFFF` | **使用禁止** (sign-extension トラップ) | — | — |

### ResourceEntry 40-byte layout

`VanillaResourceEntry` (`features/custom_assets/custom_assets.h`) と
`CustomResourceEntry` は同 layout:

| offset | type | field | 用途 |
|---|---|---|---|
| +0x00 | u16 | `self_id` | この resource の id (sanity) |
| +0x04 | f32 | `offset_x/y` | atlas 内 UV offset |
| +0x0C | f32 | `size_x/y` | atlas 内 tile size |
| +0x14 | s16 | `slot_index` | slot registry 内の位置 (chain 用) |
| +0x16 | u16 | `group_key` | filename table index |
| +0x18 | s16 | `next_id` | RGB↔alpha chain (-1 で終端) |
| +0x1C | f32 | `scale_x/y` | sprite 描画 scale |
| +0x24 | u8  | `flags` | format / blend mode |

`group_key` は vanilla で s16 だが、custom で `u16` 扱いするのは
sign-safe range (< 0x8000) を満たすため。

### per-cup 16-byte slot @ `0x8049aea0`

cup-select 上の各 cup には 16 byte の slot が紐づき、内部に 4 round × 4 byte
(square thumb id 2 byte + road thumb id 2 byte) の resource id が並ぶ。
round-select の sprite 描画 (`FUN_801c9288`) はこの slot を直接 indexing する:

```
&DAT_8049aea0[sub_index * 16]
    +0x00: round0 square id (u16)
    +0x02: round0 road   id (u16)
    +0x04: round1 square id (u16)
    +0x06: round1 road   id (u16)
    +0x08: round2 square id (u16)
    +0x0A: round2 road   id (u16)
    +0x0C: round3 square id (u16)
    +0x0E: round3 road   id (u16)
```

`sub_index` は `0x8049af8c[g_cupId]` の lookup table で取れる。

---

## 2. mod 拡張の 2 アーキテクチャ

vanilla pipeline に mod の resource を流し込むには 2 通りある。それぞれ
trade-off があるので、追加する機能の性質で選ぶ。

### A. Binding (piggyback) — vanilla resource id を借りる

**仕組み**: vanilla の getter hook 内で `ApplyBinding(resourceId)` を呼び、
`kBindings[(cupId, fromVanillaId, toCustomId)]` に hit したら `toCustomId` を
返す。slot registry には **vanilla id で entry が登録される**ので、ID range
の sign 問題に当たらない。

**長所**:
- 追加 hook は **8 getter のみ** (custom_assets PoC の最初の形)
- vanilla の PreloadResource / ResourceSlot_Load 経路をそのまま使える
- `g_cupId` で gating できる (custom cup を表示中のみ binding 発火)

**短所**:
- **vanilla の atlas 数が天井**。例: vanilla cup-slot は 1 cup 当たり
  square/road の 2 thumb しか入らないので、4 round 独立 thumb は実装不可
- vanilla id を上書きするので、その id を本来引いている vanilla 側コードが
  巻き添えになる。binding は cupId で scope を絞らないと事故る (詳細:
  `tasks/lessons.md` の log 由来 ID bulk bind の禁忌)

**該当: cup-level asset (icon, name, trophy, banner, cup_name_ribbon,
name_roundselect)**。1 cup = 1 個の resource id で足りる UI 要素はこれで
十分。

### B. Direct-insert — 未使用 ID 範囲を mod 専用に占有

**仕組み**: `0x4000..0x7FFF` の sign-safe vanilla 未使用域から custom id を
allocate し、vanilla の `IsValidResourceId` と `ResourceSlot_Load` の bge 命令
を hook で迂回することで、custom id をそのまま vanilla pipeline に流す。
slot registry には **custom id で entry が登録される**。

**長所**:
- vanilla の atlas/slot 数の制約から完全に独立
- 任意の数の独立 sprite を持てる (per-round 独立画像、per-cell ribbon 等)
- `kCustomResourceTable[]` の self_id を任意に設計できる

**短所**:
- `IsValidResourceId_Hook` と `ResourceSlot_Load_BranchHook` の **2 つの追加
  hook が必要** (vanilla の gate を通すため)
- **PreloadResource を能動的に呼ぶ必要**。vanilla の per-frame UV refresh パス
  は slot miss で slot 作成しないので、初回フレームに間に合わせるには
  setup phase で `PreloadResource(customId)` を 1 回叩く
- **ID は < 0x8000 必須** (後述)。high bit セットの id は sign-extension で
  slot lookup miss する

**該当: round-level asset (per-round thumb / thumb_road)**。1 cup × 4 round
× 2 種で 8 sprite 必要なので vanilla cup-slot (2 sprite/cup) では収まらない。

### 判断フローチャート

```
追加したい sprite が...
  ├ vanilla の同種 sprite を「上書き」して見せる形で十分?
  │   (vanilla cup icon を独自画像に差し替える 等)
  │   → A. binding
  │
  └ vanilla には存在しない sprite カテゴリを新設?
    /  vanilla の atlas 数では収まらない sprite 数が必要?
       → B. direct-insert
```

---

## 3. Sign-safe ID range (絶対制約)

`CUSTOM_ID_BASE = 0x4000` は **設計選択ではなく制約**。

vanilla の `Sprite_SetAnimParam(sprite, paramId, short value)` (`@0x801a0374`)
は値を **signed 16-bit** で取る。round-select 等で resource id を sprite anim
param table に格納する経路があり、consumer がこれを `(short)` で読み戻すと
sign-extend される。

```
custom id = 0x9006
        │
        │ Sprite_SetAnimParam に short として格納
        ▼
   anim param table: 0x9006 (16-bit, signed)
        │
        │ consumer が (short) で読む → sign-extend
        ▼
   読み戻し値: 0xFFFF9006 (32-bit signed)
        │
        │ slot registry の slot[0] (= resourceId, full int) と比較
        ▼
   slot[0] = 0x00009006  ≠  0xFFFF9006  → miss → 透明
```

binding 方式 (A) では slot は vanilla id (`< 0x8000`) で keyed されるので
sign-safe。**direct-insert (B) で初めて顕在化する**。

### 安全範囲

```
[0x2B04, 0x8000)  ← vanilla 未使用、かつ signed positive
```

`CUSTOM_ID_BASE = 0x4000` は中央寄りで余裕を持って取った値。`0x9xxx` 系は
**動作確認できる小規模 asset (binding 経由) では問題が出ない**ので debug が
極めて困難。新 ID 域を選ぶときは必ず `< 0x8000` を確認する。

→ 詳細: `tasks/lessons.md` の sign-safe entry / memory の `feedback_custom_resource_id_sign_safe.md`

### 範囲を変えるときの追従先

`CUSTOM_ID_BASE` / `CUSTOM_GROUPKEY_BASE` を変える場合は以下を全部直す:

- `features/custom_assets/custom_assets.h` の定数
- `features/custom_assets/gen_custom_assets_header.py` の `CUSTOM_ID_BASE` /
  `CUSTOM_GROUPKEY_BASE`
- `features/custom_assets/custom_assets.cpp` の `ResourceSlot_Load_BranchHook`
  内の range (`cmplwi r24, 0x4000` / `cmplwi r24, 0x8000`)
- `features/custom_assets/custom_assets.cpp` の `TryPreload` group_key 範囲
  (slot registry scan の `gk >= 0x4000 && gk <= 0x4100`)
- `features/round_select/round_select.cpp` の preload skip 閾値

---

## 4. 新機構の作り方 (recipe)

custom_assets / round_select 以外で同じ pipeline を活用したい場合のテンプレ。

### Step 1. アーキテクチャ選択

§2 の判断フローで A or B を決める。
- **A. binding**: vanilla の同種 sprite を上書きしたいだけ
- **B. direct-insert**: vanilla には存在しない sprite を新設、または vanilla
  の atlas 数を超える sprite が必要

### Step 2. ID allocate

direct-insert の場合は `[0x4000, 0x8000)` から空き帯を選ぶ。custom_assets が
今 `0x4000..0x4100` 使用中なので、新機構は `0x4100..` から取る。yaml-driven
で id を seq allocate する設計を踏襲する (人間が hard-code しない)。

binding の場合は対象 vanilla id を `mkgp2docs/mkgp2_resource_asset_system.md`
で確認、`kBindings[]` に行を追加するだけ。

### Step 3. asset 登録

direct-insert の場合は新機構の generator script で:
1. `kCustomResourceTable[]` に新 entry を append (custom_assets と同じ table
   を使うか、別 table を立てるかは規模で判断)
2. `kCustomPathTable[]` に TPL ファイル名を append
3. PNG → TPL encode (`encode_png_to_tpl()` 流用)
4. Riivolution XML に `<file>` フラグメント追加

custom_assets の `gen_custom_assets_header.py` をテンプレにすると最短。

### Step 4. vanilla への inject ポイント

direct-insert で id を「どこに置く」かは機能ごとに違う:

- per-cup slot → `&DAT_8049aea0[sub_index*16]` (round_select.cpp 方式)
- 別の per-X table → vanilla 側を Ghidra で読んで該当テーブルを特定
- sprite 個別の `SetResource` 呼び出し直前に hook で id 差し替え

inject タイミングは scene の **PreInit** に hook、scene 終了時の **PreDtor**
で original を restore するのがクリーン (round_select.cpp の
`InjectRoundThumbs` / `RestoreRoundThumbs` 参照)。

### Step 5. PreloadResource を能動的に呼ぶ

per-frame UV refresh パスは slot miss で slot 作成しないので、scene PreInit
で inject 直後に `PreloadResource(customId)` を 1 回叩いて slot 登録を確定
させる。これがないと初回フレームに vanilla の transparent / garbage が
ちらっと見える。

### Step 6. (binding のみ) g_cupId 等で gating

binding は wildcard (`cupId == -1`) で常時発火させると vanilla の他カップが
壊れる。何かしら scope を持たせる:

- `g_cupId` で絞る (custom_assets と同じ)
- scene state (`g_currentSceneState`) で絞る
- 自前の scope 変数 (`g_customCupScope` 等) を機能側から SetScope する

direct-insert は custom id 自体が vanilla と被らないので gating 不要。

---

## 5. アンチパターン

### 5.1 log 由来 ID を bulk bind する (binding 方式)

「g_cupId == 17 中に getter が引かれた id を全部 binding に追加」は禁忌。
help text / subtitle / BG atlas など本来カップタイルとは無関係な id まで
リダイレクトされて画面崩壊する。binding は **目視で 1 個ずつ確定**。
→ `feedback_custom_assets_bulk_sibling_bind_forbidden.md`

### 5.2 ID >= 0x8000 を使う (direct-insert 方式)

§3 参照。動作する小規模実装で問題が出ないので debug が極めて辛い。
新 ID 域を選ぶときは必ず `< 0x8000` を確認。

### 5.3 vanilla テーブル境界を超える

vanilla の cup-indexed テーブル (例: `kCup0LineBinTable`) は cupId 0..8 まで
しか持っていない。`cupId == 17` を投げると隣接テーブルを破壊。
custom cup を動かす機能は getter ごとに hook して範囲外を独自 table に
振り替える (cup_page3 の責務)。
→ `project_cupid_table_boundaries.md`

### 5.4 `next_id` chain を再現しようとする

vanilla の RGB / alpha ペア (`0x1777` → alpha `0x178B` 等) を完全再現する
場合、custom 側も `next_id` を chain させる必要があるが、alpha 側の format
(I4 / IA4) を RGBA32 に差し替えると blend が破綻する。MVP では
`next_id = -1` で chain を切って RGBA32 単独完結。

---

## 6. 関連コード参照

| 機能 | パス | 役割 |
|---|---|---|
| custom_assets | `features/custom_assets/custom_assets.cpp` | 8 getter + 2 pipeline hook |
| custom_assets | `features/custom_assets/custom_assets.h` | 構造体 / 定数 |
| custom_assets | `features/custom_assets/gen_custom_assets_header.py` | yaml → header + TPL + XML |
| round_select  | `features/round_select/round_select.cpp` | per-cup slot inject / restore |
| cup_page3     | `features/cup_page3/cup_page3.cpp` | g_cupId 維持 / cup-indexed table 振り替え |
| データソース   | `features/cups.yaml` | cup + round 定義 |
| データソース   | `features/course_models.yaml` | course model + joint 定義 |
| 個別ガイド     | `docs/custom_assets.md` | カップ追加手順 |
| vanilla 仕様  | `mkgp2docs/mkgp2_resource_asset_system.md` | resource システム rev-eng |
| 教訓集        | `tasks/lessons.md` | sign-safe / bulk bind 等 |
