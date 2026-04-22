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
    s16   group_key;    // +0x16
    s16   next_id;      // +0x18  (-1 = chain terminator)
    u16   pad_1a;
    float scale_x;      // +0x1C
    float scale_y;      // +0x20
    u8    flags;        // +0x24
    u8    pad_tail[3];
};

// Vanilla resource id remapper. Applied in every getter hook before lookup.
// cup_id = -1 acts as a wildcard; otherwise matched against g_cupId.
struct CupBinding {
    s16 cupId;          // -1 = wildcard
    u16 fromId;         // vanilla resource id to intercept
    u16 toId;           // replacement id (may be >= CUSTOM_ID_BASE)
    u16 pad;
};

extern "C" {
    extern const CustomResourceEntry kCustomResourceTable[];
    extern const unsigned int        kCustomResourceCount;
    extern const CupBinding          kBindings[];
    extern const unsigned int        kBindingCount;

    const CustomResourceEntry* CustomResource_Lookup(int resourceId);
}

static const int CUSTOM_ID_BASE = 0x9000;

#endif
