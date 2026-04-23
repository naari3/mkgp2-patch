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
    s16   slot_index;
    u16   group_key;    // read as u16 for consistency with CustomResourceEntry.
                        // vanilla values are all < 0x8000 so reinterpretation is
                        // identical to the original s16.
    s16   next_id;
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
// features/cups.yaml — bindings auto-derived from cup.cup_id +
// cup.display_alias_cup).

// Externals via externals.txt (g_cupId=0x806cf108).
extern "C" unsigned int g_cupId;
extern "C" int PreloadResource(int resourceId);   // 0x80120d80

// One-shot manual preload: when g_cupId first becomes 17, explicitly
// PreloadResource() each vanilla id we bind. This forces the resource slot
// registry to load+register our custom TPLs (with our group_key 0x900x), so
// downstream UV-only refresh paths (e.g. FUN_8011d890) can find them on
// every subsequent frame.
//
// Why needed: per-frame UV refresh paths skip slot creation on miss; only
// SetResource / FUN_8011fb2c paths trigger PreloadResource. Without manual
// pre-touch our 4 custom slots only get created during cup-confirm
// transition (briefly visible) and are released when the transition handle
// dies. Manual preload pins them across the cup-select page lifetime.
static bool s_customPreloaded = false;
static bool s_inCustomPreload = false;

static void TryPreloadCustomAssetsAtCup17() {
    if (s_customPreloaded || s_inCustomPreload) return;
    if ((int)g_cupId != 17) return;
    s_inCustomPreload = true;
    DebugPrintfSafe("MKGP2: ===== manual preload at cup=17 =====\n");
    for (unsigned int i = 0; i < kBindingCount; ++i) {
        const CupBinding& b = kBindings[i];
        if ((int)b.cupId != 17) continue;
        int ret = PreloadResource((int)(u16)b.fromId);
        DebugPrintfSafe("MKGP2:   PreloadResource(0x%04x) = %d\n",
                        (int)(u16)b.fromId, ret);
    }
    s_inCustomPreload = false;
    s_customPreloaded = true;
    // Verify by scanning slot registry. Print: total populated count, all
    // populated slots, and dedicated check for our custom group_keys.
    int* slots = (int*)0x806573e8;
    int populated = 0;
    int customFound = 0;
    DebugPrintfSafe("MKGP2: --- slot registry dump (gk != -1) ---\n");
    for (int i = 0; i < 600; ++i) {
        int* slot = slots + i * 7;
        int resId = slot[0];
        int gk = slot[1];
        int dataPtr = slot[2];
        if (gk != -1) {
            populated++;
            // low 16 bits match our custom range? handles both positive int
            // 0x9000 and sign-extended -28672 (0xFFFF9000) variants.
            bool isCustom = ((unsigned int)gk & 0xFFFF) >= 0x9000u &&
                            ((unsigned int)gk & 0xFFFF) <= 0x9100u;
            if (populated <= 20 || isCustom) {
                DebugPrintfSafe("MKGP2:   slot[%d] resId=0x%04x gk=0x%08x data=%p\n",
                                i, resId, gk, (void*)dataPtr);
            }
            if (isCustom) customFound++;
        }
    }
    DebugPrintfSafe("MKGP2: total populated=%d, custom=%d\n", populated, customFound);
}

// Per-pair (from, cup) fire counter. Tracks first-seen + total count so we
// can distinguish "binding fires every frame for all IDs" (downstream issue)
// from "some IDs fire only once" (caching / early-return per FontHandle).
// Cap at 64 unique entries.
static const int kBindingFireMax = 64;
struct BindingFireRecord { u16 from; s16 cup; u32 count; };
static BindingFireRecord s_bindingFires[kBindingFireMax];
static int s_bindingFireCount = 0;
static u32 s_bindingFireTotal = 0;
static u32 s_bindingFireNextReport = 1000;

// --- Diagnostic: log ALL resource ids queried while g_cupId == 17, dedup'd. ---
// Helps identify which vanilla ids drive the page-3 UI but aren't yet bound.
// Tracks up to 64 unique ids per getter family.
static const int kIdLogMax = 64;
static u16 s_seenIds_GroupKey[kIdLogMax];
static int s_seenIdsCount_GroupKey = 0;
static u16 s_seenIds_FilePath[kIdLogMax];
static int s_seenIdsCount_FilePath = 0;

static inline bool LogQueriedIdOnce(u16 id, u16* set, int& count, const char* tag) {
    if ((int)g_cupId != 17) return false;
    for (int i = 0; i < count; ++i) {
        if (set[i] == id) return false;
    }
    if (count >= kIdLogMax) return false;
    set[count++] = id;
    DebugPrintfSafe("MKGP2: cup17 %s queries 0x%04x (#%d)\n",
                    tag, (int)id, count);
    return true;
}

static inline int ApplyBinding(int resourceId) {
    if (kBindingCount == 0) return resourceId;
    TryPreloadCustomAssetsAtCup17();
    int cup = (int)g_cupId;
    for (unsigned int i = 0; i < kBindingCount; ++i) {
        const CupBinding& b = kBindings[i];
        if ((b.cupId == -1 || (int)b.cupId == cup) &&
            (int)(u16)b.fromId == resourceId) {
            int bound = (int)(u16)b.toId;
            // Per-pair counter
            int pairIdx = -1;
            for (int j = 0; j < s_bindingFireCount; ++j) {
                if (s_bindingFires[j].from == (u16)resourceId &&
                    s_bindingFires[j].cup  == (s16)cup) {
                    pairIdx = j; break;
                }
            }
            if (pairIdx == -1 && s_bindingFireCount < kBindingFireMax) {
                pairIdx = s_bindingFireCount++;
                s_bindingFires[pairIdx].from = (u16)resourceId;
                s_bindingFires[pairIdx].cup  = (s16)cup;
                s_bindingFires[pairIdx].count = 0;
                DebugPrintfSafe("MKGP2: first fire: 0x%04x -> 0x%04x (cup=%d)\n",
                                resourceId, bound, cup);
            }
            if (pairIdx >= 0) s_bindingFires[pairIdx].count++;

            s_bindingFireTotal++;
            if (s_bindingFireTotal >= s_bindingFireNextReport) {
                s_bindingFireNextReport += 2000;
                // Scan resource slot registry for any slot whose group_key
                // falls in the custom range. Each slot = 28 bytes (7 dwords);
                // slot[0]=resourceId, slot[1]=groupKey, slot[2]=resourceDataPtr.
                int* slots = (int*)0x806573e8;
                int found = 0;
                for (int i = 0; i < 600; ++i) {
                    int* slot = slots + i * 7;
                    int gk = slot[1];
                    if (gk >= 0x9000 && gk <= 0x9100) {
                        DebugPrintfSafe("MKGP2: slot[%d] resId=0x%04x gk=0x%04x dataPtr=%p\n",
                                        i, slot[0], gk, (void*)slot[2]);
                        found++;
                    }
                }
                if (found == 0) {
                    DebugPrintfSafe("MKGP2: no custom slots registered (total=%u)\n",
                                    s_bindingFireTotal);
                }
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
    LogQueriedIdOnce((u16)resourceId, s_seenIds_GroupKey,
                     s_seenIdsCount_GroupKey, "GroupKey");
    resourceId = ApplyBinding(resourceId);
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return (int)(unsigned int)c->group_key;   // u16 -> unsigned widen
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return (int)(unsigned int)v->group_key;
    return 0;
}

// Resolve a groupKey to a filename. Custom groupKeys (>= CUSTOM_GROUPKEY_BASE)
// route to kCustomPathTable; everything else uses the vanilla table.
static inline char* ResolveFilePath(unsigned int groupKey) {
    if (groupKey >= (unsigned int)CUSTOM_GROUPKEY_BASE) {
        unsigned int idx = groupKey - (unsigned int)CUSTOM_GROUPKEY_BASE;
        if (idx < kCustomPathCount) {
            const char* p = kCustomPathTable[idx];
            if (p) return const_cast<char*>(p);
        }
        return 0;
    }
    return kResourcePathTable[groupKey];
}

extern "C" char* GetFilePathPtr_Hook(int resourceId) {
    EnsureDBATWidened();
    LogQueriedIdOnce((u16)resourceId, s_seenIds_FilePath,
                     s_seenIdsCount_FilePath, "FilePath");
    resourceId = ApplyBinding(resourceId);
    // Extended-range (>= 0x2B00) uses a separate direct-indexed table; custom
    // IDs (>= CUSTOM_ID_BASE = 0x9000) must route through the groupKey path.
    const CustomResourceEntry* c = CustomResource_Lookup(resourceId);
    if (c) return ResolveFilePath((u16)c->group_key);
    if (resourceId >= 0x2B00) {
        // Bounds guard — extended table is direct-indexed, caller guarantees
        // the id is valid. Out-of-range would read arbitrary memory.
        return kExtendedResourcePathTable[resourceId];
    }
    const VanillaResourceEntry* v = VanillaLookup(resourceId);
    if (v) return ResolveFilePath((u16)v->group_key);
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
// Generated from features/cups.yaml by gen_custom_assets_header.py.
// Included at the bottom so the definitions participate in this translation
// unit and satisfy the extern declarations in custom_assets.h.
#include "generated_custom_assets.h"
