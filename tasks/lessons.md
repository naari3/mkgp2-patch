# Lessons (mkgp2-patch)

プロジェクト開発で得た教訓を記録。同じ失敗を繰り返さないよう、次回セッション開始時にレビューする。

---

## 2026-04-24: custom_assets の sibling binding は推測で追加するな

**失敗**: ページ3カーソル移動時の 1-frame vanilla フラッシュを消そうとして、ログ (`MKGP2: cup17 GroupKey queries 0x...`) で cup=17 中にクエリされてる未バインドID (0x1780, 0x178B, 0x1788, 0x1751, 0x176B, ...) を片っ端から `0x9000`/`0x9001`/`0x9002` にバインドした。

**結果**:
- 謎の位置にカップが重複表示
- 座標ズレ
- FPS 激落ち (PreloadResource が 129 IDs 分の DVD open を走らせる)

**根本原因**: ログに現れる ID はあくまで「`g_cupId==17` 中に resource getter が呼ばれた」というだけで、ページ3カップタイルと無関係な他UI要素 (help text, subtitle, BG atlas, subtitle banner, etc.) も含む。それらを cup icon (`0x9000`) に route すると、本来 148×64 サイズの banner slot に 128×128 RGBA32 を読ませて座標破綻 + 同じテクスチャが複数箇所に重なる。

**次回からの判断基準**:
- 「特定 UI 要素を差し替える」ためのバインドは、その要素の vanilla 描画パスを decompile / Ghidra で追跡し、実際にその要素の描画で使われる ID のみを対象にする。ログの ID 一覧は探索のヒント止まり。
- 未知の resource ID を bulk で bind するのは絶対 NG。せめて 1 個ずつバインド→ビルド→目視で「これを差し替えると何が変わるか」を確認する。
- Chain walk の next_id リダイレクトは alpha mask 等の内部フォーマットが違うので `RGBA32` に差し替えると blend が破綻する。chain を終端 (`-1`) で切るのが安全。

**MVP 判断**:
- ページ3の8タイルが test_cup を表示する目標は達成 (25 bindings)。
- カーソル移動時の 1-frame flash は未解決だが、機能的にはカップ選択・ラウンド遷移・レース全て動く。cosmetic 優先度は低。必要なら将来 Ghidra でタイル描画パスを decompile して正確に対応する。

---

## 2026-04-26: 'main game loop' を MainGameLoop と決めつけて hook した

**失敗**: debug_overlay 機能で、毎フレームのデバッグ HUD を出すために `MainGameLoop @ 0x8002dd58` 内の `bl FUN_80121120` (0x8002e4ec) を kmCall した。起動シーケンスの最初の数フレーム (~50ms) だけ HUD が出て、PCB ID チェック画面以降ずっと出ない現象になった。

**結果**:
- HUD は boot 直後の 3 frame だけ表示
- PCB ID check 画面 / title / cup select いずれでも一切出ない
- 一方で custom_assets の getter hook は cup select でも fire し続けてた

**根本原因**: `MainGameLoop` という名前と中の `while (iVar6 == 2) { ... FUN_80121120() ... }` の構造から「これが per-frame の game loop」と決めつけた。実際にはこれは **card task 待機 loop** (`iVar6 == 2` = card task in progress)。card 読込が完了すると loop を抜けて、PCB check loop → notice loop と進んで MainGameLoop は return する。

実際の per-frame ループは `FUN_800ac894` が `MainGameLoop` 後に呼ぶ **`FUN_8002c5e8`** の `do { ... } while(true)`。中身に `DebugOverlay_Dispatch(param_1)` (vanilla 側のデバッグオーバーレイ呼び出し) や `bl 0x8002cd9c` (scene draw) が入っているので「これが本物」と判断できた。BL site は 0x8002c678 (main path) と 0x8002caf0 (cleanup)。

**次回からの判断基準**:
- 関数名や Ghidra の命名 (`MainGameLoop` 等) を信用しすぎない。caller chain (`get_function_callers`) を辿って、wrapper の構造を確認する。
- "per-frame loop" の判定は **vanilla がそこで debug overlay / scene draw / フレームカウンタ等を呼んでいるか** で確認。FUN_8002c5e8 は `DebugOverlay_Dispatch` を呼んでいたので確実。
- 候補が見えたら **動作で検証**: 診断 log を仕込んで、cup select 等の active scene でも fire するか確認 (今回これで気付いた)。
- 「複数候補がある hook 先」(今回 FUN_80121120 の callers が 7 個)のうち、どれが "normal gameplay frame" かは caller の構造を Ghidra で読んで決める。

