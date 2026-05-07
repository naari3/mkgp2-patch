# MKGP2 Course Tools  --  Blender Addon

`blender_import_*.py` / `blender_export_*.py` / `blender_import_course_all.py`
をひとつの Blender addon にまとめた wrapper。

* File > Import > MKGP2 ... / File > Export > MKGP2 ... メニュー
* View3D サイドバー (`N` キー) > **MKGP2** タブ
* per-asset operator: HSD scene.json / Collision .bin / Line .bin / Auto .bin
* unified: **Import MKGP2 Full Course** (HSD + collision + line + Auto を一括)

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

- **scene.json** : `hsd_export_for_blender.csx` で吐いた JSON bundle
- **bin directory** : `<prefix>_short.bin` `<prefix>_long.bin` `<prefix>_short_line.bin` ... が並ぶフォルダ (= ISO 展開先)
- **prefix** : 例 `mr_highway`

押すと HSD コース mesh + 衝突 (short/long) + lap path (short/long) + AI path (short F/R/long F/R) が全部 import される。

### Per-asset import

サイドバーまたは `File > Import` から個別に呼べる。複数コースを並べて比較したいときに便利。

### Export

- Collision: `CollisionMesh` object (+ optional `WallSegments`) が scene にある状態で実行
- Line: 編集対象の line root empty (or その下の variant mesh) を select してから実行
- Auto: 編集対象の auto-path mesh を select してから実行

## 開発フロー

1. `tools/blender/blender_import_*.py` などを編集
2. Blender で **"Reload course modules"** クリック
3. 再 import で挙動確認

## 既知の制約

- HSD は **read-only** (現状 Blender → .dat の export 経路なし、HSDRawViewer 経由)
- Line variant の役割整理: lap = slot 6 固定、AI = 0..5 のみ参照 (`~/src/github.com/dolphin-emu/dolphin/mkgp2docs/mkgp2_line_bin_format.md` 参照)
- Collision exporter は `CollisionMesh` / `WallSegments` という固定名のオブジェクトを参照する
