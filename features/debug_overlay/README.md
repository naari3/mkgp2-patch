# debug_overlay

開発時に画面上で `g_SpriteHandlePool` (= 描画される 2D スプライト全体) の
状態を inspect するためのオーバーレイ。アクティブな各 sprite の resource ID
/ ファイル名 / サイズ / 位置を、リスト表示や per-sprite ラベル、AABB の
矩形 outline で可視化する。

## hook 位置

`kmCall(0x8002c678, DebugOverlay_FrameHook)` で `MainGameLoop`
(= `FUN_8002c5e8`、実 per-frame loop) 内 `bl SpriteHandlePool_GC` の 1
箇所を replace する。フォワードで vanilla の GC を呼んだ後、自前の
DisplayContext + DrawText / DrawColoredQuad / GX_LINES で overlay を出す。

`DebugOverlay_Dispatch` (vanilla 側のデバッグ表示 dispatcher) のすぐ前で
fire するので、毎 frame 確実に動く。boot シーケンス中の card task 待機
loop (元 `MainGameLoop` と Ghidra で呼ばれていた `FUN_8002dd58`) ではなく、
本当の per-frame loop で動くのが重要。詳しくは `tasks/lessons.md` の
"main game loop を MainGameLoop と決めつけて hook した" 節。

## 表示モード

`g_dbgOverlayMode` (4 byte int, default 0) を Dolphin Memory Engine 等で
書き換えてライブ切り替えできる。

| mode | 内容 |
|---|---|
| 0 | サマリー 1 行 (active / visible カウント) |
| 1 | 全 active+visible slot を左上に list (id, size, pos, filename) |
| 2 | 各 visible sprite の AABB 左上に id label |
| 3 | mode 2 + 緑 1px 矩形 outline (4 thin filled QUAD) |
| 4 | 中央 100x30 magenta バー (DrawColoredQuad の smoke test) |
| 5 | 中央 magenta クロス (GX_LINES の smoke test) |
| 6 | mode 2 + cyan 矩形 outline (4 GX_LINES per sprite) |

`g_dbgOverlayEnabled` (default 1) を 0 にすると全 mode 共通で off。

## 内部構造

DisplayContext (font 付き 2D テキストレンダラ) を自前で `Alloc(0xd8)` +
`DisplayContext_Init` で 1 個確保する。boot 時に lazy 初期化、以降の
frame では使い回し。

mode 3 / 4 / 5 / 6 の rect 描画は vanilla の `DrawColoredQuad`
(`FUN_801526c4`) と GX_LINES piggyback を使っている。背景・state setup
の詳細は別 doc を参照:
[mkgp2docs/mkgp2_screenspace_2d_emit.md](https://github.com/dolphin-emu/dolphin/blob/master/mkgp2docs/mkgp2_screenspace_2d_emit.md)。

## ビルド毎に変わるアドレス

`g_dbgOverlayEnabled` / `g_dbgOverlayMode` のアドレスはパッチ bin の
レイアウトに依存して毎ビルド変わる。確認は `mkgp2_patch.map` (build.sh が
自動生成) を grep。

```
$ grep g_dbgOverlay mkgp2_patch.map
  806F47E0 000004 g_dbgOverlayEnabled
  806F4E00 000004 g_dbgOverlayMode
```

## 既知の制約

- DisplayContext の entry buffer は 127 entry まで。mode 1 の list は 28 行
  cap、mode 2 の per-sprite label は 110 個 cap で安全圏に収めている。
- mode 6 の sprite 数 cap は 80 (4 line × 80 = 320 emit/frame)。実測上
  問題は見えていないが GX_LINES path は vanilla 未踏なので注意。
- mode 3 / 6 の矩形は `vertCoords[8]` (4 corner xy) の AABB を使う。回転
  済み sprite (rotation != 0) でも AABB は外接矩形になる。
