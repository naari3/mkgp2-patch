#!/bin/bash
set -e

CW="/c/Program Files (x86)/Freescale/CW for MPC55xx and MPC56xx 2.10/PowerPC_EABI_Tools/Command_Line_Tools"
KAMEK_STDLIB="/c/Users/naari/src/github.com/Treeki/Kamek/k_stdlib"
KAMEK="/c/Users/naari/src/github.com/Treeki/Kamek/Kamek.exe"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. Run per-feature generators (gen_*.py in each feature dir)
echo "=== Running feature generators ==="
shopt -s nullglob
for gen in "$PATCH_DIR"/features/*/gen_*.py; do
    # Only run headers-from-data style generators (not mod/yaml-from-yaml)
    case "$(basename "$gen")" in
        gen_joints_header.py) python3 "$gen" ;;
    esac
done
shopt -u nullglob

# 2. Discover all source files
SOURCES=()
for src in "$PATCH_DIR"/common/*.cpp "$PATCH_DIR"/features/*/*.cpp; do
    [ -f "$src" ] && SOURCES+=("$src")
done
echo "Sources: ${SOURCES[@]#$PATCH_DIR/}"

# 3. Compile
echo "=== Compiling ==="
OBJS=()
for src in "${SOURCES[@]}"; do
    obj="${src%.cpp}.o"
    feature_inc="$(dirname "$src")"
    "$CW/mwcceppc.exe" \
        -I- -i "$KAMEK_STDLIB" -i "$PATCH_DIR/common" -i "$feature_inc" \
        -Cpp_exceptions off -enum int -Os \
        -use_lmw_stmw on -fp hard -rostr \
        -sdata 0 -sdata2 0 \
        -c -o "$obj" "$src"
    OBJS+=("$obj")
done

# 4. Link with Kamek
echo "=== Linking ==="
"$KAMEK" \
    "${OBJS[@]}" \
    -static=0x806ED000 \
    -externals="$PATCH_DIR/externals.txt" \
    -output-gecko="$PATCH_DIR/joint_extend_gecko.txt" \
    -output-riiv="$PATCH_DIR/joint_extend_riiv_raw.xml" \
    -output-code="$PATCH_DIR/joint_extend.bin"

# 5. Wrap Riivolution XML
echo "=== Wrapping Riivolution XML ==="
{
  echo '<wiidisc version="1">'
  echo '  <id game="GNLJ"/>'
  echo '  <options>'
  echo '    <section name="MKGP2 Joint Extend">'
  echo '      <option name="Custom Joints" default="1">'
  echo '        <choice name="Enabled">'
  echo '          <patch id="joint_extend"/>'
  echo '        </choice>'
  echo '      </option>'
  echo '    </section>'
  echo '  </options>'
  echo '  <patch id="joint_extend">'
  while IFS= read -r line || [ -n "$line" ]; do
    echo "    $line"
  done < "$PATCH_DIR/joint_extend_riiv_raw.xml"
  echo '  </patch>'
  echo '</wiidisc>'
} > "$PATCH_DIR/joint_extend.xml"

RIIV_DIR="/c/Users/naari/Documents/Dolphin Emulator/Load/Riivolution/riivolution"
mkdir -p "$RIIV_DIR"
cp "$PATCH_DIR/joint_extend.xml" "$RIIV_DIR/joint_extend.xml"

# 6. Generate patch_map.md
echo "=== Generating patch_map.md ==="
python3 "$PATCH_DIR/tools/gen_patch_map.py"

echo "=== Done ==="
echo "Riivolution: $RIIV_DIR/joint_extend.xml"
echo "Gecko:       $PATCH_DIR/joint_extend_gecko.txt"
echo "Patch map:   $PATCH_DIR/patch_map.md"
