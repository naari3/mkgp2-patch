#ifndef CUSTOM_ASSETS_H
#define CUSTOM_ASSETS_H

// Custom resource asset injection.
//
// Layout-compatible with vanilla ResourceEntry (see Ghidra ResourceEntry @
// kResourceTableMain 0x80422208 / kResourceTableExt 0x8048da08). Custom IDs
// >= CUSTOM_ID_BASE are served from kCustomResourceTable; lower IDs fall
// through to vanilla main/ext tables.

typedef unsigned short u16;
typedef signed short   s16;
typedef unsigned char  u8;
typedef unsigned int   u32;

struct CustomResourceEntry {
    u16   self_id;      // custom resource id (>= CUSTOM_ID_BASE)
    u16   pad_02;
    float offset_x;     // +0x04
    float offset_y;     // +0x08
    float size_x;       // +0x0C
    float size_y;       // +0x10
    s16   slot_index;   // +0x14
    u16   group_key;    // +0x16 — u16 (NOT s16). Custom gks live in the
                        //         sign-safe range 0x4000..0x7FFF, so even with s16
                        //         they would not negate, but u16 keeps the type
                        //         contract consistent with the file-path table
                        //         indexing (groupKey - CUSTOM_GROUPKEY_BASE).
    s16   next_id;      // +0x18  (-1 = chain terminator)
    u16   pad_1a;
    float scale_x;      // +0x1C
    float scale_y;      // +0x20
    u8    flags;        // +0x24
    u8    pad_tail[3];
};

// Vanilla resource id remapper. Applied in every getter hook before lookup.
// cup_id = -1 acts as a wildcard; otherwise matched against the *effective*
// cup id (= g_customCupScope when active, else g_cupId).
struct CupBinding {
    s16 cupId;          // -1 = wildcard
    u16 fromId;         // vanilla resource id to intercept
    u16 toId;           // replacement id (may be >= CUSTOM_ID_BASE)
    u16 pad;
};

// Custom-cup alias map. Drives the round-select g_cupId swap so vanilla
// cupId-indexed tables (DAT_8049af8c etc) read in-bounds when our custom
// cup is active. Source: cups.yaml display_alias_cup field.
struct CupAliasEntry {
    u8 customCupId;        // our cupId (>= 17)
    u8 aliasVanillaCupId;  // vanilla cup whose tables to mimic (0..7)
    u8 pad[2];
};

// Per-round thumb resource id injection.
// vanilla の DAT_8049aea0 起点の per-cup 16 byte slot に上書きする 8 u16。
// レイアウトは vanilla と同じく [round0_sq, round0_vert, round1_sq, round1_vert,
// round2_sq, round2_vert, round3_sq, round3_vert] (FUN_801c9288 の
// iVar5 = sub_index*16 + roundIdx*4 indexing と一致)。yaml で round 2/3 が
// 未定義なら round 0/1 の duplicate を入れる (Yoshi vanilla pattern)。
// PreInit で書き込み、PreDtor で original を restore する。
//
// nRounds: yaml で実際に定義された round 数 (1..4)。RoundIsUnlocked_Wrapper
// が「round 0..(nRounds-2) → cleared、round nRounds-1 → current」として
// 用意されている round 全てを選択可能にするのに使う。
struct RoundThumbInject {
    u8  customCupId;       // 対象 cup_id (>= 17)
    u8  nRounds;           // yaml で定義された round 数 (1..4)
    u8  pad[2];
    u16 thumbIds[8];       // vanilla cup slot に書き込む 8 u16
};

extern "C" {
    extern const CustomResourceEntry kCustomResourceTable[];
    extern const unsigned int        kCustomResourceCount;
    extern const CupBinding          kBindings[];
    extern const unsigned int        kBindingCount;
    // kCustomPathTable[i] = TPL filename for groupKey (CUSTOM_GROUPKEY_BASE + i),
    // or NULL for gaps. Indexed range is [0, kCustomPathCount).
    extern const char* const         kCustomPathTable[];
    extern const unsigned int        kCustomPathCount;
    extern const CupAliasEntry       kCupAliasMap[];
    extern const unsigned int        kCupAliasMapCount;
    extern const RoundThumbInject    kRoundThumbInjects[];
    extern const unsigned int        kRoundThumbInjectCount;

    // Active custom cupId during scenes that needed a g_cupId swap to keep
    // vanilla in-bounds (e.g. round-select). 0 = inactive. ApplyBinding gates
    // on this when non-zero so bindings still match the player's real cup.
    extern volatile int              g_customCupScope;

    const CustomResourceEntry* CustomResource_Lookup(int resourceId);
    // Returns the alias vanilla cupId for a given custom cupId, or -1 if
    // the cupId is not a custom one.
    int CustomCup_LookupAlias(int customCupId);
}

// 0x4000 を選ぶ理由: vanilla 未使用域 (vanilla main = 0..0x2AFF, extended =
// 0x2B00..0x2B03) かつ signed 16-bit 範囲 (< 0x8000) に収まる。後者が肝で、
// vanilla の Sprite_SetAnimParam(sprite, paramId, short value) 等で resource id
// を short として扱う API があり、id の high bit が立つと sign-extend で
// 0xFFFFxxxx になって slot[0] (= resourceId, full int) の lookup でミスする。
// 0x4xxx 系は high bit clear なので sign-safe。
static const int CUSTOM_ID_BASE       = 0x4000;
static const int CUSTOM_GROUPKEY_BASE = 0x4000;

#endif
