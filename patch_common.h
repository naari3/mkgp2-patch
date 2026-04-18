#ifndef PATCH_COMMON_H
#define PATCH_COMMON_H

extern "C" {

// Idempotent DBAT0 widening. Call once from any feature entry point.
// MKGP2's __start installs a narrow DBAT0 (~32MB) that prevents data reads
// from the patch region (0x806EDxxx); this extends it to cover 0x80000000-0x8FFFFFFF.
void EnsureDBATWidened();

// Low-level DBAT0 expansion (rarely needed directly; prefer EnsureDBATWidened).
void WidenDBAT0_256M();

// DebugPrintf wrapper that avoids Dolphin HLE this-pointer heuristic misfire on
// format strings whose first 4 bytes resolve to a "valid RAM" address via PPC
// segment-register identity mapping (e.g. strings starting with "MKGP").
void DebugPrintfSafe(const char* fmt, ...);

// Original game symbol. Use DebugPrintfSafe in new code.
void DebugPrintf(const char* fmt, ...);

}

#endif
