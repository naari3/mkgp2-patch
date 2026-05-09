---
name: mkgp2-edit-vanilla-course
description: "MKGP2 (Mario Kart Arcade GP2) の既存 vanilla コース (MR_highway_short_A.dat 等) を Blender で編集して shipping (Riivolution mod 上書き or test_cup の round に転用) する作業のスキル。mkgp2: bundle 経路 (`Import HSD .dat` -> Blender でメッシュ / マテリアル / テクスチャを編集 -> `Export HSD .dat` -> 同名で書き戻し or 別ファイルに retarget) と、texture bypass vs re-encode の dispatch (`mkgp2_png_hash` + `image.is_dirty` + 元 GX bytes stash) と、vanilla 上書き防止ガードの仕組み。「vanilla コースをちょっとだけいじりたい」「既存の MR_highway を改造したい」「テクスチャ差し替え」「joint hierarchy を rewire したい」「mkgp2:<dat> bundle ってなに」「import-edit-export で何が壊れる / 何が保たれる」等の質問でトリガする。新規コース合成は別スキル `mkgp2-new-course` を見よ。"
---

# MKGP2 Vanilla コース改造スキル

mkgp2-patch リポジトリで **既存の vanilla `.dat` を Blender で編集して shipping する** ための pipeline と、bundle 上の data 責任分担、bypass-vs-reencode の判断基準、Riivolution への搬入経路。

> 前提: `tools/blender/blender_addon_mkgp2_course/` の addon と vendored `hsdraw` PyO3 binding (Windows 同梱、Linux/macOS は別途ビルド) があること。新規コース from-scratch 合成は別スキル `mkgp2-new-course` (vis: 経路) を参照。

---

## TL;DR (最短手順)

vanilla `MR_highway_short_A.dat` をいじって shipping する場合:

1. **Import HSD .dat** (`File > Import > MKGP2 HSD .dat` または Sidebar の HSD button) で `MR_highway_short_A.dat` を直接読む — 中間 scene.json bundle は無い、`hsdraw.export_scene_json` が中で走って 88 textures / 220 materials / 225 meshes / 14 joints が `mkgp2:MR_highway_short_A.dat` という 1 collection に展開される
2. **Blender で編集**:
   - mesh の UV / vertex color / position / 削除 / 追加
   - material slot / Principled BSDF の差し替え (Base Color)
   - Image Editor でテクスチャ pixel 編集 (= dirty 化、= 該当 TObj だけ re-encode 経路)
   - Empty (joint) を re-parent して JObj hierarchy を rewire
   - alias root の add/remove (Sidebar > MKGP2 > Aliases)
3. **Export HSD .dat** (`File > Export > MKGP2 HSD .dat`) で writable な出力先に書き出す
   - **vanilla bin dir への上書きは preference の二重ガードで refuse される**。`Output bin directory` を別途設定するか、Riivolution mod dir (`Load/Riivolution/mkgp2_patch/`) を直接指定
4. **shipping 経路の選択**:
   - **vanilla 上書き** (= 全 cup でその vanilla コースが置換される): Riivolution mod dir の同名ファイルに上書き → Dolphin 起動で即反映
   - **test_cup round に転用** (= vanilla はそのまま、test_cup の特定 round でだけ使う): `features/cup_page3/files/<new_stem>.dat` にコピー → `course_models.yaml` + `cups.yaml` に entry 追加 → `bash build.sh` (= mkgp2-new-course skill のフロー 5-7 と同じ)
5. **`bash build.sh`** を必ず走らせる (asset の cp ループ経由で Riivolution 側に同期、`mkgp2-new-course` 側 pitfall #7 と同じ罠)

---

## アーキテクチャ概要

### bundle が運ぶデータ ("mkgp2:" collection の責任範囲)

`Import HSD .dat` 後、`mkgp2:<dat name>` collection の Custom Properties に下記が stash される:

| key | 内容 | 経路 |
|-----|------|------|
| `mkgp2_source_dat` | 元 .dat の filename (例: `MR_highway_short_A.dat`) | round-trip 識別用 |
| `mkgp2_scene_json` | inline JSON (元 .dat 全構造を hsdraw.export_scene_json が吐いた snapshot) | M3 retired csx fallback、現在は常に inline |
| `mkgp2_joints` | joint tree JSON (`{id, parent, children, trs, ...}` per joint) | export 時に Empty parent chain と diff を取って rewire を反映 |
| `mkgp2_joint_aliases` | `{alias_name: jobj_id}` dict | scene_data 以外の root の管理 (Add/Remove operator で編集) |

各 mesh object には:
- `mkgp2_joint_id`, `mkgp2_single_bind_joint`, `mkgp2_cull`, `mkgp2_source_path`

各 Image (= TObj に bind された texture) には:
- `mkgp2_gx_path` — `<gx_dump_dir>/<sha1[:12]>.gx` (= 元 .dat の生 GX payload バイト)
- `mkgp2_gx_format` ("CMP" / "RGB5A3" / "RGBA8" / "I8" 等)
- `mkgp2_gx_width` / `_height` / `_size`
- `mkgp2_png_hash` — `sha1(png file content)`、importer が `img.save()` した直後の状態

### bypass vs re-encode dispatch (texture)

Export 時、各 TObj について:

```
新 hash = sha1(_png_bytes_for_image(img))   # in-memory PNG re-render
img.is_dirty (= Blender が edit を検出した) and 新 hash != mkgp2_png_hash
  ├─ True  → re-encode 経路: gx_encode(format, width, height, RGBA bytes) → 新 GX bytes
  └─ False → bypass 経路:    open(mkgp2_gx_path, "rb").read() → 元 GX bytes をそのまま採用
```

つまり **触ってない texture は CMP DXT1 round-trip での画質劣化が 0**。`mkgp2_gx_path` が存在しない (= Image が外から差し込まれた) 場合は無条件で re-encode される。

### vanilla 上書き防止 (二重ガード)

1. addon preference の `Vanilla bin directory` で ISO dump dir を登録
2. Export 系 operator (`MKGP2_OT_ExportHSD`, `MKGP2_OT_ExportFullCourse`, `MKGP2_OT_ExportCourse`) は execute() 入口で `_refuse_if_vanilla(filepath)` を通す
3. `_refuse_if_vanilla` が `Path(filepath).is_relative_to(vanilla_dir)` を見て True なら ERROR + CANCELLED

→ これによって accidentally vanilla を上書きできない。`Output bin directory` (preference) を別途 writable な場所に向け、operator はそちらに dispatch する。

### .dat の round-trip 不変条件

bundle 経路の export は **deterministic**:
- 同じ bundle を 2 回連続で export すると **byte-identical**
- `M3c` 検証経路: `test_addon_hsd_export.py` の v0/v1 sha1 一致テスト
- texture を 1 つだけ dirty 化して export すると、その TObj の payload offset から先のバイトだけが変わる (該当 mesh 領域は触らない、他 87 texture は bypass)

### Export operator の dispatcher (`mkgp2:` か `vis:` か)

`MKGP2_OT_ExportHSD` (= `File > Export > MKGP2 HSD .dat`) の execute() は、active layer collection を見て下記のいずれかに routing する:

| active collection | 経路 | 使うファイル |
|------|------|------|
| `mkgp2:<dat>` (= 本 skill 対象) | `_export_mkgp2_bundle.export_bundle_to_dat` | bundle 内 stash (`mkgp2_scene_json` inline + `mkgp2_joints` + `mkgp2_joint_aliases`) を再構築 |
| `vis:<name>` (= 新規コース、別 skill `mkgp2-new-course`) | `_promote_vis_to_hsd.promote_vis_to_dat` | mesh + Principled BSDF を from-scratch で promote |

**両方が reachable な場合は bundle (`mkgp2:`) 優先**。エディタで vis: 系と mkgp2: 系を同居させて編集中に export すると、active layer collection の選び方で writer が変わるので、export 直前に Outliner で目的の collection をクリックして active にしておく。

### bundle に新規 mesh / material を追加した場合の境界

`bundle.objects` を top-level walk するので、bundle collection 直下に Blender Mesh を追加すれば export 時に拾われる (`tools/test_addon_bundle_add_mesh.py` で検証済):

| 追加した mesh の種類 | Export 結果 |
|------|------|
| 既存 material slot を再利用 + 既存 joint_id を `mkgp2_joint_id` にセット | ✓ そのまま追加される (`stats.meshes` += 1) |
| 既存 material 再利用 + `mkgp2_joint_id` が `jobj_by_id` map に無い | ✗ skip + WARN (= mesh は出力されない) |
| 新規 Principled BSDF material を使う (BSDF Base Color が単色) | ✓ mesh + material 両方拾われる。BSDF Base Color から `(r,g,b,a)` byte を抽出して `_blender_material.make_textured_mobj` 経由で 4x4 RGBA8 fallback texture を合成して `CONSTANT|TEX0|ALPHA_MAT` (= 0x2011) MObj を build (`stats.fresh_materials` += 1)。INFO log に `built ad-hoc MObj from BSDF (color=..., img=solid 4x4)` |
| 新規 Principled BSDF material を使う (BSDF Base Color に Image Texture node を繋ぐ) | ✓ そのテクスチャが `bm.bsdf_image_texture` 経由で `(w, h, RGBA bytes)` として抽出され、`gx_encode(format=6 RGBA8, w, h, raw)` → TObj-attached Image として .dat に round-trip。`test_addon_bundle_add_mesh.py:v4` で 8x8 全 pixel orange を encode→decode 一致確認済 (commit `f1afeda`) |

つまり「既存 vanilla を活かして mesh + 単色 material をちょい足し」も「Blender でロードした任意の画像をテクスチャとして貼った mesh をちょい足し」も両方完全動作する (`test_addon_bundle_add_mesh.py` で v3/v4 case 検証済)。

**format 選択 (Material EnumProperty)**:

Sidebar > MKGP2 > **Texture format** sub-panel (active object に active material があれば表示される) で、Material per に GX texture format を 3 択から選べる:

| 選択肢 | byte/pixel | 特徴 |
|---|---|---|
| **RGBA8** (default) | 4.0 | lossless、byte-equiv 保証 |
| **CMP** (DXT1) | 0.5 | ~8x compact、lossy quantize、4x4 tile alignment 必須 (= 4 の倍数でない `(w, h)` は silent fallback で RGBA8 化) |
| **RGB5A3** | 2.0 | ~4x compact、16-bit quantize、alpha 1bit + 4bit |

実体は `bpy.types.Material.mkgp2_target_format` (EnumProperty)、`.blend` ファイルに保存される。Material level で持っているので、同じ Material を共有する複数 mesh は同じ format になる。

**bypass dispatch との関係**:
- vanilla `.dat` から import した既存 Material は **bypass 経路** (= `mkgp2_gx_path` の生 GX バイトをそのまま採用) を取るので、`mkgp2_target_format` を変えても無視される。元の format (CMP / RGB5A3 / etc. のまま) が保たれる
- format prop が効くのは **fresh material 経路** (vis: 経路 + bundle 内 hand-added mesh + 新規追加 Material) のみ

**残る制約**:
- vanilla CMP image を**別 format に再エンコードしたい場合**は、Image Editor で 1 pixel touch して dirty 化 → reencode 経路に回す (現状は同 format で再エンコード、format 変更は未対応)
- Image Texture の解像度は `bpy.data.images.size` から取る (任意)。CMP の 4 の倍数制約以外、上限はない (ただし `gx_encode` 内 alignment は要検証、特殊な解像度はリスク)
- BSDF Base Color と Image Texture の両方を同時 active にした場合、Image Texture 優先 (= `bsdf_image_texture` の戻り値が non-None なら `make_textured_mobj` の `img_tuple` がそれになる)、単色 fallback は使われない

vis: 経路 (新規コース合成、`mkgp2-new-course` skill) との material 構築ロジックは **共有 helper `_blender_material.py` 経由で同一**。新材の振る舞いを変えたいときはこのモジュール 1 箇所で済む。

### Empty による joint hierarchy rewire

import 時、joint 1 つにつき 1 Empty が `bundle.objects` に link される (`mkgp2_jobj_id` custom prop で識別)。Blender で Empty を re-parent すると、export 時に exporter がその chain を walk して `mkgp2_joints` JSON の `parent` / `children` field を上書きしてから writer に渡す。

Empty を全部消した bundle (= older import / 手動削除) の場合は stash の `mkgp2_joints` を素通しする (= rewire 機能が effectively disable)。

---

## 各操作の「やる / やらない」ガイド

### やっていい編集

- mesh vertex の position / normal / UV / vertex color の上書き
- mesh polygon の追加 / 削除 (DObj はそのまま、POBJ DL を re-build)
- material slot の Principled BSDF Base Color 変更
- Image Editor でのテクスチャ pixel 編集 (フォーマットは保持)
- Empty (joint) の re-parent / TRS 編集
- alias root の add / remove (`Sidebar > MKGP2 > Aliases`)

### やらない方が無難な編集

- texture format を変えたい (例: CMP → RGB5A3): 現在の bypass dispatch は「format は不変」を前提。dirty 化しても writer 側で format を見て encode するが、CMP→RGB5A3 はファイルサイズが激増する可能性あり、注意
- texture 解像度の変更: gx_width / gx_height は import 時に固定。寸法を変えるなら Image Editor で resize 後 dirty 化、export 時に該当 TObj のヘッダも更新される。CMP の場合は w/h が 4 の倍数必須
- 完全に新しい material を Principled 以外で作る: BSDF Image Texture node + Base Color のみが認識される

### やってはいけないこと

- `mkgp2_source_dat` / `mkgp2_scene_json` の手動上書き (round-trip 整合性が壊れる)
- `mkgp2_gx_path` の手動編集 (= bypass 経路の元バイトを差し替えるなら、`mkgp2_png_hash` と整合性を取らないと再 import で破綻)
- vanilla bin dir に直接 export (= 二重ガードで refuse されるが、ガードを bypass する裏口を作らない)

---

## shipping 経路

### A) vanilla を直接置換 (= Riivolution の per-file replace)

最も単純。**ただし全 cup / 全 round で当該コースが影響を受ける**。

1. Export 先: `C:\Users\naari\Documents\Dolphin Emulator\Load\Riivolution\mkgp2_patch\<original_name>.dat`
2. Riivolution XML はすでに `<file disc="..." external="..."/>` で全コース mapping 済み (= patch_map.md 参照)
3. Dolphin 起動で即反映

repo へのチェックインはしない (vanilla 直 mod は user-local 用途)。

### B) test_cup の round に転用 (= 新規 round として刺す)

vanilla を活かしつつ、自分の round からだけ使う。`mkgp2-new-course` skill の手順 4-7 と概ね同じだが、collision/line/auto は **vanilla をそのまま流用するか** / **自前で書き直すか** で手順が変わる:

1. Export 先: `<repo>\features\cup_page3\files\<new_stem>.dat` (任意の新名 = `cups.yaml` で参照する `course_model` に相当)
2. `course_models.yaml` に `<new_id>: file: <new_stem>.dat, joints: []` を追加
3. `cups.yaml` の test_cup round[N]:
   - `course_model: <new_id>`
   - **collision/line/auto を流用する場合**: `collision: mr_highway_short.bin`, `line_bin: mr_highway_short_line.bin` のように vanilla filename をそのまま書く。これらは Riivolution 経由で vanilla 側から lookup される (= cp 不要、ISO dump dir に既に居る)
   - **自前で書き直す場合**: `<new_stem>.bin` / `<new_stem>_line.bin` を addon の Collision/Line export operator で出力 → `features/cup_page3/files/` に置く → cups.yaml にその名前を書く
   - `bgm_l` / `bgm_r` も同様、vanilla DSP filename をそのまま書けば lookup される
   - `start_positions` は HSD .dat の座標系に合わせて指定 (vanilla を流用するなら省略可、cup_page3 が cupId=0 alias で MR_highway start を返す)
4. `bash build.sh` (= `features/cup_page3/files/*` の cp ループが必須、詳細は `mkgp2-new-course` の pitfall #7)

要点: `cups.yaml` に書く `collision` / `line_bin` / `bgm_*` は **filename だけで OK**、ファイル本体は repo 側 `features/cup_page3/files/` に置いた場合だけ build.sh で cp される。書かなければ vanilla 側 (Riivolution の元) が読まれる。

### C) リポジトリの開発資産として保管

`.dat` は重い (300KB-3MB)。git で管理するのは Riivolution 配信 path にそのまま乗るもの (= `features/*/files/*`) のみ。Blender `.blend` は user-local が原則。共有したいなら別 repo or LFS。

---

## ハマりポイント (実例)

### 1. Export したのに Dolphin で変化が見えない

**原因**: `bash build.sh` を走らせていない (= Riivolution mod dir に cp されていない、もしくは vanilla 上書き経路 A で書き出し先を誤った)。

**確認**:
```bash
ls -la <repo>/features/cup_page3/files/<stem>.dat \
       "C:/Users/naari/Documents/Dolphin Emulator/Load/Riivolution/mkgp2_patch/<stem>.dat"
```
size と mtime を比較して、Riivolution 側が古ければ build.sh 漏れ。

詳細は `mkgp2-new-course` skill の pitfall #7。

### 2. `bypass=88, reencode=0` のはずなのに 1 つだけ reencode 出る

その 1 つは Blender が `is_dirty=True` を立てている。原因:

- 自動マテリアル node 接続 / Color management 設定変更で pixels.foreach_set が走った
- Image Editor を「開いた」だけでも内部で touch されることがある (Blender ver 依存)
- import 中に色空間設定 (sRGB↔Non-Color) を切り替えた

→ 害は無い (= 該当 TObj が再エンコードされるだけ、CMP なら ε 単位で元 GX と異なる) が、deterministic test を壊すので意図しない場合は import 直後の状態で touch しない。

### 3. Export 時に "vanilla bin directory: refuse to write" エラー

意図通りのガード。`Output bin directory` preference を Riivolution mod dir に向けるか、export dialog でそれ以外の場所を指定する。

### 4. alias を追加したつもりが .dat に出ない

`Sidebar > MKGP2 > Aliases > Add` で `mkgp2_joint_aliases` JSON は更新される (= Blender 側 stash には乗る) が、export 時に writer が新 JObj を見つけられないと無視される。Add 時の `target_id` (= `jobj_<N>`) が bundle の Empty に存在することを確認。

alias rewrite を使う具体的シーン:
- joint_extend の `CourseJointLoadHook` が name-resolve する 18 slot (`MR_highway_*_joint`) のうち 1 つの target を別 mesh にすげ替えたい (= 元 mesh を活かしたまま挙動だけ変える)
- 自前の round-select / mini-map 描画コードが特定 alias で root JObj を引いている場合の rename
- `joint_extend/course_joints.yaml` で per-cup 18 slot を別名定義した場合に、新 alias を bundle にも登録しないと resolve が NULL になる

### 5. Import 直後に bypass されるはずの texture が reencode される

`MKGP2_OT_ImportHSD` は import 直後に `img.save()` で PNG を disk に書き、その bytes の sha1 を `mkgp2_png_hash` に入れる。`_png_bytes_for_image` が export 時に **同じ Image を PNG re-render** した場合に hash が一致しないと dirty 認定。原因:

- Blender ver が違う (PNG encoder が non-deterministic)
- import 後に Image の color space を触った
- import-export 間で Blender 再起動 → memory 上の Image が消えて disk から read-back される (filepath_raw を見失った可能性)

→ `tex_reencode` カウントが 0 でないなら、上記いずれかが起きていないか確認。1-2 個ならテスト fail 程度の影響、全部なら importer か addon のバグ可能性が高い。

### 6. Blender 起動中に hsdraw.pyd を refresh しようとしたら locked

Blender が `import hsdraw` でロード中の `.pyd` は OS がロックする。upstream hsdraw を更新したいときは:

1. Blender を全部閉じる
2. `vendor/windows_x86_64/hsdraw/hsdraw.pyd` を新しいビルドで上書き
3. Blender 再起動
4. (どうしても閉じられない場合) `.locked-by-blender` 接尾辞でリネームして次回起動時にクリーンアップ

詳細は `vendor/README.md`。

---

## 関連ファイル / docs

| パス | 内容 |
|------|------|
| `tools/blender/blender_import_hsd.py` | Python-only `.dat` 直接 import (`import_dat_directly`、`make_image_from_gx`、`_collect_gx_bytes`) |
| `tools/blender/blender_addon_mkgp2_course/__init__.py` | `MKGP2_OT_ImportHSD` / `MKGP2_OT_ExportHSD` operator + Sidebar UI |
| `tools/blender/blender_addon_mkgp2_course/_export_mkgp2_bundle.py` | bundle -> .dat round-trip writer (alias rewrite, joint rewire, texture bypass dispatch) |
| `tools/test_addon_hsd_export.py` | unified Export HSD operator の round-trip + bypass + determinism + 上書きガード回帰 |
| `tools/test_addon_hsd_import_props.py` | Image の `mkgp2_gx_*` / `mkgp2_png_hash` stash 整合性 |
| `tools/test_addon_hsd_alias_edit.py` | alias add/remove operator の挙動 |
| `tools/test_addon_hsd_joint_empties.py` | Empty re-parent → JSON rewire の round-trip |
| `tools/test_addon_export_mkgp2_bundle.py` | export_bundle_to_dat 単体の smoke test |
| `tools/test_addon_bundle_add_mesh.py` | 新規 mesh 追加・新規 material・bogus joint_id の境界を `stats.meshes` で検証 (上記「bundle に新規 mesh / material を追加した場合の境界」表の根拠) |
| `tools/test_addon_vanilla_safety.py` | 上書きガード 8 phase test |
| `~/src/github.com/dolphin-emu/dolphin/mkgp2docs/hsd_to_blender_visual_pipeline.md` | HSD ↔ Blender 視覚 pipeline 解析 (channel order quirk、Emission shader 経路、Material.Blending の罠) |

---

## デバッグ Tips

### export stats を見る

`Export HSD .dat` の operator は出力末尾に:
```
textures: 88 unique  (bypass=88, reencode=0)
materials: 220 MObjs built
meshes  : built=225  skipped=0  verts=62899  tris=50647
wrote   : <stem>.dat  size=3818272
```

`bypass + reencode == unique` を確認。`reencode > 0` なら意図した編集だけかチェック (上記ハマり 2 / 5)。

### .dat の中身 (round-trip 後の検証)

```bash
dotnet-script tools/hsd/hsd_dump.csx -- <new>.dat | head -40
dotnet-script tools/hsd/hsd_dump_jobjdescs.csx -- <new>.dat
```

vanilla との diff (joint flags、material RenderFlags、POBJ flags、TObj count) は:
```bash
dotnet-script tools/hsd/hsd_compare_root_jobj.csx -- <vanilla>.dat <new>.dat
```

### byte-equiv determinism check

```bash
"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe" \
  --background --python tools/test_addon_hsd_export.py
```

`v0 == v1 byte-equiv on un-edited bundle` で writer が deterministic か確認。
