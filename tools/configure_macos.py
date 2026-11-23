"""Bind a checkout to user-provided 8704 inputs; never downloads OEM resources."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(root=ROOT, vehicle_type='020301006300000002000'):
    root = Path(root).resolve()
    if not (len(vehicle_type) == 21 and vehicle_type.isascii() and vehicle_type.isdecimal()):
        return {'ready': False, 'error': 'Vehicle resource key must contain exactly 21 ASCII digits.'}, 2
    bench = root / 'src/hvf_bench'
    inputs = root / 'var/inputs'
    paths = {
        'qemu': root / 'runtime/darwin/qemu-system-aarch64',
        'qemu_data': root / 'runtime/darwin/pc-bios',
        'kernel': bench / 'runtime/alpine/vmlinuz-virt',
        'initramfs': bench / 'runtime/alpine/initramfs-virt',
        'modules': bench / 'runtime/alpine/modloop-virt',
        'firmware_root': inputs / 'analysis/cpu/ivi/icas3cn-gp-image-euto-v9.ext4',
        'firmware_hmi': inputs / 'hmi/vw-restored/vw-hmi.ext4',
        'firmware_custom': inputs / 'support/vw-verity-native/overlay-verity/lg-custom.ext4',
        'hmi_jar': bench / 'local/compile/hmi.jar',
        'osgi_jar': bench / 'local/compile/osgi.jar',
        'adapter_jar': bench / 'build/bench-platform.jar',
        'mesa_archive': bench / 'runtime/mesa/mesa-runtime.tar.gz',
        'egl_bridge': bench / 'build/libegl-forward-bridge.so',
        'novnc': bench / 'runtime/novnc',
        'output': root / 'var/hvf',
    }
    ufs = inputs / 'support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw'
    required = [value for key, value in paths.items()
                if key not in ('hmi_jar', 'osgi_jar', 'output')]
    required += [ufs, paths['qemu_data'] / 'keymaps/en-us', paths['novnc'] / 'core/rfb.js']
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if missing:
        return {'ready': False, 'missing': missing,
                'instruction': 'Prepare your own 8704 inputs; no OEM downloads are provided.'}, 2
    provenance_file = root / 'src/hvf_android/runtime/modules/provenance.json'
    provenance = json.loads(provenance_file.read_text())
    for row in provenance:
        path = provenance_file.parent / Path(row['path']).name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            return {'ready': False, 'invalid_module': str(path)}, 2
        row['path'] = str(path)
    output = paths['output']
    if any(output.glob('*current.json')):
        return {'ready': False, 'error': 'Existing session pointers found; do not reconfigure an active checkout.'}, 2
    output.mkdir(parents=True, exist_ok=True)
    misc = output / 'android-misc.raw'
    if not misc.exists():
        with misc.open('xb') as stream:
            stream.truncate(1024 * 1024)
    elif misc.stat().st_size != 1024 * 1024:
        return {'ready': False, 'error': 'Existing Android misc has an unexpected size; not overwritten.'}, 2
    config = root / 'config/hvf-8704.json'
    if config.exists() and not config.with_suffix('.json.before-configure').exists():
        config.with_suffix('.json.before-configure').write_bytes(config.read_bytes())
    config.write_text(json.dumps({key: str(value) for key, value in paths.items()}, indent=2) + '\n')
    provenance_file.write_text(json.dumps(provenance, indent=2) + '\n')
    profile = bench / 'local/vehicle-profile.json'
    if not profile.exists():
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(json.dumps({'encoded_vehicle_type': vehicle_type,
                                      'native_diagnosis': False,
                                      'source': 'Explicit synthetic offline bench resource selection; not original vehicle coding.'}, indent=2) + '\n')
    return {'ready': True, 'config': str(config), 'firmware': '8704',
            'scope': 'Input and path preflight only; boot must be verified separately.'}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vehicle-type', default='020301006300000002000',
                        help='21-digit offline HMI resource key; not a vehicle identity')
    args = parser.parse_args()
    result, status = configure(vehicle_type=args.vehicle_type)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
