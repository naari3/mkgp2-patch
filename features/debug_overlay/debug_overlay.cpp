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
    // bottom / left / right edges as separate thin filled rects).
    void DrawColoredQuad(double x, double y, double w, double h,
                         unsigned char r, unsigned char g,
                         unsigned char b, unsigned char a);

    // Phase 2B-LINES: GX-direct GX_LINES emit. DrawColoredQuad leaves all GX
    // state (vtxFmt POS=F32, TEV KColor0, ortho proj, viewport) in place after
    // returning, so we can immediately follow it with GX_Begin(GX_LINES, ...)
    // and emit 2 F32 vertices to draw a line in the same color. Vanilla MKGP2
    // never uses non-QUAD primitives, so this is uncharted territory at the
    // wrapper-state level — but the FIFO command stream itself is well-formed.
    void GX_Begin(unsigned char type, unsigned char vtxFmt, unsigned short cnt);
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

// Display mode. Default 0 = off (no overlay rendered at all).
// Pokeable from Dolphin to switch live.
//   0 = off
//   1 = summary line (active / visible counts)
//   2 = full per-slot list (id / size / pos / filename), capped at kHudListMax
//   3 = per-sprite id label at AABB top-left + cyan rect outline (GX_LINES)
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

// --- GX_LINES rect outline ----------------------------------------------
//
// Sets up state by calling DrawColoredQuad once with an offscreen 1x1 quad
// (clipped by the 640x480 scissor, so invisible). The quad emit configures
// GX exactly the way we need for line rendering (POS=F32, TEV KColor0,
// ortho 640x480), then leaves the state in place. Subsequent GX_Begin(0xa8)
// calls inherit that state and emit lines in the same color.

// Ensure GX state is set up for untextured F32 line emit, with KColor0 set
// to the supplied RGBA. The throw-away quad is 1x1 at (-100,-100) so the
// scissor clips it (no visible artifact).
static void DebugOverlay_LineSetupViaQuad(unsigned char r, unsigned char g,
                                          unsigned char b, unsigned char a) {
    DrawColoredQuad(-100.0, -100.0, 1.0, 1.0, r, g, b, a);
}

// Emit one GX_LINES segment. Args are int to avoid mwcc spilling float regs
// across the GX_Begin call (which would emit a _savefpr_NN runtime helper
// reference that Kamek's stdlib doesn't provide). The int→float conversion
// happens after GX_Begin so the volatile FPRs are clobbered by the call but
// not needed yet.
static void DrawDebugLineI(int x0, int y0, int x1, int y1) {
    GX_Begin(0xa8 /*GX_LINES*/, 0 /*vtxFmt 0*/, 2);
    *(volatile float*)0xCC008000 = (float)x0;
    *(volatile float*)0xCC008000 = (float)y0;
    *(volatile float*)0xCC008000 = (float)x1;
    *(volatile float*)0xCC008000 = (float)y1;
}

// Outline a rect with 4 GX_LINES (top/bottom/left/right). Caller must have
// already invoked DebugOverlay_LineSetupViaQuad to configure state.
static void DrawDebugRectLines(int x, int y, int w, int h) {
    if (w <= 0 || h <= 0) return;
    const int x1 = x + w;
    const int y1 = y + h;
    DrawDebugLineI(x,  y,  x1, y);   // top
    DrawDebugLineI(x1, y,  x1, y1);  // right
    DrawDebugLineI(x,  y1, x1, y1);  // bottom
    DrawDebugLineI(x,  y,  x,  y1);  // left
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
// Display caps. Each is the max number of sprites we'll visualize in a frame.
//   kHudListMax: full-list mode 2 row count, sized to fit 480px at scale 0.5.
//   kPerSpriteMax: mode 3 label + rect cap. Bound by DisplayContext's 127
//     entry buffer (one slot for the summary line, rest for labels). Rect
//     emit has no equivalent buffer, so we share the same cap to keep label
//     and outline counts in sync (no asymmetric truncation).
static const int    kHudListMax   = 28;
static const int    kPerSpriteMax = 110;

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

    // Mode 1+: top-left summary line.
    DrawText(kHudScale, s_dbgCtx, kHudOriginX, kHudOriginY, 7,
             "DBG: %d visible / %d active sprites", visible, active);

    if (g_dbgOverlayMode == 2) {
        // Full per-slot list. Capped to stay under DisplayContext's 127
        // entry-buffer limit and the screen height at scale 0.5.
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
    } else if (g_dbgOverlayMode == 3) {
        // Per-sprite id label at AABB top-left.
        int emitted = 0;
        for (int i = 0; i < 500 && emitted < kPerSpriteMax; ++i) {
            const SpriteHandleSlot& s = g_SpriteHandlePool[i];
            if (!s.activeFlag || !s.visibleFlag) continue;
            Aabb a = ComputeAabb(s.vertCoords);
            if (a.maxX <= 0.0f || a.maxY <= 0.0f) continue;
            if (a.minX >= 640.0f || a.minY >= 480.0f) continue;
            int x = (int)a.minX; if (x < 0) x = 0; if (x > 600) x = 600;
            int y = (int)a.minY; if (y < 0) y = 0; if (y > 472) y = 472;
            DrawText(kHudScale, s_dbgCtx, x, y, 7, "%04x", (int)s.resourceId);
            ++emitted;
        }
    }

    DisplayContext_Flush(s_dbgCtx);

    // Mode 3: per-sprite cyan rect outline via 4 GX_LINES per sprite.
    // Rendered AFTER the text Flush so outlines compose on top of any text
    // (including the labels above) in the FIFO command stream.
    if (g_dbgOverlayMode == 3) {
        DebugOverlay_LineSetupViaQuad(/*R=*/0, /*G=*/0xff, /*B=*/0xff, /*A=*/0xff);
        int emitted = 0;
        for (int i = 0; i < 500 && emitted < kPerSpriteMax; ++i) {
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
            DrawDebugRectLines(rx, ry, rw, rh);
            ++emitted;
        }
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

    // 2. Overlay work (mode 0 = off, no display).
    if (g_dbgOverlayMode == 0) return;
    EnsureDisplayContext();
    if (!s_dbgCtx) return;
    RenderHud();
}

// kmCall replaces the `bl` instruction at 0x8002c678 with `bl
// DebugOverlay_FrameHook`. The other 9 BL sites for SpriteHandlePool_GC
// are unaffected (boot card-task loop, BootPCBCheck loop, NOTICE loop,
// test pages, etc.).
kmCall(0x8002c678, DebugOverlay_FrameHook);
