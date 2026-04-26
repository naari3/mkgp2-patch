#include <kamek.h>
#include "patch_common.h"

// Debug overlay: walks g_SpriteHandlePool every frame and renders an on-screen
// HUD listing each active+visible slot's (resourceId, scaleX/Y, atlas filename).
//
// Phase 1 (current): HUD list via DrawText. No per-sprite rect outlines yet.
// Phase 2 (future):  per-sprite rectangle outline at slot.vertCoords[8].
//
// Hook strategy: kmCall on the BL site at 0x8002c678 inside MainGameLoop
// (the real per-frame loop, formerly FUN_8002c5e8 — RunGameMain calls it
// after BootDispatcher finishes its boot/card-task phase). Just before
// this BL the scene-draw thunk runs, so by hook time all UI sprites for
// the frame are already in the pool. We call the original
// SpriteHandlePool_GC first to preserve game behavior, then walk the pool
// for overlay output.
//
// Note: an earlier iteration hooked 0x8002e4ec (a BL inside the now-renamed
// BootDispatcher's card-task-pending loop) which only fires for ~3 boot
// frames before BootDispatcher returns. See lessons.md for the full story.

typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;

// SpriteHandleSlot mirrors the 228-byte struct defined in Ghidra
// (verified via mcp__ghidra__get_struct_layout). Only the fields we read
// are named; the rest are reserved padding.
struct SpriteHandleSlot {
    u8    header[12];      // 0x00
    void* resourcePtr;     // 0x0C
    u32   resourceId;      // 0x10
    u8    reserved_14[16]; // 0x14
    u8    alignMode;       // 0x24
    u8    reserved_25[3];  // 0x25
    float originX;         // 0x28
    float originY;         // 0x2C
    u8    reserved_30[4];  // 0x30
    float rotation;        // 0x34
    float scaleX;          // 0x38
    float scaleY;          // 0x3C
    float colorMul0;       // 0x40
    float colorMul1;       // 0x44
    u8    flipFlags;       // 0x48
    u8    shapeOverride;   // 0x49
    u8    reserved_4A[2];  // 0x4A
    float affine[6];       // 0x4C
    u32   vertColor[4];    // 0x64
    u8    reserved_74[20]; // 0x74
    float vertCoords[8];   // 0x88  — 4 (x,y) corner pairs, screen-space
    u8    reserved_A8[36]; // 0xA8..0xCB
    u8    reserved_CC[12]; // 0xCC..0xD7 (struct hole — Ghidra layout has no
                           //              named fields for these 12 bytes;
                           //              keep explicit so activeFlag lands
                           //              at the correct 0xD8 offset.)
    u8    activeFlag;      // 0xD8
    u8    visibleFlag;     // 0xD9
    u8    dirtyFlag;       // 0xDA
    u8    reserved_DB[9];  // 0xDB
};

extern "C" {
    extern SpriteHandleSlot g_SpriteHandlePool[500]; // 0x8065bee8

    void* Alloc(int size);                          // 0x8003b1fc
    void  DisplayContext_Init(void* ctx);            // 0x801db710
    void  DisplayContext_Flush(void* ctx);           // 0x801db278
    void  DrawText(double scale, void* ctx, int x, int y, int color,
                   const char* fmt, ...);            // 0x801db548
    int   ResourceTable_GetGroupKey(int resourceId); // 0x80122ac4
    extern char* const kResourcePathTable[];         // 0x80350508

    // SpriteHandlePool_GC — sprite-pool garbage collector (formerly
    // FUN_80121120). We hook a BL site that calls it; this declaration
    // lets the hook forward to the original.
    void SpriteHandlePool_GC(void);

    // Vanilla "draw an untextured colored filled quad in screen space"
    // entrypoint at 0x801526c4. Self-contained: does its own full GX prologue
    // (viewport, ortho proj, identity model mtx, TEV preset 4 = raster-only,
    // POS=F32 vtxFmt) and emits one GX_QUADS with the supplied RGBA. Phase 2B
    // calls this 4 times per sprite to draw a 1px-thick rect outline (top /
    // bottom / left / right edges as separate thin filled rects). This avoids
    // the whole GX-LINES + state-save/restore complication entirely — vanilla
    // never emits non-QUAD primitives so QUADS is the only safe path.
    void DrawColoredQuad(double x, double y, double w, double h,
                         unsigned char r, unsigned char g,
                         unsigned char b, unsigned char a);
}

// custom_assets exposes its own custom path table for groupKey >= 0x4000.
// We pull it in directly — custom_assets is always linked alongside this
// feature in the current build.
extern "C" {
    extern const char* const kCustomPathTable[];
    extern const unsigned int kCustomPathCount;
}


// --- runtime state ------------------------------------------------------

static void* s_dbgCtx        = 0;     // self-allocated DisplayContext
static int   s_dbgCtxAllocFailedOnce = 0;

// Master toggle. Default on for development. Pokeable from Dolphin if needed.
volatile int g_dbgOverlayEnabled = 1;

// Display mode. Default = compact summary (just a count).
//   0 = summary line only (active / visible / pool counts)
//   1 = full per-slot list (id / size / pos / filename), capped at kHudListMax
//   2 = per-sprite id label at each visible sprite's top-left corner
//   3 = green 1px rect outline around each visible sprite + per-sprite id label
//   4 = single magenta 100x30 filled bar at screen center (DrawColoredQuad
//       smoke test — validates the vanilla helper before mode 3 is trusted)
// Pokeable from Dolphin to switch live.
volatile int g_dbgOverlayMode = 0;

// --- helpers ------------------------------------------------------------

static const char* ResolveFilename(int resourceId) {
    int gk = ResourceTable_GetGroupKey(resourceId);
    if (gk == 0) return 0;
    if ((unsigned)gk >= 0x4000u) {
        // Custom range — owned by custom_assets feature.
        unsigned idx = (unsigned)gk - 0x4000u;
        if (idx < kCustomPathCount) return kCustomPathTable[idx];
        return 0;
    }
    return kResourcePathTable[gk];
}

static void EnsureDisplayContext() {
    if (s_dbgCtx || s_dbgCtxAllocFailedOnce) return;
    void* p = Alloc(0xd8);
    if (!p) { s_dbgCtxAllocFailedOnce = 1; return; }
    DisplayContext_Init(p);
    s_dbgCtx = p;
}

// --- Rect outline rendering (Phase 2B) ----------------------------------
//
// DrawDebugRect outlines a (x, y, w, h) screen-space box by emitting four
// thin (1px) filled quads via the vanilla DrawColoredQuad helper. Each call
// runs DrawColoredQuad's full prologue (viewport / ortho / TEV preset / vtx
// fmt) so we don't share state across edges; that's still cheap because the
// state shadow is cached and dirty-bit-flushed only on the next GX_Begin.
//
// We keep edges 1px-thick to maximize visual clarity at scale 0.5 text font.

static void DrawDebugRect(int x, int y, int w, int h,
                          unsigned char r, unsigned char g,
                          unsigned char b, unsigned char a) {
    if (w <= 0 || h <= 0) return;
    const int t = 1;
    DrawColoredQuad((double)x,         (double)y,         (double)w, (double)t, r, g, b, a); // top
    DrawColoredQuad((double)x,         (double)(y + h - t),(double)w, (double)t, r, g, b, a); // bottom
    DrawColoredQuad((double)x,         (double)y,         (double)t, (double)h, r, g, b, a); // left
    DrawColoredQuad((double)(x + w - t),(double)y,         (double)t, (double)h, r, g, b, a); // right
}

// AABB of the 4 corner (x,y) pairs in vertCoords[].
struct Aabb { float minX, minY, maxX, maxY; };
static Aabb ComputeAabb(const float* v) {
    Aabb a;
    a.minX = a.maxX = v[0];
    a.minY = a.maxY = v[1];
    for (int i = 1; i < 4; ++i) {
        float x = v[i*2 + 0];
        float y = v[i*2 + 1];
        if (x < a.minX) a.minX = x;
        if (x > a.maxX) a.maxX = x;
        if (y < a.minY) a.minY = y;
        if (y > a.maxY) a.maxY = y;
    }
    return a;
}

// --- HUD rendering ------------------------------------------------------

// Default scale 0.5 = compact text (vanilla DrawText callers use 1.0). Glyph
// width / advance shrink ~50% so a typical id-line fits in ~30 columns.
static const double kHudScale     = 0.5;
static const int    kHudLineHeight = 10;   // matches scale 0.5 (glyph ~16px nominal → ~8px advance)
static const int    kHudOriginX   = 8;
static const int    kHudOriginY   = 24;
static const int    kHudListMax   = 28;    // cap when full-list mode is on
// Per-sprite label cap. DisplayContext entry limit is 127 (one summary line
// is always shown), so leave headroom for vanilla glyph use elsewhere.
static const int    kPerSpriteLabelMax = 110;

static void RenderHud() {
    if (!s_dbgCtx) return;

    // Pool counters.
    int active = 0;
    int visible = 0;
    for (int i = 0; i < 500; ++i) {
        const SpriteHandleSlot& s = g_SpriteHandlePool[i];
        if (s.activeFlag) {
            ++active;
            if (s.visibleFlag) ++visible;
        }
    }

    // Header: always shown (compact summary).
    DrawText(kHudScale, s_dbgCtx, kHudOriginX, kHudOriginY, 7,
             "DBG: %d visible / %d active sprites", visible, active);

    // Optional full list (mode 1). Capped at kHudListMax to avoid screen
    // overflow and DrawText's per-frame entry cap (127).
    if (g_dbgOverlayMode == 1) {
        int line = 1;
        for (int i = 0; i < 500 && line <= kHudListMax; ++i) {
            const SpriteHandleSlot& s = g_SpriteHandlePool[i];
            if (!s.activeFlag || !s.visibleFlag) continue;
            const char* fname = ResolveFilename((int)s.resourceId);
            Aabb a = ComputeAabb(s.vertCoords);
            DrawText(kHudScale, s_dbgCtx,
                     kHudOriginX, kHudOriginY + line * kHudLineHeight, 7,
                     "%04x %dx%d @%d,%d %s",
                     (int)s.resourceId,
                     (int)s.scaleX, (int)s.scaleY,
                     (int)a.minX,   (int)a.minY,
                     fname ? fname : "(?)");
            ++line;
        }
    }
    // Per-sprite id label at the AABB top-left of each visible sprite (mode 2/3).
    // Lets us spatially correlate each rendered UI element with its resourceId
    // without the global HUD list eating screen real-estate. Capped at
    // kPerSpriteLabelMax to stay under the DisplayContext 127 entry budget.
    else if (g_dbgOverlayMode == 2 || g_dbgOverlayMode == 3) {
        int emitted = 0;
        for (int i = 0; i < 500 && emitted < kPerSpriteLabelMax; ++i) {
            const SpriteHandleSlot& s = g_SpriteHandlePool[i];
            if (!s.activeFlag || !s.visibleFlag) continue;
            Aabb a = ComputeAabb(s.vertCoords);
            // Skip degenerate / fully-offscreen rects.
            if (a.maxX <= 0.0f || a.maxY <= 0.0f) continue;
            if (a.minX >= 640.0f || a.minY >= 480.0f) continue;
            int x = (int)a.minX; if (x < 0) x = 0; if (x > 600) x = 600;
            int y = (int)a.minY; if (y < 0) y = 0; if (y > 472) y = 472;
            DrawText(kHudScale, s_dbgCtx, x, y, 7,
                     "%04x", (int)s.resourceId);
            ++emitted;
        }
    }

    DisplayContext_Flush(s_dbgCtx);

    // Filled-quad rect outlines (Phase 2B). Run AFTER the text Flush so the
    // outlines compose on top of text in the FIFO command stream.
    //   mode 3 = walk pool, outline each visible sprite (green, ~50% alpha)
    //   mode 4 = single magenta filled rect at screen center (smoke test)
    if (g_dbgOverlayMode == 3) {
        int emitted = 0;
        for (int i = 0; i < 500 && emitted < 80; ++i) {
            const SpriteHandleSlot& s = g_SpriteHandlePool[i];
            if (!s.activeFlag || !s.visibleFlag) continue;
            Aabb a = ComputeAabb(s.vertCoords);
            if (a.maxX <= 0.0f || a.maxY <= 0.0f) continue;
            if (a.minX >= 640.0f || a.minY >= 480.0f) continue;
            int rx = (int)a.minX; if (rx < 0) rx = 0;
            int ry = (int)a.minY; if (ry < 0) ry = 0;
            int rw = (int)(a.maxX - a.minX); if (rw < 1) rw = 1;
            int rh = (int)(a.maxY - a.minY); if (rh < 1) rh = 1;
            if (rx + rw > 640) rw = 640 - rx;
            if (ry + rh > 480) rh = 480 - ry;
            DrawDebugRect(rx, ry, rw, rh,
                          /*R=*/0, /*G=*/0xff, /*B=*/0, /*A=*/0xc0);
            ++emitted;
        }
    } else if (g_dbgOverlayMode == 4) {
        // 100×30 magenta bar at screen center. If this shows up, the
        // DrawColoredQuad path works and mode 3 should also work.
        DrawColoredQuad(270.0, 225.0, 100.0, 30.0,
                        /*R=*/0xff, /*G=*/0, /*B=*/0xff, /*A=*/0xff);
    }
}

// --- frame hook ---------------------------------------------------------
//
// Replaces the `bl SpriteHandlePool_GC` at 0x8002c678 inside MainGameLoop
// (the real per-frame loop). Runs the original GC, then our overlay work.
// Doing the overlay AFTER the GC means GC has already swept the freelist
// for this frame; only "still-active" slots remain visible to us, which
// is exactly what we want to render.

extern "C" void DebugOverlay_FrameHook() {
    EnsureDBATWidened();

    // 1. Run original sprite-pool GC.
    SpriteHandlePool_GC();

    // 2. Overlay work (gated).
    if (!g_dbgOverlayEnabled) return;
    EnsureDisplayContext();
    if (!s_dbgCtx) return;
    RenderHud();
}

// kmCall replaces the `bl` instruction at 0x8002c678 with `bl
// DebugOverlay_FrameHook`. The other 9 BL sites for SpriteHandlePool_GC
// are unaffected (boot card-task loop, BootPCBCheck loop, NOTICE loop,
// test pages, etc.).
kmCall(0x8002c678, DebugOverlay_FrameHook);
