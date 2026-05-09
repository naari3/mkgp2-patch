# M1-M3: vanilla 不要 HSD export 統合計画

## ゴール

`MKGP2_OT_ExportHSD` を vis: / mkgp2: 両対応の単一 operator に統一し、
vanilla `.dat` 借用なしでメッシュ・テクスチャ編集を完全反映する経路を作る。
hsdraw 側 H1-H4 (commit `b437907` で vendor 完了) で必要 API は揃った。

## 主要設計

### bundle 拡張 (self-contained)

各 texture について、PNG (人間用) と並べて `tex/<id>.gx` (raw GX bytes、機械用) を保管。
texture id は既存どおり `SHA-1(raw GX bytes)`。

scene.json の `textures[]` には新フィールド:
- `gx_file: "tex/<id>.gx"`
- `gx_size: <int>`

### bypass 判定 (PNG edit detection)

Blender Image に import 時の PNG content hash を stash:
- `image["mkgp2_png_hash"]`: import 時の PNG SHA-1 (現状の PNG file content)
- `image["mkgp2_gx_path"]`: bundle の `.gx` 絶対パス
- `image["mkgp2_gx_format"]`: 元 GX format ("CMP" / "RGB5A3" / "RGBA8" / "RGB565")
- `image["mkgp2_gx_width"]`, `image["mkgp2_gx_height"]`: 元寸法

export 時に Blender Image の現在 PNG hash を計算 → import 時 hash と比較:
- 一致 → `.gx` を read → `Image.set_image_data_bytes(...)` (vanilla bit-equivalent)
- 不一致 → Blender Image pixels (RGBA8) を取り出し → `hsdraw.gx_encode(format_int, w, h, rgba)` → 同 setter
- 寸法変更は warning (再エンコード経路で format 変更不可)

### 統一 operator

`MKGP2_OT_ExportHSD` (bl_idname `export_scene.mkgp2_hsd_json`) を内部 dispatcher に書き換え:
- context resolver は vis: / mkgp2: 両方を解決
- 分岐:
  - vis: → `_promote_vis_to_hsd.promote_vis_to_dat` を vanilla 不要 mode で呼ぶ
  - mkgp2: → 新しい `_export_mkgp2_bundle.py` (M3 で新設) で再合成
- 既存の `MKGP2_OT_PromoteVisToHSD` は廃止 (operator + UI ボタン削除)
- UI: サイドバーの "Promote vis: → HSD .dat" を消し、"Export HSD bundle (.dat)" 1 本に集約

## 作業順

### M1: csx export 拡張 [0.5 日]

`tools/hsd/hsd_export_for_blender.csx`:
- [ ] `InternTexture` で PNG 保存と並んで `tex/<sha>.gx` に raw GX bytes を書き出す
  (`img.ImageData` バイト列をそのまま `File.WriteAllBytes`)
- [ ] `TextureDto` に `gx_file` と `gx_size` を追加、scene.json に乗る
- [ ] 既存 csx 出力との下位互換: 古い scene.json (gx_file 欠如) でも import が壊れないこと
- [ ] mr_highway を実際に再 export して bundle に `.gx` が並ぶことを確認

### M2: import_hsd.py 拡張 [0.5 日]

`tools/blender/blender_import_hsd.py`:
- [ ] `make_image()` 内で PNG content の SHA-1 を計算して `image["mkgp2_png_hash"]` に
- [ ] scene.json から `gx_file` / `gx_size` / format / w / h を読んで Image custom prop に保存
- [ ] 古い bundle (`gx_file` なし) は warning だけ出して legacy mode で続行
  (export 時に再エンコード強制になるが import 自体は通す)
- [ ] 既存 mkgp2: bundle を再 import して prop が乗ることを確認 (test_addon_hsd_import 系で smoke)

### M3: 統一 export operator [0.5-1 日]

#### M3a: 内部関数 `_export_mkgp2_bundle.py` 新設

[ ] hsdraw API を直接叩いて bundle 全体を書く新モジュール:
  - `Dat.alloc_scene_data()` から開始
  - bundle の stashed `mkgp2_joints` から JObj 階層を構築 (TRS / flags 反映)
  - 各 mesh (`mkgp2_joint_id` で索引) を MeshBuilder で POBJ 化
  - mesh の material から MObj 構築:
    - `material.diffuse_rgba` を Material に流す
    - texture chain は scene.json の materials[].textures から再構築
    - 各 TObj は wrap / filter / blending / color_op / alpha_op を property setter で
    - 各 Image は bypass 判定 → `set_image_data_bytes` で attach
  - alias root を `add_root` で全部 attach
  - `dat.write()`

[ ] vis: 経路 (`_promote_vis_to_hsd.py`) も同じ helper を共有できる範囲で refactor:
  - 共通: MObj 構築、TObj 構築、Image 構築 → `_hsd_build.py` に抽出
  - vis: 専用部分: 単一 alias root だけ作る

#### M3b: operator の統合

[ ] `MKGP2_OT_ExportHSD.execute` を dispatcher に
[ ] `MKGP2_OT_PromoteVisToHSD` を廃止 (operator class + CLASSES + UI 削除)
[ ] サイドバーの該当ボタンを 1 本に
[ ] vanilla bin dir 解決のフォールバック (古い structural-only 経路) は削除
   → vanilla `.dat` への参照が完全に消える

#### M3c: regression test

[ ] `tools/test_addon_hsd_export.py` を拡張: 再 import → 再 export で
   - mesh 数 / 頂点数 / 面数が一致
   - texture が一致 (bypass 経路) → byte-equiv の .dat が出る
- vanilla MR_highway を再 export → 視覚的に同じ (Blender 上の re-import)
- vis:my_course の test (既存 `test_addon_promote_vis_to_hsd.py`) を統合 operator 経由でも通す

## 完了判定

- [ ] vanilla `.dat` への file IO 参照が addon の execute path に残らない (preference は import 用に残す)
- [ ] mr_highway を再 export → bypass で byte-equiv の .dat が出る (CMP 再エンコード起きない)
- [ ] mr_highway の 1 mesh を Blender で動かして再 export → その mesh だけ反映、他 byte-equiv
- [ ] mr_highway の 1 texture を編集して再 export → encoder 経由、他 bypass、視覚劣化最小
- [ ] vis:my_course も同じ operator 1 つで export できる

## 次セッション持ち越し候補

- texture 寸法変更 (Blender 上で resize) のサポート (現状 reject)
- 新規 texture (Blender で新しい Image を貼る) のサポート (M3 では既存 binding に紐づくものに限定)
- normal map / specular map / emission map (texture 1 枚のみが現実的、複層は MOB 既存どおり)
