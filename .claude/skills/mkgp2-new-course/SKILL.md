---
name: mkgp2-new-course
description: "MKGP2 (Mario Kart Arcade GP2) custom コース 1 round を新規追加する作業 (mesh / collision / line / start position / cup-tile asset / round 配線) と、ロード経路 (cups.yaml -> course_models.yaml -> cup_page3 hooks -> joint_extend no-op -> render pass scan) のスキル。Blender vis: 経路で from-scratch .dat を吐き、test_cup (cupId=17) に round として刺すまでの最短手順。「新コース追加」「Blender でコースを作って読ませる」「round.course_model / start_positions の書き方」「joint_extend は何をしている」等の質問でトリガする。"
---

# MKGP2 Custom コース新規追加スキル

mkgp2-patch リポジトリで **新しい race-able round を 1 つ追加する** ための、データソース → ビルド → ロードの流れと現状の責任分担。

> 前提: feature `cup_page3` (test_cup, cupId=17) と `joint_extend` (CourseScene_Load hook) と `custom_assets` (sprite/asset re-routing) は既に組み込み済み。**コース追加は test_cup の round[N] を埋めるだけ**で、新 cup を増やしたい場合のみ別作業。

---

## TL;DR (最短手順)

新規コース `my_course` を test_cup の round 3 に刺す:

1. **Blender** でコース mesh を作る (`vis:my_course` Collection に普通の Principled BSDF マテリアルで配置)
2. **コリジョン / ライン** も同 Collection に配置 (`<stem>_collision_a` / `<stem>_collision_b` mesh + `<stem>_line` Empty + 7 variant)
3. **Full Course Export** (`File > Export > MKGP2 Full Course (HSD + collision + line + auto)` または Sidebar の Full Course operator) で `my_course.dat` / `my_course.bin` / `my_course_line.bin` を出力先に書き出し
4. **Riivolution 配置先**: `features/cup_page3/files/` に 3 ファイルを置く (build.sh が ISO root として配信、Triforce DVD `<file create="true">` 経由でゲームから lookup される)
5. **`features/course_models.yaml`** にエントリを 1 つ追加:
   ```yaml
   my_course:
     file: my_course.dat
     joints: []         # 空のままで良い (現状未参照 dead field)
   ```
6. **`features/cups.yaml`** で test_cup の round[N] に bind:
   ```yaml
   - id: round3
     course_model: my_course
     collision: my_course.bin
     line_bin:  my_course_line.bin
     laps: 3
     time: 120.0
     bonus: 15.0
     bgm_l: bgm01_demoL.dsp
     bgm_r: bgm01_demoR.dsp
     thumb:      images/test_cup_course3_thumb.png
     thumb_road: images/test_cup_course3_thumb_road.png
     ai_lap_bonus_rules: *shared_ai_lap_bonus_rules
     base_speed:         *shared_base_speed
     start_positions:                # HSD world 座標 (X, Y, Z)、最大 8 個
       - [1400.0, 0.5, -150.0]
       ... 8 行 ...
   ```
7. **`bash build.sh`** → patch bin / xml が更新される、**かつ** `features/*/files/*` が `Load/Riivolution/mkgp2_patch/` に cp される (← 後者が肝、後述 pitfall #7)
8. **Dolphin 起動 → test_cup → round 3 選択** で走れる

ヒット率の高いハマりは下記「ロード経路と責任分担」を参照。

> **重要**: asset (`.dat` / `.bin` / `.tpl` / `.png`) だけを書き換えた場合でも build.sh を **必ず**実行する。Dolphin が読むのは Riivolution mod dir 側のコピーで、build.sh の末尾 cp ループでしか同期されない。ソースを 1 行も変えていなくても asset を更新したら build.sh。

---

## アーキテクチャ概要

### データ責任のレイヤー

```
features/cups.yaml              cup-level (test_cup) と round-level (round1..3) の binding
                                cup_id=17, display_alias_cup=7 (= Yoshi UI 借用)
                                round[i] -> course_model 参照, collision/line bin 名,
                                          laps/time/bonus/bgm, thumb*, AI table,
                                          start_positions[8][3]
        |
        v 参照
features/course_models.yaml     course geometry の <id> -> .dat ファイル名 + joints
                                joints: [] は「現状未参照」(後述)
        |
        v ファイル
features/cup_page3/files/<id>.dat    HSD .dat (Blender vis: から from-scratch 合成)
features/cup_page3/files/<id>.bin    collision .bin (Blender collision mesh から)
features/cup_page3/files/<id>_line.bin  line .bin (Blender line Empty hierarchy から)
features/custom_assets/images/test_cup_course3_thumb*.png  cup-tile/round-tile 画像
```

### コード責任 (実機側)

| 機能 | feature | hook | 役割 |
|------|---------|------|------|
| course .dat ファイル名解決 | `cup_page3` | `GetCourseModelFilenameHook` (0x8009c418) | custom cup なら `round->courseModelFile` を返す |
| road .dat 2nd getter | `cup_page3` | `GetRoadDatFilenameAltHook` (0x8009c5d0) | 同上 (HSDArchive loader 用、ハング防止に必須) |
| 18-slot joint name table | `cup_page3` | `GetJointNameTableHook` (0x8009c57c) | custom cup を `cupId=0` (MR_highway) にフォールバック |
| 18-slot joint resolve + Show/Hide | `joint_extend` | `CourseJointLoadHook` (0x80047bb0) | nameTable[0..17] を ResolveJointByName で archive から引いて state[0..0x12] に保存、long/short/normal/reverse で Show/Hide 分岐、加えて YAML `course_joints.yaml` の per-cup 追加 joint も処理 |
| start position | `cup_page3` | `GetStartPositionHook` (0x8009c688) | custom round なら `round->startPositions[slot]` を返す |
| cup-tile / thumb / banner asset | `custom_assets` | resource-table getter 6 hook + ResourceSlot_Load_BranchHook | 4000 番台 ID を自前 sprite に routing |
| round-select alias swap | `round_select` | clFlowRound_Init/Update Pre/PostHook | g_cupId 17 ↔ 7 を一時 swap して vanilla テーブル OOB 回避 |

### ロード経路 (race 開始時の dispatch)

```
[round 選択]                                                       (round_select.cpp)
   round_select PreInit が thumbnail injection
       g_cupId = 7 (Yoshi alias)、g_customCupScope = 17 を保存
       PreDtor で復元

[クラス選択 → Course Scene 入場]
   (game)
   GetCourseModelFilename -> hook -> round->courseModelFile = "my_course.dat"
   FileLoader_LoadBin("my_course.dat") -> Riivolution lookup
   HSDArchive_Load                                  (HSD parser)
        |
        v scene_data.JOBJDescs[0].RootJoint を取得
   CourseScene_Load (0x80047bb0) -> CourseJointLoadHook
        nameTable = GetJointNameTable() -> hook -> MR_highway 18 slot
        for i in 0..17: state[i] = ResolveJointByName(archive, nameTable[i])
            -> my_course.dat に "MR_highway_*_joint" は無い -> state[i] = 0
        Show/Hide 分岐は state[i] != 0 ガードで全 skip
        YAML lookup -> my_course は course_joints.yaml 未登録 -> NULL -> skip
        => 結果: CourseJointLoadImpl は実質 no-op

[render loop]
   HSD render pass scan (OPA / XLU / TEXEDGE)
        scene_data.JOBJDescs[0].RootJoint を traverse
        JObj.flags の ROOT_OPA bit が立っていれば OPA pass の対象
        各 DObj の MObj.RenderFlags + POBJ.flags で描画判定
```

---

## 各ファイルの「書く / 書かない」ガイド

### `features/course_models.yaml`

```yaml
<id>:
  file: <stem>.dat
  joints: []          # 必ず [] (= 未参照 dead field)
```

- `joints` は **絶対に書かない**。記述しても誰も読まない (joint_extend は別 yaml = `course_joints.yaml` を見る)
- 同じ .dat を別 id で再利用したい場合 (joint subset 別解釈等) は別 entry を追加
- `file` は ISO root からの相対パス (= Riivolution の `<file create>` で配置するファイル名)

### `features/cups.yaml`

round 単位で必須 / 任意のフィールド:

| field | 必須 | 内容 |
|-------|------|------|
| `id` | ◯ | C ident。`round1` `round2` 等。シンボル名 suffix |
| `course_model` | ◯ | `course_models.yaml` の id |
| `collision` | ◯ | `<stem>.bin` (Riivolution 配置名) |
| `line_bin` | ◯ | `<stem>_line.bin`。**未生成の場合は別 round の流用も可** (`test_course_short_line.bin` 等) |
| `laps` / `time` / `bonus` | ◯ | レース設定 |
| `bgm_l` / `bgm_r` | ◯ | DSP filename。共有でも個別でも可 |
| `thumb` / `thumb_road` | ◯ | round-select cell の thumbnail PNG (相対 path、`features/custom_assets/images/` 起点) |
| `ai_lap_bonus_rules` | ◯ | `*shared_ai_lap_bonus_rules` anchor 流用が無難 |
| `base_speed` | ◯ | `*shared_base_speed` anchor 流用 |
| `base_speed_rounds` | △ | per-round 上書き (任意、cc_class 配列内 8 entry) |
| `start_positions` | △ | HSD world 座標 `[X, Y, Z]` 配列、1-8 個。少ない場合は最後の値で 8 まで pad される |

`start_positions` 未指定の場合、cup_page3 が cupId=0 (vanilla MR_highway) の table を alias で返すので **vanilla の MR_highway start でレースが始まる**。my_course の路面と座標系が違うと地面下 / 場外スポーン。必ず指定推奨。

### `features/joint_extend/course_joints.yaml`

**書く必要なし** (round で variant 切替えしないなら)。書く必要が出るのは:

- short/long round で別 mesh を出し分けたい (例: round1=short, round2=long で同じ .dat の異なる subtree を表示)
- normal/reverse round で別 mesh を出し分けたい

その場合は:

1. 18 slot 全部を別名で書く (e.g. `my_course_short_road_joint`, `my_course_long_road_joint`, ...)
2. `gen_joints_header.py` の `cup_ids` dict に `"my_course": 17` を追加
3. my_course.dat 側にも対応する alias root を per-slot で追加 (今は `_promote_vis_to_hsd.py` が固定 1 alias `<name>_joint` だけ。複数 alias 化は addon 拡張要)

### `features/custom_assets/images/`

cup-select scene の icon/name/trophy/banner/cup_name_ribbon と、round-select scene の name_roundselect / thumb / thumb_road を必要枚数置く。`gen_custom_assets_header.py` が cups.yaml と合わせて TPL を吐く。

---

## Blender 側の作業 (vis: 経路)

### Collection 構造

```
Scene/
  vis:my_course/                         <- 編集専用 (HSD 一切エンコードしない)
    my_course_road            (mesh, material slot 1+ で色分け)
    my_course_infield         (mesh)
    my_course_wall_outer      (mesh)
    ... 30+ mesh ...
  MKGP2_Course/                          <- addon が生成、collision/line を管理
    my_course/
      my_course_collision_a   (mesh, GroundType vertex group で hi/lo 分割)
      my_course_collision_b   (mesh)
      my_course_line          (Empty)
        my_course_line.0_lap  (Empty)
        my_course_line.1_normal_a
        my_course_line.2_normal_b
        ... 7 variant ...
        my_course_line.0_lap_v0  (子 NURBS path)
        ...
      my_course_auto          (mesh, auto-F field)
      my_course_origin        (Empty, course origin marker)
```

### マテリアル

- `vis:` 経路は **Principled BSDF 1 ノードのみ**。テクスチャ無し、unlit 単色 (BSDF base color が POBJ vertex color として書き出される)
- `mb.set_cull_back(True)` が `_promote_vis_to_hsd.py` で立つので Blender CCW winding でそのまま正しく描画される
- mesh の `material_slots` を増やすと slot ごとに POBJ が分割される (1 slot = 1 DObj/MObj/POBJ)

### Export operator

- **MKGP2 Full Course (一括)**: `File > Export > MKGP2 Full Course (HSD + collision + line + auto)` または Sidebar の Full Course button
  - 内部で `_promote_vis_to_hsd.promote_vis_to_dat` (vis: → .dat) + collision exporter + line exporter + auto-F exporter を順に呼ぶ
  - 出力先 dialog で `<dest>/my_course.dat` 等の prefix を選ぶ
- **Promote vis: only**: vis: collection だけ .dat に焼きたいときの単独 operator (Sidebar > MKGP2 > Promote vis:)

### Vanilla 上書き防止

- addon preference に `vanilla_files_dir` を持たせ、ISO dump dir 配下への書き込みを refuse する
- detail: `tools/blender/blender_addon_mkgp2_course/test_addon_vanilla_safety.py` の 8 phase テスト

### Headless export (CI / 検証用)

```bash
"/c/Program Files/Blender Foundation/Blender 4.3/blender.exe" \
  --background "<your.blend>" \
  --python tools/_blender_headless_promote.py \
  -- "<output_dat>"
```

- vanilla `.dat` は読まない (fully independent)。scene_data は `hsdraw.Dat.alloc_scene_data()` で fresh 生成
- 出力サイズの目安: 16000 verts / 5000 tris の中規模コース (4x4 dummy texture x 39 material) で ~490 KB

---

## 出来上がる .dat の構造 (検証用)

```
Roots: 2
  scene_data (HSD_SOBJ)
    JOBJDescs[0].RootJoint -> JObj#0
  <stem>_joint (HSD_JOBJ alias) -> JObj#0 と同じ struct を pointer

JObj#0
  flags = OPA, ROOT_OPA   (= 0x10040000)   <- 必須 (NULL だと render pass scan が skip)
  Dobj chain: 39 個 (= material slot 数の合計)
    each DObj:
      MObj
        RenderFlags = CONSTANT, DIFFUSE  (= 0x05)
        Material: DIF_RGBA = BSDF base color
      POBJ
        flags = CULLBACK   (= 0x4000)   <- Blender CCW winding 用
        DL: 0x98 (TRIANGLE_STRIP) opcode、F32x3 position + RGBA8 color
```

dump 検証:

```bash
dotnet-script tools/hsd/hsd_dump.csx -- features/cup_page3/files/my_course.dat | head
dotnet-script tools/hsd/hsd_dump_jobjdescs.csx -- features/cup_page3/files/my_course.dat
```

**`flags=NULL` または `POBJ.flags=0` を見たらバグ**。`_promote_vis_to_hsd.py` が flags を立てているか確認 (HEAD c12f608 では未対応、現在は対応済み)。

---

## ハマりポイント (実機で踏んだ実例)

### 1. 永久 retry hang

`FileLoader_LoadBin` がリトライループで戻らない。原因: `GetRoadDatFilenameAltHook` (0x8009c5d0) が collision .bin を返していた (= 古い実装) → HSDArchive header check sizeMismatch。**`GetCollisionBinFilenameHook` という名前は嘘**で、実体は **road .dat 2nd getter** (`PTR_s_test_course_road_dat_8040b920` rodata symbol が証拠)。`round->courseModelFile` を返すこと。

### 2. レースに入れるが画面真っ黒

JObj.flags = NULL。ROOT_OPA bit が無いので OPA pass scan が tree skip。`_promote_vis_to_hsd.py` で `root_jobj.flags = (1<<18) | (1<<28)` 必須。

### 3. カメラ位置依存にコースが点滅 / フェード

POBJ.flags = 0 (cull 無し)。HSD CW vs Blender CCW で背面 fragment が前面と z-fighting して camera 距離で勝ち負けが変わる。`mb.set_cull_back(True)` 必須。

### 4. ロードまでは行くが地面下に落ちる

`start_positions` 未指定 → vanilla MR_highway の start が使われる。my_course の路面が Y≈0.5 でも MR_highway start は Y=もっと上。yaml に `start_positions` を 8 行書く。

### 5. cup-select は出るが round-select で固まる / 文字化け

`custom_assets` の thumb/thumb_road が未配置 or `gen_custom_assets_header.py` がまだ未走行。`bash build.sh` を再実行。

### 6. コースは見えるが、自分の移動でフェードする (光源反射のような点滅)

vanilla の **primary course mesh は全件 textured** (`CONSTANT|TEX0|ALPHA_MAT` = 0x2011)。textureless 構成は vanilla では shadow / overlay 用 (`DN_stadium_shade_al.dat`, `DNA_startgate.dat`) しか存在せず、これらは LIGHTING bit を立てて lighting 計算で色を出す別 path。

**実機検証で点滅が消えた構成 (2026-05-09 確認済、test_cup round 3)**:

- POBJ attr = **POS + NRM + TEX0 (UV)** ← TEX0 は UV (0,0) 固定で OK
- POBJ.flags = **`CULLBACK`** (= 0x4000)
- JObj.flags = **`OPA, ROOT_OPA`** (= 0x10040000) ← LIGHTING 抜き
- MObj.RenderFlags = **`CONSTANT, TEX0, ALPHA_MAT`** (= 0x2011)
- TObj attached: 4x4 RGBA8 colored texture (= material color baked)
- 色は texture sample が提供 (Material.DIF はそのまま、CONSTANT mode で multiply)
- Per-material slot で 4x4 colored Image を Blender 側に bake (`_bake_vis_textures.py`) → BSDF Image Texture node 経由で export pipeline が読み出す
- scene_data: `hsdraw.Dat.alloc_scene_data()` で fresh 生成 (vanilla `.dat` は読まない)

vanilla `test_course_road.dat` の root JObj raw 64 bytes と完全一致する構成。hsd_dump.csx で確認:

```
JOBJ#0 flags=OPA, ROOT_OPA
MOBJ#0 RenderFlags=CONSTANT, TEX0, ALPHA_MAT (0x2011) TexRef=True
```

ファイルサイズは vert dedup を捨てる + texture 追加 影響で ~5x (96 KB → 490 KB)。

> **どの要素が決定打かは未切り分け**: 動作する状態を得るまでに `ALPHA_MAT 追加` / `LIGHTING bit 抜き` / `4x4 texture 付与` / `OPA|ROOT_OPA` / `CULLBACK` を同時に導入した。どれか 1 つだけでも崩すと点滅再発する可能性は十分あるので、**上記の組み合わせを 1 セットで保つ**ことを推奨。再発したら逆順に 1 つずつ外して切り分け。
>
> なお過去のデバッグでは「LIGHTING bit を抜けば消える」「ALPHA_MAT を立てれば消える」など何度か単独原因仮説を立てたが、いずれも build.sh を走らせ忘れていて user 検証では古い描画が見えていただけだった (= 仮説検証になっていなかった)。詳細は pitfall #7。

恒久対応は hsdraw 本体に textured mesh preset を追加 (現状 `MObj.alloc_unlit_color` は textureless 向けで不十分):
- `MObj.alloc_textured(color, image_w, image_h, image_data)` 的な one-shot allocator
- RenderFlags 強制 0x2011、TObj+Image 自動配線

### 7. asset を編集したのに「変わらない」ように見える

**症状**: `features/cup_page3/files/my_course.dat` を再 export したり `_swap_in_vanilla_mesh.py` で書き換えたあと Dolphin で起動しても、絵が前回と同じまま。何度修正しても効果なし。

**原因**: Dolphin が Riivolution 経由で読むのは `C:\Users\naari\Documents\Dolphin Emulator\Load\Riivolution\mkgp2_patch\my_course.dat` の方であって、repo 側 `features/cup_page3/files/my_course.dat` ではない。両者は **build.sh の末尾の cp ループでしか同期しない**:

```bash
# build.sh 末尾より
RIIV_FILES_DIR="$RIIV_ROOT/mkgp2_patch"
for asset in "$PATCH_DIR"/features/*/files/*; do
    [ -f "$asset" ] && cp "$asset" "$RIIV_FILES_DIR/"
done
```

つまり asset を 1 文字でも触ったら **必ず `bash build.sh` を実行**する。`.cpp` を変えてないから `Kamek` も走らせる必要がない、と思っても、cp ループのために build.sh は必須。

**確認方法**:

```bash
# repo 側と Riivolution 側の同期を ls で照合
ls -la features/cup_page3/files/my_course.dat \
       "C:/Users/naari/Documents/Dolphin Emulator/Load/Riivolution/mkgp2_patch/my_course.dat"
# 両方の size と mtime が一致してなかったら build.sh 漏れ
```

**過去の事例**: `_promote_vis_to_hsd.py` の MObj/JObj flags を 5 通り試して全部「変わらない」と user に報告された。実は 5 通り全部 build.sh を走らせ忘れていて、Dolphin は 1 番最初の export を見続けていた。最終的に build.sh を入れたら 1 発で点滅が消え、5 ラウンドの仮説検証が全部無駄になった。

**ルール**: user が「ビルドはいるの？」と聞いてきたら **即「いる」と答える**。`features/*/files/` か `*.cpp` か `gen_*_header.py` か `externals.txt` か yaml を 1 つでも触っていたら必ず build。「いらない」と答えていいのは、git diff が完全に空のときだけ。

### 8. 「scene_data.RootJoint を直接 repoint しただけで描画されない」

それは **古い解析** (`mkgp2_custom_course_modding.md` 由来)。joint_extend 入れる前の話で、現在は CourseJointLoadHook が name-based resolve するため scene_data 側 alone で描画される。alias root は `<stem>_joint` 1 個でも OK。

---

## やらなくていい (= 過去の誤作業)

- ~~base .dat (MR_highway_short_A.dat) を借用して空 alias root 12 個を残す~~  → **不要**。inu パターンは「scene_data.RootJoint repoint だけだと描画されない」古い時代の workaround。今は flags をセットすれば 1 alias root で動く。
- ~~`course_models.yaml.joints` を埋める~~ → **不要**。joint_extend は別 yaml を見る。
- ~~`course_joints.yaml` に my_course (cupId=17) を追加~~ → **variant 切替えしない限り不要**。GetJointNameTableHook が cupId=0 にフォールバックして MR_highway 18 alias を引きにくるが、my_course.dat にそれら alias は無いので state[*]=0 で全 skip = no-op で通過する。
- ~~Blender bundle の `mkgp2_joint_aliases` UI で alias を追加~~ → vis: 経路ではこの UI は通らない。固定 1 alias `<name>_joint` で十分。

---

## 関連ファイル / docs

| パス | 内容 |
|------|------|
| `features/cups.yaml` | cup と round の binding (single source of truth) |
| `features/course_models.yaml` | `<id>` -> `.dat` マッピング |
| `features/cup_page3/cup_page3.cpp` | 6 hook (course filename, road alt, joint name table, start position, ...) |
| `features/cup_page3/gen_cup_courses_header.py` | yaml -> generated header (CustomRound[]) |
| `features/joint_extend/joint_extend.cpp` | CourseScene_Load 0x80047bb0 hook |
| `features/joint_extend/course_joints.yaml` | 18 slot joint name per cup (vanilla 8 cup + custom 必要なら追加) |
| `features/custom_assets/cup_assets.yaml` | sprite TPL re-routing (cup-select / round-select) |
| `tools/blender/blender_addon_mkgp2_course/__init__.py` | addon 本体、operator 群、Sidebar UI |
| `tools/blender/blender_addon_mkgp2_course/_promote_vis_to_hsd.py` | vis: -> .dat 合成 (from-scratch) |
| `tools/blender/blender_addon_mkgp2_course/_export_mkgp2_bundle.py` | mkgp2: -> .dat 再 export (vanilla 編集経路) |
| `tools/_blender_headless_promote.py` | CLI から vis: を再 export する補助 |
| `tools/hsd/hsd_dump.csx` | .dat 構造 dump (HSDLib 経由) |
| `tools/hsd/hsd_dump_jobjdescs.csx` | scene_data.JOBJDescs と RootJoint flags を簡易 dump |
| `tools/hsd/hsd_compare_root_jobj.csx` | 2 ファイル間の root JObj diff (TRS/flags/DObj/POBJ 詳細) |
| `~/src/github.com/dolphin-emu/dolphin/mkgp2docs/mkgp2_course_joint_loader.md` | 古い解析 (joint_extend 入れる前の前提)、現状とズレあり (上記「ハマり 6」参照) |
| `~/src/github.com/dolphin-emu/dolphin/mkgp2docs/hsd_to_blender_visual_pipeline.md` | HSD -> Blender 視覚参照 pipeline (mkgp2: bundle 経路の解説) |
| `~/src/github.com/dolphin-emu/dolphin/mkgp2docs/mkgp2_course_layout_system.md` | course filename getter 4 系統 + cupId stride 解析 |

---

## 新しい cup を追加したいとき (1 round じゃなく 1 cup)

このスキルの範疇外だが概略:

1. `cups.yaml` に `<new_cup>: cup_id: 18` 等で追加 (>=17 が必須)
2. `display_alias_cup` を選ぶ (= UI でどの vanilla cup の見た目を借りるか)
3. `custom_assets` 側で cup-select scene 用 7 PNG (icon/name/trophy/banner/cup_name_ribbon/name_roundselect 等) を用意
4. `round_select` の alias swap が cups.yaml の追加 entry から自動展開される (kCupAliasMap[])
5. cup_id を増やすと vanilla テーブル境界 (`kCup0LineBinTable` は 9 cups 限定等) を超える可能性があるので `MEMORY.md > project_cupid_table_boundaries.md` 参照 (13 getter hook 方式必須の場合あり)

---

## デバッグ Tips

### in-game ログを見る

`Dolphin User\Logs\dolphin.log` に `HLE` / `OSREPORT_HLE` チャンネルが有効なら patch の DebugPrintfSafe 出力が出る。

```
MKGP2: joints loaded cup=17 long=0 reverse=0 occShort=0 occLong=0
```

`cup=17` と全 occShort/occLong=0 が並んでいれば joint_extend は no-op で通過している (= 期待動作)。

### live memory view

`mkgp2-view` (`~/src/github.com/naari3/mkgp2-view/`) で Dolphin プロセスからアドレス実時間読み取り。`g_cupId` `g_roundIndex` 等が confidence 高く確認できる。

### HSD .dat の中を見る

```bash
dotnet-script tools/hsd/hsd_dump.csx -- <path>.dat | head -40
dotnet-script tools/hsd/hsd_dump_jobjdescs.csx -- <path>.dat
```

`JObj#0 flags=OPA, ROOT_OPA` と `POBJ#0 flags=CULLBACK` が出ていれば render OK。
