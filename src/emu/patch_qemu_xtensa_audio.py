

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "var/inputs/support/abox-dsp-native/xtensa-source"
DMA = "cdc63fb8ac191c40cd4d50915bff11b8ed3829570509551adf3d5af1f42d8714"
INSTRUCTIONS = "1f7760f764e3a1448eb089130c5b4d773a90c78118c7f5953b0dd6ed698d1d24"
ADDRESS = 0x19C00A00
EDITS = (
    ("hw/xtensa/sim.c", "e2cdc6604c5147f28400939904743d4d3e64e037f904e63dc2937f8d0b4b36a2",
     b'    auto9_audio_config_word(machine, 0x19c01800u, 0xffu, "UAIF_CLK_DIV");',
     (b"    /* OEM IPC 0x0d writes 0, 0x10, then sets bit 1 after readback. */",
      b'    auto9_audio_config_word(machine, 0x19c00a00u, 0x12u, "AUD_SPARE0");')),
    ("target/xtensa/mmu_helper.c", "8b8d690a7488bdd6afff6221b9eec60d2dce01aa7c35962b46ead2bbb489c404",
     b"         (vaddr - 0x19d80000u < 0xe000u) ||",
     (b"         /* Data page for AUD_SPARE0; the device maps only one word. */",
      b"         (vaddr & ~0xfffu) == 0x19c00000u ||")),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def edit(data, before, anchor, lines):
    eol = b"\r\n" if data.count(b"\r\n") == data.count(b"\n") else b"\n"
    anchor += eol
    block = anchor + eol.join(lines) + eol
    if data.count(block) == 1 and digest(data.replace(block, anchor, 1)) == before:
        return data
    if digest(data) != before or data.count(anchor) != 1:
        raise ValueError("source does not match the pinned preimage or postimage")
    return data.replace(anchor, block, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-core0", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Atomically update the private source")
    args = parser.parse_args()
    original = args.original_core0.read_bytes()
    if (digest(original) != DMA or int.from_bytes(original[0xffdc:0xffe0], "little") != ADDRESS
            or digest(original[0x281ab:0x281ef]) != INSTRUCTIONS):
        parser.error("Original DSP DMA readback or exact instruction window differs")
    staged = []
    for relative, before, anchor, lines in EDITS:
        source = SOURCE / relative
        data = source.read_bytes()
        try:
            patched = edit(data, before, anchor, lines)
        except ValueError as exc:
            parser.error(f"{relative}: {exc}")
        staged.append((source, patched, {"path": relative, "before_sha256": before,
                                        "after_sha256": digest(patched),
                                        "changed": args.apply and patched != data}))
    for source, patched, report in staged:
        if report["changed"]:
            with tempfile.NamedTemporaryFile(dir=source.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(patched)
            try:
                os.replace(temporary, source)
            finally:
                temporary.unlink(missing_ok=True)
    print(json.dumps({"source": str(SOURCE), "files": [x[2] for x in staged],
                      "original_sha256": DMA, "instruction_sha256": INSTRUCTIONS,
                      "address": hex(ADDRESS), "write_mask": "0x12"}, indent=2))


if __name__ == "__main__":
    main()
