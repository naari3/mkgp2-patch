#include <kamek.h>
#include "patch_common.h"
#include "custom_assets.h"

// PoC phase A: hook the 6 ResourceEntry getters clustered at
// 0x801223e8..0x80122808. Custom IDs (>= 0x9000) are served from
// kCustomResourceTable[]. IDs < 0x9000 fall through to vanilla
// kResourceTableMain[11008] @ 0x80422208 and kResourceTableExt[4] @ 0x8048da08
// by reimplementing the vanilla lookup (cache is NOT touched to avoid
// corrupting vanilla's shared last-hit slot).
//
// Binding layer (Layer 1): kBindings[] remaps vanilla resource IDs to other
// IDs (vanilla or custom) before lookup. Each binding is optionally filtered
// by g_cupId (-1 = wildcard / apply always). Applied first in every getter.
//
// Getter semantics (from Ghidra plate comments — siblings share lookup,
// differ only in returned field):
//
//   0x80122730 GetOffsetXY    void(id, *ox, *oy)  offset_x/y  miss (0,0)
//   0x80122658 GetSizeXY      void(id, *sx, *sy)  size_x/y    miss (1,1)
//   0x801224b4 GetScaleXY     void(id, *sx, *sy)  scale_x/y   miss (1,1)
//   0x801223e8 GetFlagsByte      int(id)             flags byte     miss 4
//   0x8012258c GetChainNextId    int(id)             nextId         miss -1
//   0x80122808 GetSlotIndex      int(id)             slotIndex      miss -1
//   0x80122ac4 GetGroupKey       int(id)             groupKey       miss 0
//   0x801229c4 GetFilePathPtr    char*(id)           filename ptr   miss NULL

// Vanilla resource entry layout (40 bytes) — mirrors CustomResourceEntry.
struct VanillaResourceEntry {
    u16   self_id, pad_02;
    float offset_x, offset_y;
    float size_x, size_y;
    s16   slot_index, group_key, next_id;
    u16   pad_1a;
    float scale_x, scale_y;
    u8    flags, pad_tail[3];
};

static VanillaResourceEntry* const kResourceTableMain =
    (VanillaResourceEntry*)0x80422208;
static VanillaResourceEntry* const kResourceTableExt  =
    (VanillaResourceEntry*)0x8048da08;
static const int kResourceTableMainCount = 0x2B00;   // IDs 0..0x2AFF
static const int kResourceTableExtCount  = 4;        // IDs 0x2B00..0x2B03

// Filename pointer tables (indexed by groupKey for id<0x2b00, by raw id for
// extended). vanilla PTR_s_adjust_tpl_80350508 / s_tpl2_sysFONT_..._8034a418.
extern "C" {
    extern char* const kResourcePathTable[];            // @ 0x80350508
    extern char* const kExtendedResourcePathTable[];    // @ 0x8034a418 (indexed by raw id)
}

// --------- Binding layer (vanilla ID → other ID remap) -------------------
// CupBinding struct + kBindings[] / kBindingCount come from
// generated_custom_assets.h (emitted by gen_custom_assets_header.py from
// bindings/*.yaml).

// Externals via externals.txt (g_cupId=0x806cf108).
extern "C" unsigned int g_cupId;

static int s_bindingLogCount = 0;
static const int kBindingLogMax = 5;

static inline int ApplyBinding(int resourceId) {
    if (kBindingCount == 0) return resourceId;
    int cup = (int)g_cupId;
    for (unsigned int i = 0; i < kBindingCount; ++i) {
        const CupBinding& b = kBindings[i];
        if ((b.cupId == -1 || (int)b.cupId == cup) &&
            (int)(u16)b.fromId == resourceId) {
            int bound = (int)(u16)b.toId;
            if (s_bindingLogCount < kBindingLogMax) {
                s_bindingLogCount++;
                DebugPrintfSafe("MKGP2: binding fire #%d: 0x%04x -> 0x%04x (cup=%d)\n",
                                s_bindingLogCount, resourceId, bound, cup);
            }
            return bound;
        }
    }
    return resourceId;
}

// Linear scan over CustomResourceEntry[] for resourceId. PoC size is 1 entry;
// later we can sort + bsearch or hash, but for < ~64 entries linear is fine.
const CustomResourceEntry* CustomResource_Lookup(int resourceId) {
    if (resourceId < CUSTOM_ID_BASE) return 0;
    for (u32 i = 0; i < kCustomResourceCount; ++i) {
        if ((int)kCustomResourceTable[i].self_id == resourceId)
            return &kCustomResourceTable[i];
    }
    return 0;
}

// Vanilla-compatible lookup helper. Returns NULL if resourceId has no entry
// in either table. Does NOT touch cache.
static const VanillaResourceEntry* VanillaLookup(int resourceId) {
    if ((unsigned)resourceId < (unsigned)kResourceTableMainCount) {
        return &kResourceTableMain[resourceId];
    }
    for (int i = 0; i < kResourceTableExtCount; ++i) {
        if ((int)kResourceTableExt[i].self_id == resourceId)
            return &kResourceTableExt[i];
    }
    return 0;
}

// --------- PoC diagnostic: once-at-first-call logger --------------------
static bool s_pocTested = false;

static void RunPoC() {
    if (s_pocTested) return;
    s_pocTested = true;

    const int testId = CUSTOM_ID_BASE;
    const CustomResourceEntry* e = CustomResource_Lookup(testId);
    if (!e) {
        DebugPrintfSafe("MKGP2: custom_assets PoC id=0x%04x NOT FOUND in kCustomResourceTable\n",
                        testId);
        return;
    }
    DebugPrintfSafe("MKGP2: custom_assets PoC id=0x%04x FOUND\n", testId);
    DebugPrintfSafe("MKGP2:   flags=%d slot=%d next=%d group=%d\n",
                    (int)e->flags, (int)e->slot_index,
                    (int)e->next_id, (int)e->group_key);
    DebugPrintfSafe("MKGP2:   size=(%g,%g) scale=(%g,%g) offset=(%g,%g)\n",
                    e->size_x, e->size_y, e->scale_x, e->scale_y,
                    e->offset_x, e->offset_y);
}

// --------- 6 getter hooks -----------------------------------------------

extern "C" void GetOffsetXY_Hook(int resourceId, float* pX, float* pY) {
    EnsureDBATWidened();
    RunPoC();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) { *pX = c->offset_x; *pY = c->offset_y; return; }
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) { *pX = v->offset_x; *pY = v->offset_y; return; }
    *pX = 0.0f; *pY = 0.0f;
}

extern "C" void GetSizeXY_Hook(int resourceId, float* pX, float* pY) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) { *pX = c->size_x; *pY = c->size_y; return; }
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) { *pX = v->size_x; *pY = v->size_y; return; }
    *pX = 1.0f; *pY = 1.0f;
}

extern "C" void GetScaleXY_Hook(int resourceId, float* pX, float* pY) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) { *pX = c->scale_x; *pY = c->scale_y; return; }
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) { *pX = v->scale_x; *pY = v->scale_y; return; }
    *pX = 1.0f; *pY = 1.0f;
}

extern "C" int GetFlagsByte_Hook(int resourceId) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return (int)(unsigned)c->flags;
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return (int)(unsigned)v->flags;
    return 4;
}

extern "C" int GetChainNextId_Hook(int resourceId) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return (int)c->next_id;
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return (int)v->next_id;
    return -1;
}

extern "C" int GetSlotIndex_Hook(int resourceId) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return (int)c->slot_index;
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return (int)v->slot_index;
    return -1;
}

extern "C" int GetGroupKey_Hook(int resourceId) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return (int)c->group_key;
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return (int)v->group_key;
    return 0;
}

extern "C" char* GetFilePathPtr_Hook(int resourceId) {
    EnsureDBATWidened();
    resourceId = ApplyBinding(resourceId);
    // Extended-range (>= 0x2B00) uses a separate direct-indexed table; custom
    // IDs (>= CUSTOM_ID_BASE = 0x9000) must route through the groupKey path.
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return kResourcePathTable[(u16)c->group_key];
    if (resourceId >= 0x2B00) {
        // Bounds guard — extended table is direct-indexed, caller guarantees
        // the id is valid. Out-of-range would read arbitrary memory.
        return kExtendedResourcePathTable[resourceId];
    }
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return kResourcePathTable[(u16)v->group_key];
    return 0;
}

// --------- Hook installation --------------------------------------------

kmBranch(0x80122730, GetOffsetXY_Hook);
kmBranch(0x80122658, GetSizeXY_Hook);
kmBranch(0x801224b4, GetScaleXY_Hook);
kmBranch(0x801223e8, GetFlagsByte_Hook);
kmBranch(0x8012258c, GetChainNextId_Hook);
kmBranch(0x80122808, GetSlotIndex_Hook);
kmBranch(0x80122ac4, GetGroupKey_Hook);
kmBranch(0x801229c4, GetFilePathPtr_Hook);

// --------- Data tables: kCustomResourceTable[] + kBindings[] ---------------
// Generated from assets.yaml + bindings/*.yaml by gen_custom_assets_header.py.
// Included at the bottom so the definitions participate in this translation
// unit and satisfy the extern declarations in custom_assets.h.
#include "generated_custom_assets.h"
