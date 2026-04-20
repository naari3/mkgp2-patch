#include <kamek.h>
#include "patch_common.h"

// Widen DBAT0 to cover 0x80000000-0x8FFFFFFF (256MB) so HLE/MMU can read
// data from our patch region (0x806EDxxx). MKGP2's __start installs a narrow
// DBAT0 (~32MB) and wider IBAT0, so code runs but data reads from our region
// fail via PPC MMU. One-shot: invalidates JIT cache on DBAT update.
extern "C" asm void WidenDBAT0_256M() {
    nofralloc
    // BATU = BEPI(0x80000000) | BL(0x7FF << 2) | VS(1<<1)
    lis r3, 0x8000
    ori r3, r3, 0x1FFE
    // BATL = BRPN(0) | WIMG=M(0x2 << 3) | PP=rw(0x2)
    li  r4, 0x12
    sync
    mtspr 537, r4       // DBAT0L
    mtspr 536, r3       // DBAT0U
    sync
    isync
    blr
}

// Dolphin's HLE_GeneralDebugPrint uses a heuristic: if r3 is a valid RAM address
// AND *r3 also looks like a valid RAM address, it assumes r3 is a C++ `this`
// pointer and reads the format string from r4. Otherwise it reads from r3.
//
// For our format strings placed in the Kamek patch region, the first 4 bytes
// ("MKGP" = 0x4D4B4750) happen to resolve to a "valid RAM" address via PPC
// segment-register translation in MKGP2. HLE then mistakenly treats r3 as
// `this` and reads garbage from r4/r5.
//
// Workaround: shift all integer varargs registers by one and set r3 = 0.
// Dolphin's HLE will take the `this`-style path (r3=null), see r4 as a valid
// RAM pointer (our format string), and print correctly. The real DebugPrintf
// runs after HLE with r3=0 but won't crash (it reads harmless low-memory bytes).
extern "C" asm void DebugPrintfSafe(const char* fmt, ...) {
    nofralloc
    // Shift int varargs regs up one slot: r10->r11, r9->r10, ... r3->r4
    mr   r11, r10
    mr   r10, r9
    mr   r9, r8
    mr   r8, r7
    mr   r7, r6
    mr   r6, r5
    mr   r5, r4
    mr   r4, r3
    li   r3, 0
    b    DebugPrintf
}

static int s_dbat_widened = 0;

extern "C" void EnsureDBATWidened() {
    if (!s_dbat_widened) {
        WidenDBAT0_256M();
        s_dbat_widened = 1;
    }
}

// Raise ArenaLo past our patch code. Game's heap (Alloc) starts here.
//
// Patch bin is loaded at 0x806ED000 (see `-static=0x806ED000` in build.sh).
// ArenaLo MUST be >= bin_end or the game's first Alloc() overwrites the tail
// of our patch data (joint name tables, strings, pointer arrays). Symptom:
// ResolveJointByName / path lookups receive garbage pointers → strcmp /
// DVDConvertPathToEntrynum crash loops firing thousands of invalid reads
// per frame.
//
// build.sh enforces `load_addr + bin_size <= ArenaLo` after linking. If it
// fails, raise the value below to the next 0x10000 boundary above bin end
// (the error message prints the required minimum). Don't shrink it below
// 0x10000 of bin end — there's no allocation pressure that low in MEM1 and
// headroom is free.
kmWrite32(0x80000030, 0x80700000);
