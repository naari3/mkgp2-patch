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
    u16   group_key;    // +0x16 — u16 (NOT s16): custom gks >= 0x9000 must not
                        //         sign-extend into negative int. vanilla uses s16
                        //         here but all its values are < 0x8000, so reading
                        //         vanilla as u16 is identical.
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

    // Active custom cupId during scenes that needed a g_cupId swap to keep
    // vanilla in-bounds (e.g. round-select). 0 = inactive. ApplyBinding gates
    // on this when non-zero so bindings still match the player's real cup.
    extern volatile int              g_customCupScope;

    const CustomResourceEntry* CustomResource_Lookup(int resourceId);
    // Returns the alias vanilla cupId for a given custom cupId, or -1 if
    // the cupId is not a custom one.
    int CustomCup_LookupAlias(int customCupId);
}

static const int CUSTOM_ID_BASE       = 0x9000;
static const int CUSTOM_GROUPKEY_BASE = 0x9000;

#endif
