# Blender / hsdraw 周辺の残作業

`m1m3-full-independent-hsd-export` の merge と skill 整理 (mkgp2-new-course / mkgp2-edit-vanilla-course 両方完成、commit `500333c` まで) を経て確認した、未着手の項目を分野別に整理。

セッション境界: **2026-05-09 時点でクローズした作業**は本ファイルに含めない (= test_addon の csx/scene.json 撤去、addon README 整理、edit-vanilla skill 新規追加、新 mesh 境界検証、軸変換 + bake helper 整理、Collection 構造実名化 + 最低構成 + 動作確認 + rounds 順序、ローカルブランチ削除 — すべて main に commit 済み)。

---

## A. hsdraw 上流 (別 repo・別 session 案件)

リポジトリ: `~/src/github.com/naari3/hsdraw/` (vendored: `tools/blender/blender_addon_mkgp2_course/vendor/<platform>/hsdraw/hsdraw.pyd`)

- [ ] **A-1: 配列 API refactor (per-vertex → numpy bulk)**
  - 現状: Python 側 mesh export は `add_position(x,y,z)` / `add_color(r,g,b,a)` を頂点ごとにループしている
  - 目標: `from_arrays(positions=np.ndarray, colors=np.ndarray, ...)` 1 発に置き換え
  - 動機: PyO3 + rust-numpy で zero-copy 化、hsdraw 本来の Rust 速度メリットを Python 経路でも享受
  - 影響: `_export_mkgp2_bundle._build_pobj_for_mesh` / `_promote_vis_to_hsd._build_pobj_for_slot` の 2 箇所が numpy ベースに simplify される
  - 詳細: memory `project_hsdraw_array_api_refactor.md`
- [ ] **A-2: API consistency (JObj.dobj() getter, DObj.mobj 戻り値統一)**
  - `JObj` には `.child` / `.next` getter があるが `.dobj()` getter が無い (= mkgp2-patch 側が `as_struct().references()` 経由で offset 0x10 を手で引いている)
  - `DObj.mobj` は HsdStruct を raw で返すのに `DObj.next` は DObj wrapper を返す → 呼び側で `MObj.from_struct(s)` する必要があり inconsistent
  - 目標: getter を 1 セット (`jobj.dobj`, `dobj.mobj` → wrapper を返す) で統一
  - 影響: addon の type-check 分岐 (`if isinstance(x, hsdraw.HsdStruct): ... else: ...`) が消せる
- [ ] **A-3: Linux/macOS 用 wheel build**
  - 現状: `vendor/windows_x86_64/hsdraw/hsdraw.pyd` のみ shipping
  - 上流 `hsdraw` 本体は abi3-py37 で multi-platform 対応済 → maturin で各 platform build → vendor に配置すれば addon import が即通る
  - 優先度低 (= 開発者が Windows 中心)、要望が出たら対応

---

## B. mkgp2-patch リポジトリローカル

- [ ] **B-1: `_bake_vis_textures.py` を addon README に明記**
  - 現状: skill (`mkgp2-new-course`) では「per-mesh pattern が必要なときだけ使う」と書いたが、addon の `README.md` には言及なし
  - 目標: README の「使い方 > vis: 経路」に「BSDF Base Color の単色なら 4x4 fallback で済む / pattern が要るなら `_bake_vis_textures.py` を `Sidebar > MKGP2 > Bake vis: textures` から呼ぶ」を追加
  - operator id / button 配置を実コード (`__init__.py`) で確認してから書く
- [ ] **B-2: `MKGP2_OT_NewCourse` の docstring 整備**
  - 現状: docstring に「vis: と mkgp2: のどちらの collection を生成するか」が明文化されていない
  - 目標: `docstring` に「vis:<name> を生成 (新規コース合成用)、mkgp2:<dat> bundle は別 operator (`MKGP2_OT_ImportHSD`)」を追記。N panel の tooltip も同様
- [ ] **B-3: 新 material 受け入れ時のグレー fallback の挙動を再考**
  - 検証 (`tools/test_addon_bundle_add_mesh.py` v3 ケース) で判明: bundle に新材で塗った mesh を追加すると、exporter は **mesh を採用するが MObj は `alloc_unlit_color(200,200,200,255)` のグレー fallback で書き出す**
  - 現状: WARN ログは出るが UI に通知無し → ユーザーが気付かないうちに「色を設定したのに灰色」現象
  - 設計判断要: (a) WARN を Operator.report() で UI に出す / (b) refuse して reject / (c) 新材の Base Color を fallback に流す (= 簡易対応) のいずれを採るか
  - skill (`mkgp2-edit-vanilla-course`) には現状の挙動を明記済 → どこかで挙動変えるなら skill も更新
- [ ] **B-4: `tasks/todo_m1m3_full_independent_export.md` を archive に移動 or 削除**
  - 既に M1-M3 全完了 (memory `project_m1m3_unified_hsd_export_done.md`)、現役 todo ではない
  - 残しておくと「これがアクティブな計画か?」と紛れる → `tasks/done/todo_m1m3_full_independent_export.md` 等にリネーム or 削除
- [ ] **B-5: `tools/_blender_headless_promote.py` の引数仕様を README/skill から実機追試**
  - skill では `-- "<output_dat>"` 1 引数と書いた
  - 実コードを再読して invocation 行が現実と一致しているか確認 (もし複数 .blend を支える etc あれば skill 更新)

---

## C. 動作検証 / 実機確認待ち

- [ ] **C-1: round 3 (my_course) の `start_positions` 値検証**
  - 現在 yaml に書いた値が「Blender 軸変換が正しいか」を実機で確認する宿題
  - 確認方法: `mkgp2-view` で race 開始直後の kart 位置を読み、`_blender_to_hsd` 規則どおりか照合
  - skill の「動作確認手順」に書いた cheatsheet を一巡する作業

---

## D. 未確定 / 設計判断待ち (実害低、メモ留め)

- [ ] **D-1: vanilla `Auto.bin` の用途特定**
  - 現状コメント: "purpose still unclear" (= `tools/blender/blender_export_auto.py` head 8 行目)
  - 走らせるだけなら不要だが、何の path なのかは未解明 (= mini-map? AI 補助 line? camera path?)
  - PathManager 系ではないことだけ判明
  - 解明したら skill (`mkgp2-new-course` の「最低構成」表) と `mkgp2_course_layout_system.md` (Dolphin docs 側) を更新
- [ ] **D-2: hsdraw `MObj.alloc_textured(color, w, h, raw)` 一発 helper**
  - 現状: `_promote_vis_to_hsd._make_textured_mobj` が手で MObj/TObj/Image を組み立てて `render_flags = 0x2011` を後付けしている (= ALPHA_MAT 抜けバグの跡、memory `project_alloc_unlit_color_alpha_mat.md`)
  - hsdraw 上流に `MObj.alloc_textured(color_rgba, image_w, image_h, image_data)` 的な one-shot allocator を入れて `render_flags=0x2011 / TObj+Image 自動配線` を内蔵すれば、`_make_textured_mobj` が ~30 行 → ~5 行に縮む
  - A-1/A-2 と一緒に hsdraw upstream PR で扱うのが筋
