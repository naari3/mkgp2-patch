# MKGP2 Course Tools  --  Blender Addon

`blender_import_*.py` / `blender_export_*.py` / `blender_import_course_all.py`
をひとつの Blender addon にまとめた wrapper。

* File > Import > MKGP2 ... / File > Export > MKGP2 ... メニュー
* View3D サイドバー (`N` キー) > **MKGP2** タブ
* per-asset operator: HSD `.dat` / Collision .bin / Line .bin / Auto .bin
* unified: **Import / Export MKGP2 Full Course** (HSD + collision + line + Auto を一括)
* HSD は **read-write** (`hsdraw` PyO3 binding 経由、`.dat` を直接 import + export)

addon は parser/exporter コードを持たず、`mkgp2-patch/tools/blender/` の
standalone スクリプトに dispatch する thin wrapper。スクリプトを編集したら
**"Reload course modules" ボタンで hot-reload** できる。

## install

### A) addon が `mkgp2-patch/tools/blender/` の中にある場合 (このリポジトリの推奨配置)

1. **Blender の standard addon ディレクトリにシンボリックリンクを張る**:
   - Windows (cmd, 管理者で実行):
     ```
     mklink /D "%APPDATA%\Blender Foundation\Blender\<ver>\scripts\addons\mkgp2_course" "<repo>\tools\blender\blender_addon_mkgp2_course"
     ```
   - PowerShell:
     ```powershell
     New-Item -ItemType SymbolicLink `
       -Path "$env:APPDATA\Blender Foundation\Blender\<ver>\scripts\addons\mkgp2_course" `
       -Target "<repo>\tools\blender\blender_addon_mkgp2_course"
     ```
   - macOS / Linux: `ln -s <repo>/tools/blender/blender_addon_mkgp2_course ~/.config/blender/<ver>/scripts/addons/mkgp2_course`
2. `Edit > Preferences > Add-ons > MKGP2 Course Tools` を有効化
3. `Source modules directory` の preference は **空のままで OK** (= addon の親 = `tools/blender/` が自動解決される)

### B) zip install (`tools/blender/` の外にコピーする場合)

1. `blender_addon_mkgp2_course/` フォルダを zip 化
2. `Edit > Preferences > Add-ons > Install...` から zip を選択
3. 有効化後、addon preferences の `Source modules directory` に
   `<repo>/tools/blender` を入力

どちらでも内部的には `sys.path.insert(0, source_modules_path)` してから
`importlib.import_module("blender_import_hsd")` 等が走る。

## 使い方

### Full Course import

`File > Import > MKGP2 Full Course` または サイドバーの 「HSD + col + line + auto」 ボタン。

ダイアログで:

- **bin directory** : `<prefix>_short.bin` `<prefix>_long.bin` `<prefix>_short_line.bin` ... と `<Prefix>_short_A.dat` / `<Prefix>_long_A.dat` が並ぶフォルダ (= ISO 展開先)
- **prefix** : 例 `mr_highway` (大文字小文字を問わず `<bin_dir>/<Prefix>_<round>_A.dat` を auto-discover)

押すと HSD コース mesh (`hsdraw` 直読) + 衝突 (short/long) + lap path (short/long) + AI path (short F/R/long F/R) が全部 import される。

### Per-asset import

サイドバーまたは `File > Import` から個別に呼べる。複数コースを並べて比較したいときに便利。

### Export

- HSD: import 済み `mkgp2:<dat>` collection を Active にして
  `File > Export > MKGP2 HSD .dat` (または `vis:<name>` を Active にすると
  fresh course .dat の合成 — vanilla `.dat` は不要)
- Collision: `CollisionMesh` object (+ optional `WallSegments`) が scene にある状態で実行
- Line: 編集対象の line root empty (or その下の variant mesh) を select してから実行
- Auto: 編集対象の auto-path mesh を select してから実行
- Full Course: `File > Export > MKGP2 Full Course` で 8 つの .bin
  (collision + line + auto F/R × short/long) を一括出力

vanilla bin dir (Riivolution の元 ISO dump 先) への上書きは preference
で別途 *Vanilla bin directory* を設定すれば二重ガードで refuse される。

## 開発フロー

1. `tools/blender/blender_import_*.py` などを編集
2. Blender で **"Reload course modules"** クリック
3. 再 import で挙動確認

## マテリアルテクスチャの Blender preview (任意)

`vis:` 経路は **Principled BSDF の Base Color 単色**でも export 時に 4×4 fallback
texture を `_promote_vis_to_hsd._make_textured_mobj` が自動合成するので、
何もしなくても in-game では色が出る。ただし **Blender viewport 上では BSDF Base Color
のみ表示** で、in-game に焼かれる texture そのものは見えない。

viewport で「焼かれる texture を直接見たい」場合は、`_bake_vis_textures.py` の
`bake_vis_collection_materials(vis_collection, log_fn=print)` を呼ぶと
全 material に 4×4 単色 PNG を Image Texture node として attach する (= viewport
shading で texture preview 可能)。現状は **CLI / headless 経由のみ** で
Sidebar button は未実装 (`tools/_blender_headless_promote.py` で promote 前に
自動 bake する経路あり)。手動 bake が必要なら Blender python console で:

```python
import sys; sys.path.insert(0, r"<repo>/tools/blender/blender_addon_mkgp2_course")
import _bake_vis_textures as b, bpy
b.bake_vis_collection_materials(bpy.data.collections["vis:my_course"], log_fn=print)
```

## 既知の制約

- HSD pipeline は vendored `hsdraw` PyO3 binding に依存。Windows 用 `.pyd` は
  リポジトリに同梱だが、Linux/macOS 用は別途ビルド必要 (`vendor/README.md` 参照)
- Line variant の役割整理: lap = slot 6 固定、AI = 0..5 のみ参照 (`~/src/github.com/dolphin-emu/dolphin/mkgp2docs/mkgp2_line_bin_format.md` 参照)
- Collision exporter は `CollisionMesh` / `WallSegments` という固定名のオブジェクトを参照する
- `vis:<name>` collection 経由の HSD 合成 (新規コース用) は textured な
  primary mesh は POS+NRM 必須、textureless の OPA mesh も POS+NRM (CLR0+LIGHTING
  抜きは TEV register 漏れで camera 移動依存にフェードする)
