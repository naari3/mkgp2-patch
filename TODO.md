# TODO

cupId=17+ 移行で残った積み残し。発生順・必要性順ではなく、テーマごとに整理。

## カスタムトラック機能拡張

### カップセレクト / ラウンドセレクト の独自アセット挿入
- **現状**: cupId=17+ の page3 は、page1 (ウラ) の sprite state / TPL テクスチャをそのまま流用。カップ名・カップ絵・ラウンドサムネ・ラウンド名が全部 vanilla の「ウラ ○○カップ」表示のまま (最初のカップを選ぶと見た目はウラヨッシーカップだがレースは test_course、のような齟齬)。
- **やること**:
  - カップ絵テクスチャ: `features/cup_page3/files/` に TPL 追加 → Riivolution `<file create="true"/>` で ISO 側に積み上げ。既存アセットテーブル (`tools/resource_table_dump.py` で空き ID 確認) に挿入するための hook を立てて `Sprite_SetupAnim` 系の引数 ID を新規割当。
  - カップ名文字列: game-side の `CupName_Get` 相当 (未同定、要 Ghidra scan) を hook して custom cupId → 自前文字列リソース。
  - ラウンドサムネ: per-track の minimap 画像 TPL を CustomTrack に `thumbnailTpl` として追加、ラウンドセレクト描画前に差し替え。
  - ラウンド名: ラウンドセレクト画面の label getter (未同定) を hook して CustomTrack の `displayName` を返す。
- **優先度**: CustomTrack が「ちゃんと固有のトラックに見える」ための基盤。他の機能 (model/object/joint の distinct 化) より UX 寄与が大きい。

### coin spawn table を per-track で指定可能に
- **現状**: `FUN_8009c238_Hook` は custom cupId に対し NULL を返す。`CoinSystem_Init` は NULL 受けると早期リターンするのでコインが 0 個。旧 cupId=0 の test_course も `iVar2 = -1 < 0` ガードで同じ NULL 経由。
- **やること**: `CustomTrack` に `coinSpawnTable` field を追加。yaml では座標リストで書けるように。
- **エントリ形式**: 16-byte 程度、+0 x / +4 y / +8 z / +0xc flags。**terminator は `*(ptr + 0xc) >> 31 != 0`** (IsSpawnTableTerminator @ 0x8005f5cc が bit31 で判定)。

### model / object / joint / start 位置 を distinct 化
- **現状**: 11 本の asset getter hook は custom cupId を cupId=0 に alias して vanilla rodata を読む。vanilla cupId=0 は dev leftover の test_course データなのでクローン以上のことはできない。
- **やること**: `CustomTrack` に次の field を追加し、各 hook で CustomTrack 優先・NULL なら cupId=0 alias に fallback:
  - `courseModelRoad` / `courseModelMesh` (GetCourseModelFilename)
  - `collisionBin` (GetCollisionBinFilename)
  - `objectTable` / `jointTable` (GetCourseObjectTable / GetJointNameTable)
  - `startYaw` / `startPositions[N]` (GetCourseStartYaw / GetStartPosition)
  - `bgmEntry` / `FUN_8009c1d0` / `FUN_8009c360` / `FUN_8009c3c4` 相当データ (用途未調査、要 caller 追跡)

### round-intro voice id
- **現状**: `FUN_801b0af4` の push predicate を `cmplwi r3, 15 / bgt` に絞ったため custom cups はレーススタート時の「オモテ/ウラ ○○コース!」ボイスが無音。
- **やること**: `CustomTrack` に `voiceId` (short) を追加。predicate 直前に hook を挟み、custom cupId なら voiceId に置換して push、そうでなければ vanilla 式。

### BGM dsp の per-track override
- **現状**: すでに `CustomBgmPair` + relocated `kCustomBgmTable` で対応済 (`WeatherInitCustom` が custom cupId の bgmPair を見る)。test_course は `bgm01_demo`(L/R) を再利用。
- **やること** (軽め): 新規カスタムトラックで固有 BGM を使いたい時の手順を README に書く。現 yaml の `bgm_l` / `bgm_r` を `features/cup_page3/files/` に配置するだけで Riivolution が吸う。

## コード整備

### FUN_\* の Ghidra rename
cup_page3 hook で意味が確定した以下を rename + plate 付け:
- `FUN_8009c238` → `GetCoinSpawnTable` (stride 8 ptr、base 0x8040b218、`(cupId-1)*8 + long*4 + reverse*2 + TA?1:0`)
- `FUN_8009c1d0` → 要調査 (stride 0x228 raw、base 0x8040b93c、reverse yes / variant no、u32 return)
- `FUN_8009c360` → 要調査 (stride 0x228 raw、base 0x8040ba10、reverse no / variant yes、u32 return)
- `FUN_8009c3c4` → 要調査 (stride 0x8a ptr、base 0x8040b930、variant no、char* return)

### kCup0LineBinTable まわりの遺物削除
- `gen_cup_courses_header.py` にまだ `COLLISION_TABLE_BASE` 等の定数が残っている。custom tracks は全て hook 経由なので使ってない。次 refactor のタイミングで掃除。
- `generated_cup_courses.h` の「// --- (removed) Per-cup asset struct collision overrides ---」 セクションは traceability コメントが主。古くなったら削る。

### externals.txt の FUN_\* 記載
- `FUN_8003b120` / `FUN_8007e344` / `FUN_8007dfe4` を MemoryManager / DVD 系の正式名に改名したい (Ghidra 側の rename 連動)。当面は `FUN_\*` で通用するのでそのまま。

## 検証・将来的な課題

### 他の cupId-indexed 箇所の網羅
- 0x8009c000..0x8009c800 の範囲は全 13 本 hook 済。
- それ以外の領域 (他 module から g_cupId を読んで `cupId * N` で table を引く箇所) は未スキャン。custom cupId=17 で新たな invalid read が出たら `FindCupIdStrideAll.java` を範囲広げて再スキャン。

### 2 トラック目以降の扱い
- `kCupPage2Courses[8]` は現状 tracks[0] で 8 スロット全部埋め。tracks 複数定義したら cursor ごとに違うカップになる想定 (gen.py は対応済)。
- ただし同一ページ内で「どこに何を配置するか」の UI はまだ vanilla に ウラカップ用のまま。sprite 配置 / カップ名テクスチャを差し替えたいなら `Sprite_SetupAnim` 呼び出し周辺の拡張。

### CUP3 page の見た目
- 現状 page 1 (ウラ) の sprite state をそのまま使い回している。独自のカップ名 / 背景を出したいなら `features/cup_page3/files/` に TPL を追加 + `Sprite_SetupAnim` の 引数 ID を新規割当。
- TPL 追加は Riivolution `<file create="true"/>` + 既存アセットテーブルへの挿入が必要。`tools/resource_table_dump.py` の出力でどの ID が空いているか確認可能。
