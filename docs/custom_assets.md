# custom_assets — UI スプライト差し替え機構

`features/custom_assets/` の設計と使い方。

vanilla 側の resource / atlas システムの理解は前提なので、まず
`mkgp2docs/mkgp2_resource_asset_system.md` を読むこと。

---

## 概要

カスタムカップ (test_cup, cupId=17) 用の UI 画像を vanilla アトラスから
独立して差し込む。やってることは:

1. **vanilla resource 8 兄弟 getter にすべて hook を仕込む**
2. getter は ID を `kBindings[]` で remap (vanilla ID → custom ID)
3. custom ID なら `kCustomResourceTable[]` から ResourceEntry 互換の値を返す
4. `group_key >= 0x9000` の custom group は `kCustomPathTable[]` から
   独自 TPL ファイル名を返す
5. その TPL は yaml 駆動で PNG → RGBA32 TPL → zlib envelope に encode 済みで、
   Riivolution 経由で disc に貼り付け済

## 全体図

```
features/cups.yaml  (cup 定義 + 差し替え PNG パス)
       │
       │ python gen_custom_assets_header.py
       ▼
generated_custom_assets.h           generated_riivolution.xml
  - kCustomResourceTable[]            <file disc=... external=.../>
  - kBindings[]                       (TPL を disc tree に投入)
  - kCustomPathTable[]
       │                                       │
       │ #include                              │ Riivolution patch
       ▼                                       ▼
custom_assets.cpp (8 hook)            files/mkgp2_custom_*.tpl
  - GetOffsetXY / GetSizeXY / ...     (RGBA32 TPL on DVD)
  - ApplyBinding(id) で remap
  - CustomResource_Lookup() で table 引き
       │
       ▼
vanilla の caller (sprite 描画パイプライン) は
普通の getter を呼んでるつもりで custom 値を受け取る
```

---

## 構成要素

### `custom_assets.h`

カスタムリソースの C 構造体宣言。

- `CustomResourceEntry` — vanilla `ResourceEntry` (40 byte) と layout 互換。
  ただし `group_key` を `u16` にしている (vanilla は `s16`)。custom 値が
  0x9000 以上なので、`s16` だと符号拡張で負になって判定が壊れる。
- `CupBinding` — `(cupId, fromId, toId)` の 3 組。`cupId == -1` で wildcard
  (g_cupId の値に関わらず常に発火)。
- `CUSTOM_ID_BASE = 0x9000` / `CUSTOM_GROUPKEY_BASE = 0x9000` — vanilla
  resource ID と custom ID を分離する境界。

### `custom_assets.cpp`

実装本体。主要パーツ:

- `kResourceTableMain` (`0x80422208`) と `kResourceTableExt` (`0x8048da08`) を
  `VanillaResourceEntry*` として外部参照
- `kResourcePathTable` (`0x80350508`) と `kExtendedResourcePathTable`
  (`0x8034a418`) を `extern` 宣言
- 8 個の `*_Hook` 関数 (1 個の getter につき 1 個)。共通フロー:

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

### `gen_custom_assets_header.py`

`features/cups.yaml` を読んで以下を生成:

- `generated_custom_assets.h` — `kCustomResourceTable[]` / `kBindings[]` /
  `kCustomPathTable[]` の C 定義
- `files/mkgp2_custom_<id>.tpl` — PNG を RGBA32 タイル化 + zlib envelope で
  くるんだ TPL ファイル
- `generated_riivolution.xml` — disc tree に TPL を埋めるための Riivolution
  `<file>` フラグメント

`build.sh` が discover した時に自動実行される (`gen_*.py` 規約)。

### `cups.yaml` (`features/cups.yaml` でも可)

差し替え対象の宣言:

```yaml
cups:
  - id: test_cup
    cup_id: 17
    display_alias_cup: 0      # UI 上で何カップに見せかけるか
    assets:
      icon:           images/test_cup_icon.png
      name:           images/test_cup_name.png
      trophy:         images/test_cup_trophy.png
      banner:         images/test_cup_banner.png
      cup_name_ribbon: images/test_cup_name_ribbon.png
    courses:
      - id: test_course
        ...
```

`cups.yaml` は `gen_cup_courses_header.py` (コース定義側) と
`gen_custom_assets_header.py` (アセット側) の両方が読む単一データソース。
重複させない。

### `ASSET_SLOTS` テーブル (`gen_custom_assets_header.py`)

差し替え可能な「スロット種別」の定義。1 行 = 1 種類のアセット:

```python
ASSET_SLOTS = [
    # key             , vanilla_base, default_size, slot_off, cup_indexed
    ("icon"           , 0x1777, (128.0, 128.0), 0, True),
    ("name"           , 0x1729, (256.0,  46.0), 1, True),
    ("trophy"         , 0x1EA2, ( 92.0,  86.0), 2, True),
    ("banner"         , 0x175E, (301.0, 125.0), 3, False),
    ("cup_name_ribbon", 0x1780, (148.0,  64.0), 4, True),
]
```

| 列 | 意味 |
|---|---|
| `key` | yaml `assets:` 配下のキー名 (例: `icon`) |
| `vanilla_base` | 差し替え対象の vanilla resource ID 基準値 |
| `default_size` | デフォルト幅 / 高さ (PNG が違うサイズなら警告) |
| `slot_off` | カップ内の id ローテーション (`base + cup_idx*8 + slot_off`) |
| `cup_indexed` | `True` なら `vanilla_base + 0..7` の 8 ID 全てに binding を撒く |

**`cup_indexed=True` は要注意**: vanilla の cup-select page 3 は「8 つのタイルが
それぞれ position-native な resource ID で描画される」(タイル 0 は 0x1777, タイル 1 は
0x1778, ...) ため、custom カップのアイコンを **全 8 タイル** に出すには 8 個の
binding が必要。これは「同じ画像を全タイルで表示するため」の意図的な仕様。

---

## ID 割り当てルール

各 cup は `[0x9000 + cup_idx*8, 0x9000 + cup_idx*8 + 7]` の 8 ID を予約する。

| 用途 | offset | custom ID (test_cup の例、cup_idx=0) |
|---|---|---|
| icon            | +0 | `0x9000` |
| name            | +1 | `0x9001` |
| trophy          | +2 | `0x9002` |
| banner          | +3 | `0x9003` |
| cup_name_ribbon | +4 | `0x9004` |
| (予約)           | +5..+7 | (将来用) |

`cup_idx` は **`cups.yaml` 内での順序** であって `cup_id` ではない。
順序を入れ替えると ID がシフトするが、binding も同じ yaml から再生成されるので
vanilla 側からは見えない (内部一貫性のみ保たれる)。

---

## group_key 割り当てルール

各 PNG は固有の `group_key` を消費する。ID と独立で、`0x9000` から順に
インクリメント。`kCustomPathTable[group_key - 0x9000]` で TPL ファイル名が引ける。

```c
const char* const kCustomPathTable[] = {
    "mkgp2_custom_9000.tpl",  // 0x9000
    "mkgp2_custom_9001.tpl",  // 0x9001
    "mkgp2_custom_9002.tpl",  // 0x9002
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

`kmBranch` で 8 本パッチ:

```c
kmBranch(0x80122730, GetOffsetXY_Hook);
kmBranch(0x80122658, GetSizeXY_Hook);
kmBranch(0x801224b4, GetScaleXY_Hook);
kmBranch(0x801223e8, GetFlagsByte_Hook);
kmBranch(0x8012258c, GetChainNextId_Hook);
kmBranch(0x80122808, GetSlotIndex_Hook);
kmBranch(0x80122ac4, GetGroupKey_Hook);
kmBranch(0x801229c4, GetFilePathPtr_Hook);
```

これら 8 つがカバーする getter family は vanilla 側で
`ResourceEntry *` を引いて 1 フィールドだけ返す形に統一されており、hook も
8 つすべてを差し替えないと「flags は custom だけど size は vanilla」のような
不整合になる。

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

## 新しいスロット種別を追加する手順 (例: ラウンド選択画面のサムネ)

1. vanilla 側で「どの resource ID をどの groupKey 経由で読んでいるか」を
   `tools/extract_yoshi_round_assets.py` 形式で特定
2. `gen_custom_assets_header.py` の `ASSET_SLOTS` に行を追加:
   ```python
   ("course1_thumb_road", 0x19E0, (128.0, 160.0), 5, False),
   ```
   `cup_indexed=False` にするのは、サムネはカップ内固定 ID を使うため
3. `cups.yaml` の `assets:` ブロックに対応キーを追加
4. PNG 配置 + ビルド

注: g_cupId のゲーティングは binding が決まった時点で `cup_id` で行われる
ので、ラウンド選択画面で正しく発火するには g_cupId が遷移先でも 17 のまま
保たれている必要がある。ラウンド選択画面では cup_page3 hook の対象外になる
場合があり、その場合は別途 `clFlowRound_Init` (`0x801caf34`) hook で
g_cupId を維持する必要がある (将来課題)。

---

## ハマりやすい所

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

per-frame UV refresh パスは slot miss 時に slot 作成しない (vanilla の
`SetResource` / `FUN_8011fb2c` パスのみが `PreloadResource` をトリガする)。
そのため custom resource を初めてバインドした瞬間に slot が作られないと、
1 frame だけ vanilla アセットがフラッシュする。

`custom_assets.cpp` の `TryPreloadCustomAssetsAtCup17()` が g_cupId が初めて
17 になったときに、binding 全件を `PreloadResource()` で先回りロードしている。
これがないと 1 frame の vanilla フラッシュが見える。

### `next_id` の chain

vanilla の RGB / alpha ペア (`0x1777` → alpha `0x178B` 等) を完全に再現したい
なら custom 側の `next_id` も chain させる必要があるが、alpha 側の format
(I4 / IA4) を RGBA32 に差し替えると blend が破綻する。MVP では `next_id = -1`
で chain を切って RGBA32 単独完結させるのが安全。

---

## 関連コード参照

- `features/custom_assets/custom_assets.cpp` (実装)
- `features/custom_assets/custom_assets.h` (構造体)
- `features/custom_assets/gen_custom_assets_header.py` (yaml → header + TPL)
- `features/cups.yaml` (データソース)
- `features/cup_page3/cup_page3.cpp` (g_cupId をどこで 17 にするか)
- `tools/extract_yoshi_round_assets.py` (vanilla atlas からのクロップ抽出例)
- `mkgp2docs/mkgp2_resource_asset_system.md` (vanilla 側仕様)
- `tasks/lessons.md` (過去のハマりポイント)
