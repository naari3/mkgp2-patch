# custom_assets — UI スプライト差し替え機構

`features/custom_assets/` の設計と使い方。

vanilla 側の resource システムの内部仕様は
`mkgp2docs/mkgp2_resource_asset_system.md`、binding / direct-insert の
2 アーキテクチャ全体像 + 新機構を作るときの recipe は
`docs/asset_pipeline.md` を先に読むこと。本 doc は **既存 custom_assets を
使ってカップを追加する** 手順に集中する。

---

## 概要

カスタムカップ (test_cup, cupId=17) 用の UI 画像を vanilla アトラスから
独立して差し込む。2 アーキテクチャを併用:

1. **cup-level asset** (icon / name / trophy / banner / cup_name_ribbon /
   name_roundselect) は **binding 方式** — vanilla ID を `kBindings[]` で
   remap して custom ID にすり替える
2. **round-level asset** (round 0..3 の thumb / thumb_road) は
   **direct-insert 方式** — custom ID (`0x4000..0x7FFF` の sign-safe range)
   を vanilla pipeline に直接流す。`round_select` 機能が PreInit で per-cup
   16-byte slot に inject する

両者とも resource lookup は同じ:
- 10 個の vanilla hook (8 getter + IsValidResourceId + ResourceSlot_Load) が
  custom ID を `kCustomResourceTable[]` から ResourceEntry 互換の値で返す
- `group_key >= 0x4000` の custom group は `kCustomPathTable[]` から独自 TPL
  ファイル名を返す
- TPL は yaml 駆動で PNG → RGBA32 TPL → zlib envelope に encode 済みで、
  Riivolution 経由で disc に貼り付け済

## 全体図

```
features/cups.yaml             features/course_models.yaml
  (cup + round 定義             (course model + joint 定義
   + 差し替え PNG パス)           — round.course_model から参照)
       │
       │ python gen_custom_assets_header.py
       ▼
generated_custom_assets.h                generated_riivolution.xml
  - kCustomResourceTable[]                 <file disc=... external=.../>
  - kBindings[]              (cup-level)   (TPL を disc tree に投入)
  - kCustomPathTable[]
  - kCupAliasMap[]
  - kRoundThumbInjects[]     (round-level)
       │                                            │
       │ #include                                   │ Riivolution patch
       ▼                                            ▼
custom_assets.cpp (10 hook)                files/mkgp2_custom_4*.tpl
  - 8 getter: GetOffsetXY / GetSizeXY /    (RGBA32 TPL on DVD)
              GetGroupKey / GetFilePathPtr /
              GetSlotIndex / GetChainNextId /
              GetScaleXY / GetFlagsByte
  - IsValidResourceId         (gate 拡張)
  - ResourceSlot_Load_BranchHook (THEN/ELSE 振り分け拡張)
  - ApplyBinding(id) で cup-level remap
  - CustomResource_Lookup() で table 引き
       │
       ▼
vanilla の caller (sprite 描画パイプライン) は
普通の getter を呼んでるつもりで custom 値を受け取る

(round-level は別経路で round_select.cpp が
 PreInit hook で per-cup slot に custom ID を inject、
 vanilla の indexing が直接 custom ID を読む)
```

---

## 構成要素

### `custom_assets.h`

カスタムリソースの C 構造体宣言。

- `CustomResourceEntry` — vanilla `ResourceEntry` (40 byte) と layout 互換。
  `group_key` を `u16` にしている (vanilla は `s16`) のは file-path table
  indexing (`groupKey - CUSTOM_GROUPKEY_BASE`) と型契約を揃えるため。
- `CupBinding` — `(cupId, fromId, toId)` の 3 組。`cupId == -1` で wildcard
  (g_cupId の値に関わらず常に発火)。cup-level asset 用。
- `CupAliasEntry` — `(customCupId, aliasVanillaCupId)`。round-select の
  cup-name strip 等を vanilla cup 経由で表示するための alias マップ。
- `RoundThumbInject` — direct-insert 方式の per-cup 16-byte slot 上書きデータ。
  `customCupId`, `nRounds` (yaml 定義 round 数 1..4), `thumbIds[8]`
  (round 0..3 × square+road)。`round_select` 機能が PreInit で書き込み、
  PreDtor で original を restore する。
- `CUSTOM_ID_BASE = 0x4000` / `CUSTOM_GROUPKEY_BASE = 0x4000` — vanilla
  resource ID と custom ID を分離する境界。**`< 0x8000`** であることが必須
  (sign-safe range、§ハマりやすい所参照)。

### `custom_assets.cpp`

実装本体。vanilla 構造体 / アドレス (`kResourceTableMain @0x80422208`,
`kResourceTableExt @0x8048da08`, `kResourcePathTable @0x80350508`,
`kExtendedResourcePathTable @0x8034a418`) は `mkgp2docs/mkgp2_resource_asset_system.md`
の説明と layout がそのまま該当する。

主要パーツ:

- 上記 vanilla 4 テーブルを `extern` で外部参照
- 10 個の `*_Hook` 関数 (8 getter + IsValidResourceId + ResourceSlot_Load の
  asm wrapper)。8 getter の共通フロー:

  ```c
  int GetGroupKey_Hook(int resourceId) {
      EnsureDBATWidened();
      resourceId = ApplyBinding(resourceId);    // remap
      const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
      if (c) return c->group_key;
      const VanillaResourceEntry* v = VanillaLookup(resourceId);
      if (v) return v->group_key;
      return 0;  // miss default
  }
  ```

- `ApplyBinding(id)`: `kBindings[]` を線形スキャンして
  `(cupId == -1 || cupId == g_cupId) && fromId == id` の最初のヒットで
  `toId` を返す。
- `CustomResource_Lookup(id)`: `kCustomResourceTable[]` を線形スキャン。
  PoC 規模 (< 64 件) なので線形で問題ない。
- `IsValidResourceId_Hook` (@`0x80122b90`): vanilla は `< 0x2b04` のみ valid。
  `CustomResource_Lookup(id) != 0` も valid と返す。これがないと
  PreloadResource が即 bail して slot 登録されず透明描画になる。
- `ResourceSlot_Load_BranchHook` (@`0x8011dccc`): vanilla の `bge` 命令
  (`r24 >= 0x2b00` で ELSE = in-mem buffer に分岐) を asm wrapper で置換。
  `r24 in [0x4000, 0x8000)` なら強制的に THEN (filename path) に流す。
  これがないと custom id が `DisplayBuffer_GetByIndex(0x1500+)` で OOB を
  読んで全 slot が同じ garbage を指す。

### `gen_custom_assets_header.py`

`features/cups.yaml` を読んで以下を生成:

- `generated_custom_assets.h` — `kCustomResourceTable[]` / `kBindings[]` /
  `kCustomPathTable[]` / `kCupAliasMap[]` / `kRoundThumbInjects[]` の C 定義
- `files/mkgp2_custom_4<NN>.tpl` — PNG を RGBA32 タイル化 + zlib envelope で
  くるんだ TPL ファイル (id = `0x4000 + cup_idx*16 + slot_off`)
- `generated_riivolution.xml` — disc tree に TPL を埋めるための Riivolution
  `<file>` フラグメント

`build.sh` が discover した時に自動実行される (`gen_*.py` 規約)。

### `cups.yaml` + `course_models.yaml`

差し替え対象の宣言。round entry が round-level asset を持つのが現スキーマ:

```yaml
cups:
  - id: test_cup
    cup_id: 17
    display_alias_cup: 7              # round-select で何カップに見せかけるか (Yoshi)
    assets:                           # cup-level (binding 方式)
      icon:            images/test_cup_icon.png
      name:            images/test_cup_name.png
      trophy:          images/test_cup_trophy.png
      banner:          images/test_cup_banner.png
      cup_name_ribbon: images/test_cup_name_ribbon.png
      name_roundselect: images/test_cup_name_roundselect.png
    rounds:                           # round-level (direct-insert 方式)
      - id: round1
        course_model: test_course     # course_models.yaml の id
        collision: grd_short.bin
        line_bin: test_course_short_line.bin
        thumb:      images/test_cup_course1_thumb.png       # 128x128 sq
        thumb_road: images/test_cup_course1_thumb_road.png  # 128x160 vert
        laps: 3
        time: 120.0
        bonus: 15.0
        bgm_l: bgm01_demoL.dsp
        bgm_r: bgm01_demoR.dsp
      - id: round2
        course_model: test_course
        ...
```

`cups.yaml` は `gen_cup_courses_header.py` (コース定義側) と
`gen_custom_assets_header.py` (アセット側) の両方が読む単一データソース。
重複させない。`course_models.yaml` は course model + joint 等の HSD 情報を
別 yaml に切り出したもの (cup と関係なく shared)。

### asset slot テーブル (`gen_custom_assets_header.py`)

差し替え可能な「スロット種別」の定義。cup-level と round-level に分離:

```python
# Cup-level (binding 方式)
CUP_ASSET_SLOTS = [
    # key             , vanilla_base, default_size, slot_off, cup_indexed
    ("icon"           , 0x1777, (128.0, 128.0), 0, True),
    ("name"           , 0x1729, (256.0,  46.0), 1, True),
    ("trophy_locked"  , 0x1EA2, ( 92.0,  86.0), 2, True),
    ("banner"         , 0x175E, (301.0, 125.0), 3, False),
    ("cup_name_ribbon", 0x1780, (148.0,  64.0), 4, True),
    ("name_roundselect", 0x16ED, (110.0,  67.0), 5, False),
]

# Round-level (direct-insert 方式、binding なし)
ROUND_ASSET_SLOTS = [
    # yaml_key   , default_size,    sub_off (round 内 2-slot 内オフセット)
    ("thumb"     , (128.0, 128.0), 0),    # square
    ("thumb_road", (128.0, 160.0), 1),    # vertical
]
ROUND_SLOT_BASE = 6        # cup-level 6 slot の後ろから
MAX_ROUNDS_PER_CUP = 4
```

| 列 (cup-level) | 意味 |
|---|---|
| `key` | yaml `assets:` 配下のキー名 (例: `icon`) |
| `vanilla_base` | 差し替え対象の vanilla resource ID 基準値 |
| `default_size` | デフォルト幅 / 高さ (PNG が違うサイズなら警告) |
| `slot_off` | カップ内の id 配置 (`0x4000 + cup_idx*16 + slot_off`) |
| `cup_indexed` | `True` なら `vanilla_base + 0..7` の 8 ID 全てに binding を撒く |

**`cup_indexed=True` は要注意**: vanilla の cup-select page 3 は「8 つのタイルが
それぞれ position-native な resource ID で描画される」(タイル 0 は 0x1777, タイル 1 は
0x1778, ...) ため、custom カップのアイコンを **全 8 タイル** に出すには 8 個の
binding が必要。これは「同じ画像を全タイルで表示するため」の意図的な仕様。

| 列 (round-level) | 意味 |
|---|---|
| `yaml_key` | yaml `rounds[].<key>` の key 名 (例: `thumb`) |
| `default_size` | デフォルト幅 / 高さ |
| `sub_off` | round 内の 2-slot ブロック (square=0, vertical=1) の offset |

round-level の id は `0x4000 + cup_idx*16 + ROUND_SLOT_BASE + roundIdx*2 + sub_off`。
yaml で round 2/3 が未定義なら最後の round の id を duplicate (vanilla の
Yoshi pattern を踏襲、未定義 round は `RoundIsUnlocked_Wrapper` で locked)。

---

## ID 割り当てルール

各 cup は `[0x4000 + cup_idx*16, 0x4000 + cup_idx*16 + 15]` の 16 ID を予約する。

| 用途 | offset | custom ID (test_cup, cup_idx=0) | 方式 |
|---|---|---|---|
| icon            | +0 | `0x4000` | binding |
| name            | +1 | `0x4001` | binding |
| trophy_locked   | +2 | `0x4002` | binding |
| banner          | +3 | `0x4003` | binding |
| cup_name_ribbon | +4 | `0x4004` | binding |
| name_roundselect| +5 | `0x4005` | binding |
| round[0].thumb       | +6 | `0x4006` | direct-insert |
| round[0].thumb_road  | +7 | `0x4007` | direct-insert |
| round[1].thumb       | +8 | `0x4008` | direct-insert |
| round[1].thumb_road  | +9 | `0x4009` | direct-insert |
| round[2].thumb       | +10 | `0x400A` | direct-insert |
| round[2].thumb_road  | +11 | `0x400B` | direct-insert |
| round[3].thumb       | +12 | `0x400C` | direct-insert |
| round[3].thumb_road  | +13 | `0x400D` | direct-insert |
| (予約)           | +14..+15 | (将来用) |

`cup_idx` は **`cups.yaml` 内での順序** であって `cup_id` ではない。
順序を入れ替えると ID がシフトするが、binding も同じ yaml から再生成されるので
vanilla 側からは見えない (内部一貫性のみ保たれる)。

---

## group_key 割り当てルール

各 PNG は固有の `group_key` を消費する。ID と独立で、`0x4000` から順に
インクリメント。`kCustomPathTable[group_key - 0x4000]` で TPL ファイル名が引ける。

```c
const char* const kCustomPathTable[] = {
    "mkgp2_custom_4000.tpl",  // 0x4000
    "mkgp2_custom_4001.tpl",  // 0x4001
    "mkgp2_custom_4002.tpl",  // 0x4002
    ...
};
```

vanilla の `kResourcePathTable[]` (`0x80350508`) は触らず、
`GetFilePathPtr_Hook` 内の `ResolveFilePath()` で custom range だけ別経路に
振り分ける。

---

## TPL encode 仕様

`gen_custom_assets_header.py` の `encode_png_to_tpl()` がやってる:

1. PNG を Pillow で `RGBA` として開く
2. `_rgba32_encode()` で 4×4 タイル順序に並び替え
   (各タイル = 32 byte AR ペア + 32 byte GB ペア = 64 byte)
3. `_build_tpl_rgba32()` で標準 GameCube TPL ヘッダ (56 byte) を被せる
4. `_wrap_tpl_envelope()` で `(LE u32 size)(LE u32 0)(zlib)` で envelope

format は **常に RGBA32** (format コード 6)。理由:
- alpha チェインを切って単独 RGBA で完結させているため
- 元 atlas の I4/IA4/RGB565 と異なる format でも、ResourceEntry 側の
  `next_id = -1` で alpha 経路が走らないので blend 不一致が起きない

---

## g_cupId のゲーティング

binding は `g_cupId == binding.cupId` のときだけ発火する
(`cupId == -1` なら wildcard で常時発火)。

これは「custom カップを表示しているあいだだけ vanilla resource を差し替えたい」
ためで、g_cupId が `cup_page3` 機能側で:

- カップ選択ページ 2 → 3 遷移時に `g_cupId = 17` に書き換え
- ページ 2 退出 / cup-select scene init で `g_cupId = 0` に戻す

されることに依存している。詳細は `features/cup_page3/cup_page3.cpp` 参照。

---

## hook を撃つ vanilla アドレス (一覧)

`kmBranch` で 10 本パッチ:

```c
// 8 getter family — ResourceEntry の 1 field を返す API。
// 全部差し替えないと「flags は custom だが size は vanilla」のような不整合になる。
kmBranch(0x80122730, GetOffsetXY_Hook);
kmBranch(0x80122658, GetSizeXY_Hook);
kmBranch(0x801224b4, GetScaleXY_Hook);
kmBranch(0x801223e8, GetFlagsByte_Hook);
kmBranch(0x8012258c, GetChainNextId_Hook);
kmBranch(0x80122808, GetSlotIndex_Hook);
kmBranch(0x80122ac4, GetGroupKey_Hook);
kmBranch(0x801229c4, GetFilePathPtr_Hook);

// pipeline gate / dispatch — direct-insert 方式に必要。
kmBranch(0x80122b90, IsValidResourceId_Hook);
kmBranch(0x8011dccc, ResourceSlot_Load_BranchHook);
```

direct-insert 方式 (round-level asset) を使わない場合でも上 2 つを installed
にしておくのは安全 (vanilla id は今まで通り動く)。

---

## 新しい cup を追加する手順

1. `features/cups.yaml` の `cups:` リストに新エントリを追加
   - `id` (識別子), `cup_id` (>= 17 で空きスロット), `display_alias_cup`
   - `assets:` 配下に差し替えたい PNG を列挙
2. PNG を `features/custom_assets/images/` (パスは yaml に書いた通り) に配置
3. `bash build.sh` 実行 → 自動で
   - `gen_custom_assets_header.py` が走って `generated_custom_assets.h` 更新
   - PNG → TPL encode + `files/` に出力
   - `mkgp2_patch.bin` 再ビルド + Riivolution XML 更新

## 新しいスロット種別を追加する手順

### cup-level (binding 方式)

1. vanilla 側で「どの resource ID をどの groupKey 経由で読んでいるか」を
   特定 (Ghidra で getter caller を辿る)
2. `gen_custom_assets_header.py` の `CUP_ASSET_SLOTS` に行を追加:
   ```python
   ("my_new_asset", 0x1234, (128.0, 64.0), 5, False),
   ```
   `cup_indexed=False` にするのは、その asset がカップ内固定 ID を使う場合
3. `cups.yaml` の `assets:` ブロックに対応キーを追加
4. PNG 配置 + ビルド

### round-level (direct-insert 方式)

1. vanilla の per-round indexing 経路を特定 (例: `FUN_801c9288` の
   `&DAT_8049aea0[sub_idx*16 + roundIdx*4]` 読み出しなど)
2. `gen_custom_assets_header.py` の `ROUND_ASSET_SLOTS` に行を追加。
   現状は thumb (sq) + thumb_road (vert) の 2 種だが、追加なら sub_off を
   伸ばす
3. `cups.yaml` の `rounds[]` 内の各 round entry に対応キーを追加
4. inject 先の vanilla テーブル (per-cup slot 等) に書き込む logic は
   `round_select.cpp` のような scene-specific な機能側で書く
5. PNG 配置 + ビルド

### 全く別のカテゴリの asset を新設したい場合

`docs/asset_pipeline.md` の "新機構の作り方 (recipe)" を参照。custom_assets と
同じ pipeline (`kCustomResourceTable[]` + 10 hook) に乗せられるなら、
yaml schema の拡張だけで済む。乗らない (sprite が vanilla pipeline 外) なら
別 feature を立てる。

---

## ハマりやすい所

### custom ID は `< 0x8000` 必須 (sign-safe range)

vanilla の `Sprite_SetAnimParam` 経由で resource id が `short` 扱いされる
経路があり、mod id の high bit が立つと sign-extend で slot lookup miss →
透明描画になる。**binding 方式では出ない、direct-insert 方式でのみ顕在化**。

メカニズム詳細: `mkgp2docs/mkgp2_resource_asset_system.md` § Sprite_SetAnimParam。
mod 側の判断と現状: `docs/asset_pipeline.md` § 3 Sign-safe ID range。

### vanilla テーブルの境界を踏み抜く

vanilla の cup-indexed テーブル (例: `kCup0LineBinTable`) は `cupId 0..8` までしか
要素を持っていない。`cupId == 17` を投げると **隣接テーブルを読みに行って
別データを破壊**する。custom カップを動かすときは getter ごとに hook して
範囲外を独自テーブルに振り替える必要がある (これは `cup_page3` 側の責務、
custom_assets 自体ではやってない)。

### log 由来の resource ID を bulk bind するな (重要)

`g_cupId == 17` 中に走った全 resource id を集めて bulk で binding を追加すると、
本来カップタイルとは無関係な help text / subtitle banner / BG atlas の ID も
全部リダイレクトされて画面崩壊する。1 個ずつ追加して目視確認すること。

詳細は `tasks/lessons.md` の "2026-04-24" エントリ参照。

### preload のタイミング

vanilla の per-frame UV refresh パスは slot miss しても slot を作成しない
(詳細: `mkgp2docs/mkgp2_resource_asset_system.md` § per-frame UV refresh path
は slot を作成しない)。そのため custom resource を初めて参照する瞬間に
slot が作られていないと 1 frame だけ vanilla 側がフラッシュする。

`custom_assets.cpp` の `TryPreloadCustomAssetsAtCup17()` が g_cupId が初めて
17 になったときに、binding 全件を `PreloadResource()` で先回りロードしている。
direct-insert 方式 (round-level asset) は `round_select.cpp` 側で同等の
preload を行う (PreInit hook で inject 後に `PreloadResource(customId)` を call)。

### `next_id` の chain

vanilla の RGB / alpha ペア (`0x1777` → alpha `0x178B` 等) を完全に再現したい
なら custom 側の `next_id` も chain させる必要があるが、alpha 側の format
(I4 / IA4) を RGBA32 に差し替えると blend が破綻する。MVP では `next_id = -1`
で chain を切って RGBA32 単独完結させるのが安全。

---

## 関連コード参照

- `features/custom_assets/custom_assets.cpp` (実装、10 hook)
- `features/custom_assets/custom_assets.h` (構造体)
- `features/custom_assets/gen_custom_assets_header.py` (yaml → header + TPL)
- `features/cups.yaml` (データソース、cup + round 定義)
- `features/course_models.yaml` (course model + joint 定義)
- `features/cup_page3/cup_page3.cpp` (g_cupId をどこで 17 にするか + cup-indexed table 振り替え)
- `features/round_select/round_select.cpp` (round-level direct-insert の inject/restore)
- `docs/asset_pipeline.md` (binding vs direct-insert アーキテクチャ全体像)
- `tools/extract_yoshi_round_assets.py` (vanilla atlas からのクロップ抽出例)
- `mkgp2docs/mkgp2_resource_asset_system.md` (vanilla 側仕様)
- `tasks/lessons.md` (過去のハマりポイント)
