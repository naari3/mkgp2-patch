# MKGP2 Kamek パッチ開発ガイド

MKGP2 (Mario Kart Arcade GP2, GNLJ82) への Kamek パッチ開発リポジトリ。
GitHub: https://github.com/naari3/mkgp2-patch。ビルド: `bash build.sh`。

---

## ワークフロー設計

### 1. 偵察してから動く

- コードを書く前に、変更対象とその周辺を必ず Read/Grep/Glob で確認する
- 読んでいないファイルを変更しない。これは絶対のルール
- 3ファイル以上に影響する変更は、先に import/export の依存を辿り影響範囲を洗い出す
- 偵察結果の報告や承認待ちは不要。把握できたらそのまま実装に入る
- 途中で想定外の構造に遭遇したら、書きかけのコードを放置してでも追加の偵察を行う
- 曖昧さはコードを読んで解消する。ユーザーに聞くのは、コードから判断できない場合の最終手段

### 2. サブエージェント戦略
- メインのコンテキストウィンドウをクリーンに保つためにサブエージェントを積極的に活用する
- リサーチ・調査・並列分析はサブエージェントに任せる
- 複雑な問題には、サブエージェントを使ってより多くの計算リソースを投入する
- 集中して実行するために、サブエージェント1つにつき1タスクを割り当てる

### 3. 自己改善ループ
- ユーザーから修正を受けたら必ず `tasks/lessons.md` にそのパターンを記録する
- 同じミスを繰り返さないように、自分へのルールを書く
- ミス率が下がるまで、ルールを徹底的に改善し続ける
- セッション開始時に、そのプロジェクトに関連するlessonsをレビューする

### 4. 完了前に必ず検証する
- 動作を証明できるまで、タスクを完了とマークしない
- 必要に応じてmainブランチと自分の変更の差分を確認する
- 「スタッフエンジニアはこれを承認するか？」と自問する
- テストを実行し、ログを確認し、正しく動作することを示す

### 5. エレガントさを追求する（バランスよく）
- 重要な変更をする前に「もっとエレガントな方法はないか？」と一度立ち止まる
- ハック的な修正に感じたら「今知っていることをすべて踏まえて、エレガントな解決策を実装する」
- シンプルで明白な修正にはこのプロセスをスキップする（過剰設計しない）
- 提示する前に自分の作業に自問自答する

### 6. 自律的なバグ修正
- バグレポートを受けたら、手取り足取り教えてもらわずにそのまま修正する
- ログ・エラー・失敗しているテストを見て、自分で解決する
- ユーザーのコンテキスト切り替えをゼロにする
- 言われなくても、失敗しているCIテストを修正しに行く

### 7. 調査は自主的に進める
- 「調査して良いか」「次の方針はこれで良いか」をユーザーに聞かない
- Ghidra MCP / Grep / decompile / log 解析等は、必要と判断したら即実行する
- 調査結果と仮説、それに基づく次の手は **報告ではなく実行** で示す
- 大規模なリファクタや破壊的変更を伴う場合のみ、事前に計画を提示する
- ユーザーへの問いは「コードから判断不能な仕様 / 設計判断 / 優先順位」に限定する

---

## Code Intelligence

コードナビゲーションには Grep/Read より LSP を優先する:
- `workspaceSymbol` で定義箇所を検索
- `findReferences` でコードベース全体の使用箇所を確認
- `goToDefinition` / `goToImplementation` でソースにジャンプ
- `hover` でファイルを開かずに型情報を取得

Grep は LSP が使えない場合やテキスト/パターン検索にのみ使用する。
コードの作成・編集後は LSP diagnostics を確認し、エラーがあれば先に修正する。

---

## タスク管理

1. **まず計画を立てる**：チェック可能な項目として `tasks/todo.md` に計画を書く
2. **計画を確認する**：実装を開始する前に確認する
3. **進捗を記録する**：完了した項目を随時マークしていく
4. **変更を説明する**：各ステップで高レベルのサマリーを提供する
5. **結果をドキュメント化する**：`tasks/todo.md` にレビューセクションを追加する
6. **学びを記録する**：修正を受けた後に `tasks/lessons.md` を更新する

---

## コア原則

- **シンプル第一**：すべての変更をできる限りシンプルにする。影響するコードを最小限にする。
- **手を抜かない**：根本原因を見つける。一時的な修正は避ける。シニアエンジニアの水準を保つ。
- **影響を最小化する**：変更は必要な箇所のみにとどめる。バグを新たに引き込まない。

---

## 関連リソース

- **解析ドキュメント**: `~/src/github.com/dolphin-emu/dolphin/mkgp2docs/` — 関数アドレス・構造体レイアウト・HSD コース format 等の調査結果。新しい hook を設計する前に必ず参照。
- **ライブメモリビューワ**: `~/src/github.com/naari3/mkgp2-view/` — Dolphin プロセスからアドレスを実時間読み取り。パッチ動作のデバッグに使用。MCPサーバー (`read_u32`, `get_game_state` 等) も提供。
- **ISO 展開結果**: `C:\Users\naari\Documents\Dolphin ROMs\Triforce\mkgp2\files\` — コースモデル・コリジョン・テクスチャ等の全リソースが Dolphin dump + extract 済み。ファイル名命名規則の実物確認・没データ (test_course 等) 存在確認に使用。詳細は `mkgp2docs/mkgp2_iso_dump_location.md`。

## 関連MCP

- Ghidraによる解析を行う場合 /ghidra-mcp スキルのロードを激しく推奨する。

## 必須のセットアップ (MKGP2特有のハマりどころ)

### 1. DBAT0 拡張

MKGP2 の `__start` は DBAT0 を ~32MB しか張らない (IBAT0 は広い)。結果、パッチ領域 (0x806EDxxx 等) の**コードは実行できるがデータ読み書きが失敗する**。HLE や MMU の Host 系アクセスも全滅する。

対策: 各機能のエントリで `EnsureDBATWidened()` (in `common/patch_common.h`) を呼ぶ。内部でフラグを見て一度だけ `WidenDBAT0_256M` を実行する。

```cpp
// features/xxx/xxx.cpp
#include "patch_common.h"
void MyFeatureEntry() {
    EnsureDBATWidened();  // idempotent
    ...
}
```

Dolphin 側では `mtspr DBAT*` で `UpdateBATs` / `DBATUpdated` がトリガされ JIT キャッシュが無効化される。問題なし。

### 2. ArenaLo 引き上げ

`ArenaLo (0x80000030)` はゲームのヒープ開始位置。デフォルトではパッチ領域を上書きする。Riivolution の `<memory>` パッチで一度だけ書き込む。

```cpp
// common/patch_common.cpp
kmWrite32(0x80000030, 0x806EF000);  // パッチ bin 末尾より上に設定
```

パッチ bin サイズが変わったら再計算すること (`patch_map.md` で bin サイズ確認可)。

### 3. DebugPrintf HLE ヒューリスティック回避

Dolphin の `HLE_GeneralDebugPrint` は `r3` が RAM アドレスで、かつ `*r3` も RAM アドレス (または NULL) なら `r3` を `this` ポインタと誤判定し、書式文字列を `r4` から読む。**パッチ領域の書式文字列が "MKGP" (0x4D4B4750) で始まると、PPC セグメントレジスタの identity mapping 経由で `HostIsRAMAddress(0x4D4B4750)` が true を返し、ヒューリスティック誤爆**。結果、書式が読めず空文字列または文字化け。

**Dolphin 側を修正してはいけない** (ゲーム都合を emu に押し付けるため)。代わりに `common/patch_common.cpp` の `DebugPrintfSafe` (レジスタを1つシフトして r3=0 にする asm ラッパー) を使う:

```cpp
// features/xxx/xxx.cpp
#include "patch_common.h"
DebugPrintfSafe("MKGP2: value=%d\n", x);
```

`DebugPrintf` (元のゲームシンボル) も `patch_common.h` で extern 宣言済み。既存ゲームコードの `DebugPrintf` 呼び出しには影響なし (0x4D4B4750 始まりの書式文字列がそもそも存在しないため)。

## externals.txt 検証

`externals.txt` は Ghidra アドレスの手書き転記で、古い/誤った値が紛れる。新しいシンボルを追加する前、および疑わしい動作を見た際は **Ghidra の `list_globals` / `decompile_function` で必ず検証**する。

実績: `g_cupId` (旧 `g_courseId`) が `0x806d1264` (誤) のまま残っていた。正しくは `0x806cf108`。検証手順:

1. `mcp__ghidra__list_globals(filter="シンボル名", program="main.dol")` でアドレス取得
2. 参照先関数を decompile してそのアドレスが意図どおり使われているか確認
3. `mkgp2-view/src/dolphin.rs` の `addr::*` 定数とも照合 (こちらが運用中のため最新)

## デバッグログの確認

Dolphin の `HLE` / `OSREPORT_HLE` ログチャンネルが有効か確認 (デフォルトで無効)。`User\Logs\dolphin.log` を `tail`/`grep` してパッチ出力を確認する。

## プロジェクト構成

```
mkgp2-patch/
├── build.sh              # 自動discover: common/*.cpp + features/*/*.cpp
├── externals.txt         # 共有シンボル (Ghidraで検証すること)
├── common/
│   ├── patch_common.{cpp,h}  # EnsureDBATWidened, DebugPrintfSafe, ArenaLo等
├── features/
│   └── joint_extend/
│       ├── joint_extend.cpp
│       ├── course_joints.yaml
│       ├── gen_joints_header.py   # course_joints.yaml → generated_joints.h
│       └── gen_mod_yaml.py
├── tools/
│   └── gen_patch_map.py  # 全kmBranch/kmWriteスキャン → patch_map.md
└── patch_map.md          # 自動生成 (gitignore)
```

### 新機能追加フロー

1. `features/my_feature/` を作成
2. `.cpp` を1つ以上追加 (`#include "patch_common.h"` でインフラ呼べる)
3. データが必要なら `gen_*.py` も同ディレクトリに置く (build.sh が自動実行)
4. `bash build.sh` 実行 — `SOURCES=()` に自動追加、`patch_map.md` に hook が載る

`build.sh` を編集する必要はない。

### ビルド出力

- `joint_extend.bin` — Kamek パッチバイナリ
- `joint_extend.xml` — Riivolution 用ラップ済み XML (Dolphin の Load/Riivolution/riivolution/ にコピー済み)
- `joint_extend_gecko.txt` — Gecko code 形式
- `patch_map.md` — 全 hook の一覧 (feature 別)

### 参考: Newer-Team/NewerSMBW

`ghq get Newer-Team/NewerSMBW` で `~/src/github.com/Newer-Team/NewerSMBW/` にクローン。**旧 Python Kamek** ベースなので toolchain は違うが、yaml駆動の hook 管理・~100モジュール構成は大規模化時の参考になる。

うちの方針 (新 Kamek.exe): yaml indirection は避け、`kmBranch` マクロをソースに直書き + feature-per-directory + `tools/gen_patch_map.py` で機械抽出。yaml 方式より:
- hook がコードと同じファイル → IDE 補完/refactor/grep が効く
- コンパイラが型チェック
- yaml→code のジェネレータ不要
