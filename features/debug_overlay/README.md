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
| 0 | off (overlay 一切描画しない、default) |
| 1 | サマリー 1 行 (active / visible カウント) |
| 2 | 全 active+visible slot を左上に list (id, size, pos, filename) |
| 3 | 各 visible sprite の AABB 左上に id label + cyan 矩形 outline (GX_LINES) |

## 内部構造

DisplayContext (font 付き 2D テキストレンダラ) を自前で `Alloc(0xd8)` +
`DisplayContext_Init` で 1 個確保する。boot 時に lazy 初期化、以降の
frame では使い回し。

mode 3 の矩形描画は vanilla の `DrawColoredQuad` (`FUN_801526c4`) で GX
state を立ち上げてから GX_LINES を 4 本/sprite emit する piggyback 構造。
背景・state setup の詳細は別 doc を参照:
[mkgp2docs/mkgp2_screenspace_2d_emit.md](https://github.com/dolphin-emu/dolphin/blob/master/mkgp2docs/mkgp2_screenspace_2d_emit.md)。

## ビルド毎に変わるアドレス

`g_dbgOverlayMode` のアドレスはパッチ bin のレイアウトに依存して毎ビルド
変わる。確認は `mkgp2_patch.map` (build.sh が自動生成) を grep。

```
$ grep g_dbgOverlayMode mkgp2_patch.map
  806F47EC 000004 g_dbgOverlayMode
```

## 既知の制約

- mode 2 の list 表示は **28 行 cap** (画面 480px / 行高 10px に収める)。
  cap を超えた slot は表示されないが、サマリ行で総数は分かる。
- mode 3 (label + 矩形) は **cap なし**。pool 全 500 slot を walk する。
  vanilla `DrawText` には `< 0x7f` (= 127 entry) のハード cap が hardcode
  されているが、loop 中で 120 entry ごとに `DisplayContext_Flush` を呼んで
  buffer をドレインすることで実質撤廃している。Flush 後も EFB に既描画分
  は残るので、複数 Flush を重ねるとそのまま積み上がる。
- mode 3 の矩形は `vertCoords[8]` (4 corner xy) の AABB を使う。回転済み
  sprite (rotation != 0) でも AABB は外接矩形になるので、視覚的には実際の
  描画形状より大きい矩形が出る場合がある。
