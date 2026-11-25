"""All machine-specific paths belong to an ignored local configuration."""
from dataclasses import dataclass
import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PATH_KEYS = (
    "qemu", "qemu_data", "kernel", "initramfs", "modules", "firmware_root",
    "firmware_hmi", "hmi_jar", "osgi_jar", "adapter_jar", "mesa_archive",
    "egl_bridge", "novnc", "output", "firmware_custom",
)


OPTIONAL_PATH_KEYS = ("firmware_navigation", "navigation_data", "navigation_map", "navigation_uplink")


@dataclass(frozen=True)
class Config:
    source: Path
    paths: dict[str, Path]

    @classmethod
    def load(cls, path: Path | str) -> "Config":
        source = Path(path).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
        unknown = set(raw) - set(PATH_KEYS) - set(OPTIONAL_PATH_KEYS)
        if unknown:
            raise ValueError("Unknown configuration keys: " + ", ".join(sorted(unknown)))
        paths = {}
        for key in PATH_KEYS + tuple(key for key in OPTIONAL_PATH_KEYS if key in raw):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Missing path setting: " + key)
            resolved = Path(os.path.expandvars(value)).expanduser()
            paths[key] = (source.parent / resolved).resolve() if not resolved.is_absolute() else resolved.resolve()
        return cls(source, paths)

    def __getitem__(self, key: str) -> Path:
        return self.paths[key]

    def required(self, mode: str) -> list[str]:
        if mode not in ("baseline", "jvm", "hmi"):
            raise ValueError("Unknown boot mode")
        required = ["qemu", "kernel", "initramfs"]
        if mode != "baseline":
            required += ["modules", "firmware_root"]
        if mode == "hmi":
            required += ["firmware_hmi", "firmware_custom", "adapter_jar", "mesa_archive", "egl_bridge", "novnc", "qemu_data"]
        if mode == "hmi" and "firmware_navigation" in self.paths:
            required += ["firmware_navigation"]
        if mode == "hmi" and "navigation_map" in self.paths:
            required += ["navigation_map"]
        if mode == 'hmi' and 'navigation_uplink' in self.paths:
            required += ['navigation_uplink']
        return required

    def validate(self, mode: str, *, gpu: bool = False) -> None:
        missing = [key + ": " + str(self[key]) for key in self.required(mode) if not self[key].exists()]
        if mode == "hmi" and not (self["novnc"] / "core/rfb.js").is_file():
            missing.append("novnc/core/rfb.js")
        if mode == "hmi" and not (self["qemu_data"] / "keymaps/en-us").is_file():
            missing.append("qemu_data/keymaps/en-us")
        if gpu:
            if mode != 'hmi':
                raise ValueError('GPU rendering requires hmi mode')
            for item in (PROJECT / 'build/libvirgl-drm-compat.so',
                         self['qemu'].parent / 'libepoxy-0.dll',
                         self['qemu'].parent / 'libEGL.dll',
                         self['qemu'].parent / 'libGLESv2.dll'):
                if not item.is_file():
                    missing.append(str(item))
        if missing:
            raise FileNotFoundError("Missing local dependencies:\n  " + "\n  ".join(missing))
        # Generated output must never contain or overwrite an input tree.
        for key in self.required(mode):
            item = self[key]
            if item == self["output"] or self["output"] in item.parents:
                raise ValueError("Input paths must be separate from output: " + key)
