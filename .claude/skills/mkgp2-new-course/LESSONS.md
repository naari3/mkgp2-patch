# mkgp2-new-course Lessons & Retrospective

`SKILL.md` から退避した、過去の誤作業 / 検証失敗 / 命名の歴史 / 未来 TODO 等。
作業手順そのものではなく **背景・経緯・教訓**。トラブルが SKILL.md 側で再現
した時にだけ参照する。

---

## `GetRoadDatFilenameAltHook` の命名の経緯 (関連: SKILL.md 「永久 retry hang」)

このフックは過去に `GetCollisionBinFilenameHook` という名前で書かれていた。
実体は **road .dat の 2nd getter** であり、collision とは無関係:

- `PTR_s_test_course_road_dat_8040b920` rodata symbol が読み出し元
- 1st getter (`GetCourseModelFilenameHook`) と同じく `round->courseModelFile`
  を返す必要がある
- 旧実装はここで collision .bin を返していたため HSDArchive header check で
  sizeMismatch → `FileLoader_LoadBin` が永久 retry loop に陥っていた

新規 hook を足すときに「Collision」と付いた名前を見て collision を返さない
こと。命名は嘘なので疑う。

---

## 点滅が消えた構成は 5 要素同時導入 — 単独切り分け不能 (関連: SKILL.md「自分の移動でフェードする」)

`my_course` の点滅が解消した状態 (test_cup round 3 で 2026-05-09 実機確認) は、
以下を同時導入した結果:

1. POBJ attr に `POS + NRM + TEX0 (UV)` を含める
2. POBJ.flags に `CULLBACK` (0x4000) を立てる ※後の解析で 0x8000 = CULLBACK と訂正、`project_pobj_8000_cullback_winding.md` 参照
3. JObj.flags に `OPA, ROOT_OPA` (0x10040000) を立てる (LIGHTING bit は **立てない**)
4. MObj.RenderFlags = `CONSTANT, TEX0, ALPHA_MAT` (0x2011)
5. 4×4 RGBA8 colored texture を attach (Material color baked)

**どの 1 つを崩すと再発するかは未切り分け**。動作した状態に到達するまでに
これらを同時に投入したため、独立に validate していない。再発したら逆順に
外して bisect する。

過去のデバッグでは「LIGHTING bit を抜けば消える」「ALPHA_MAT を立てれば消える」
など何度か単独原因仮説を立てたが、いずれも `build.sh` を走らせ忘れていて user
検証では古い描画が見えていただけだった (= 仮説検証になっていなかった)。
詳細は下記「build.sh 5 ラウンド忘却事件」項。

---

## hsdraw 本体への恒久対応案 (関連: SKILL.md「自分の移動でフェードする」)

textured mesh preset を hsdraw に追加すれば、上記 5 要素の手作業組み立ては
1-shot allocator で済む:

- `MObj.alloc_textured(color, image_w, image_h, image_data)` 的な API
- 内部で RenderFlags 強制 0x2011、TObj+Image を自動配線

**現状**: 2026-05-11 時点で `hsdraw.MObj.alloc_textured(material, image,
**kwargs)` が hsdraw 側に追加され (handoff #5)、addon 側も `_blender_material.py`
で採用済 (commit `cd981fc`)。本案は **解消済み**。

---

## build.sh 5 ラウンド忘却事件 (関連: SKILL.md「asset を編集したのに変わらない」)

### 事件

`_promote_vis_to_hsd.py` の MObj/JObj flags を 5 通り試して、すべて「変わらない」
と user に報告された。実は 5 通り全部 `build.sh` を走らせ忘れていて、Dolphin は
1 番最初の export を見続けていた。最終的に build.sh を入れたら 1 発で点滅が
消え、**5 ラウンドの仮説検証が全部無駄になった**。

### AI 向けルール

User が「ビルドはいるの？」と聞いてきたら **即「いる」と答える**。
`features/*/files/` か `*.cpp` か `gen_*_header.py` か `externals.txt` か yaml
を 1 つでも触っていたら必ず build。「いらない」と答えていいのは、`git diff`
が完全に空のときだけ。

### 教訓

- 検証が「変わらない」結果に終わったら、まず仮説 (コードロジック) を疑う前に
  **環境 (build.sh / cp 同期 / Riivolution パス)** を疑う
- in-game 検証を依頼する前に `ls -la` で size+mtime 同期を必ず照合
- 「`.cpp` を変えてないから build 不要」は罠、cp ループのために必要

---

## 古いドキュメント (`mkgp2_custom_course_modding.md`) の前提 (関連: SKILL.md「alias root は `<stem>_joint` 1 個で十分」)

「scene_data.RootJoint を直接 repoint しただけで描画されない」「base .dat
(MR_highway_short_A.dat) を借用して空 alias root 12 個を残す」等の記述は、
`joint_extend` 導入前の workaround。現在は CourseJointLoadHook が name-based
resolve するため scene_data alone で描画でき、alias root は `<stem>_joint`
1 個で OK。

dolphin-emu の `mkgp2docs/mkgp2_course_joint_loader.md` 等を参照する際は、
**現在の hook 構成を必ず最初に確認**してから記述を採用する。古い解析を
そのまま信じると 12 alias root の borrowing をやり直すことになる。

---

## やらなくていい (= 過去の誤作業)

| 過去の誤作業 | 何が間違いだったか |
|---|---|
| base .dat (MR_highway_short_A.dat) を借用して空 alias root 12 個を残す | 「scene_data.RootJoint repoint だけだと描画されない」古い時代の workaround。今は flags をセットすれば 1 alias root で動く |
| `course_models.yaml.joints` を埋める | 誰も読まない。joint_extend は別 yaml = `course_joints.yaml` を見る |
| `course_joints.yaml` に my_course (cupId=17) を追加 | variant 切替えしない限り不要。GetJointNameTableHook が cupId=0 にフォールバックして MR_highway 18 alias を引きにくるが、my_course.dat にそれら alias は無いので state[*]=0 で全 skip = no-op で通過する |
| Blender bundle の `mkgp2_joint_aliases` UI で alias を追加 | vis: 経路ではこの UI は通らない。固定 1 alias `<name>_joint` で十分 |

---

## position cap = `start_positions` × `finish_line` 跨ぎ問題 (関連: SKILL.md「順位が 4 位より上に行けない」)

### 経緯

2026-05-11 my_course (cup 17 round 3) で「ラップは進むが順位が 4 位より上に行けない」と
user 報告。最初は AI 速度設定や `KartMovement_CalcFinishPosition` のロジックを
疑ったが、user 自身が **「ゴールラインより前にいる CPU の数だったりしない?」**
と指摘 → 検証で正解。

### 当時の状態

- `finish_line: [[1200, 0, 0], [1800, 0, 0]]` (= Z=0 の水平線)
- `start_positions:` Z = `[-150, -50, +50, +150]` 各 2 体ずつ → **Z=0 を跨いで 4 vs 4**
- 結果: Z<0 の 4 体 (= 既に line を通過した側) が常に上位 4 位を占有、player が Z>0 側にいると 5 位以下、player が Z<0 側にいても 4 位 (他の 3 体に勝てない)

### 修正

`start_positions` の Z を `[+50, +150, +250, +350]` に揃え (= 全 8 体が line の手前 / Z>0)、
`finish_line` は Z=0 のまま据え置き → 順位 cap 解消、1 位獲得可能。

### 機序の推定

`PathManager_UpdateAll @ 0x8003b6c4` の lap-cross flag は
`smoothedPathIndex ∈ [55..58]` で立ち、その後 `Math_Segment2DIntersect` が
prev→cur kart 線分と finish line 線分の交差を検出した瞬間に lap 確定。

`smoothedPathIndex` は raw `pathIndex` を「単調増加 + 1 frame あたり最大 +4」で
追従するスムージング値。スポーン位置によって `pathIndex` の初期値が異なり、
line を跨いだ 2 群で初回 lap-cross flag 発火タイミングが非対称になる
(→ 一方の群が先に `currentLap == -1 → 0` の transition を済ませ、その後の
position sort で永続的に上位)。完全な機序解明はしていない (再現性確認のみ)。

### 教訓

- 「順位が cap される」=「N 体が常に上位を占有」と言われたら、まず
  `start_positions` × `finish_line` の幾何関係を疑う
- 直接 PathManager_UpdateAll の decompile を読み込むより前に、
  **user の幾何的直観 (= 「線の前後で CPU が分かれている」) を
  まず検証**するのが速い
- conventional racing layout = 「全 kart は finish line の手前に並ぶ」を
  custom course でも踏襲する

### 関連

- 修正 commit: branch main (2026-05-12)
- 関連 hook: `cup_page3.cpp` の `FUN_8009c3c4_Hook` (= finish_line getter)
- 関連 memory: `project_position_cap_finish_line_straddle.md` (本件)

---

## 関連 memory entry

- `feedback_features_files_require_build_sh.md` — 「build.sh 5 ラウンド忘却事件」と同源
- `feedback_no_premature_root_cause_confirmation.md` — 同上の教訓を一般化
- `feedback_commit_at_in_game_confirmation.md` — in-game 確認後即 commit
- `project_my_course_flicker_resolved.md` — 「点滅が消えた構成は 5 要素同時導入」の最終解決報告
- `project_pobj_8000_cullback_winding.md` — 同上 CULLBACK bit 訂正
- `project_position_cap_finish_line_straddle.md` — 「position cap = `start_positions` × `finish_line` 跨ぎ問題」(本件)
