"""Reproducible newc overlay appended to an externally supplied initramfs."""
import gzip
import hashlib
import io
from pathlib import Path, PurePosixPath
import tarfile

from .config import Config, PROJECT
from .fixtures import payloads


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cpio_entry(name: str, content: bytes, mode: int, inode: int) -> bytes:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\0" in name:
        raise ValueError("Invalid CPIO member name")
    encoded = name.encode("utf-8")
    fields = [inode, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(encoded) + 1, 0]
    data = b"070701" + "".join(f"{n:08x}" for n in fields).encode() + encoded + b"\0"
    data += b"\0" * (-len(data) % 4)
    data += content
    return data + b"\0" * (-len(content) % 4)


def build_initramfs(config: Config, mode: str, seconds: int, language: str = 'en_US', diagnostics: bool = False,
                    gateway=None, disable_animations: bool = False, gpu: bool = False) -> tuple[Path, list[dict]]:
    if mode not in ("baseline", "jvm", "hmi") or not 1 <= seconds <= 3600:
        raise ValueError("Invalid boot mode or lifetime")
    if disable_animations and mode != 'hmi':
        raise ValueError('Animation control requires hmi mode')
    if gpu and mode != 'hmi':
        raise ValueError('GPU rendering requires hmi mode')
    config["output"].mkdir(parents=True, exist_ok=True)
    guest = PROJECT / "guest"
    init = {"baseline": "baseline-init.sh", "jvm": "probe-init.sh", "hmi": "hmi-init.sh"}[mode]
    members = {"probe-init": (guest.joinpath(init).read_bytes(), 0o100755)}
    if mode == "hmi":
        if 'navigation_uplink' in config.paths:
            from .navigation_fixture import uplink
            members['navigation-uplink.conf']=(uplink(config['navigation_uplink'])[1],0o100644)
            members['navigation-network']=((guest/'navigation-network.sh').read_bytes(),0o100755)
        if gpu:
            members['bench-gpu-virgl'] = (b'enabled', 0o100644)
            members['virgl-drm-compat.so'] = ((PROJECT / 'build/libvirgl-drm-compat.so').read_bytes(), 0o100755)
            members['native-pointer.py'] = ((guest / 'native-pointer.py').read_bytes(), 0o100644)
        if disable_animations:
            members['bench-disable-animations'] = (b'enabled', 0o100644)
        if gateway:
            gateway.validate(mode)
            members['http-gateway-go'] = (gateway.binary.read_bytes(), 0o100755)
            members['bench-tcp-relay'] = (gateway.relay.read_bytes(), 0o100755)
            members['gateway-setup'] = ((guest / 'gateway-setup.sh').read_bytes(), 0o100755)
            members['gateway-probe.py'] = ((guest / 'gateway-probe.py').read_bytes(), 0o100644)
            members['gateway-hmi-port'] = (str(gateway.proxy_port).encode(), 0o100644)
            if gateway.sdk_gateway:
                members['gateway-sdk-enabled'] = (b'yes', 0o100644)
        for name, source in {
            "display-setup": "display-setup.sh", "platform-setup": "platform-setup.sh",
            "domain-network": "domain-network.sh",
            "navigation-setup": "navigation-setup.sh", "navigation-run": "navigation-run.sh",
            "navigation-memory": "navigation-memory.sh",
            "native-rsi-setup": "native-rsi-setup.sh",
            "display-map": "display-map-setup.sh", "display-map-apply": "display-map-apply.sh",
            "platform-readiness.py": "platform-readiness.py",
            "online-service.py": "online-service.py",
            "permission-service.py": "permission-service.py",
            "vehicle-rsi-setup.py": "vehicle-rsi-setup.py",
            "update-service": "update-service.sh", "update-watch.py": "update-watch.py",
            "host-clock-setup": "host-clock-setup.sh", "host-clock.py": "host-clock.py",
        }.items():
            members[name] = ((guest / source).read_bytes(), 0o100755)
        from .navigation_fixture import gnss_frame
        members['navigation-gnss-frame']=(gnss_frame(PROJECT.parents[1]/'var/hvf/navigation-location.json'),0o100644)
        simulation = PROJECT.parents[1]/'var/hvf/navigation-simulation.json'
        if simulation.is_file():
            from . import navigation_fixture
            navigation_fixture.validate_simulation(simulation)
            members['navigation-motion.py']=((guest/'navigation-motion.py').read_bytes(),0o100644)
            members['navigation-motion-route.json']=(simulation.read_bytes(),0o100644)
        members['libunified-touch.so']=((PROJECT/'build/libunified-touch.so').read_bytes(),0o100755)
        members['libnavigation-gnss.so']=((PROJECT/'build/libnavigation-gnss.so').read_bytes(),0o100755)
        members['rem-input.so'] = ((PROJECT / 'build/rem-input.so').read_bytes(), 0o100755)
        members["hmi-seconds"] = (str(seconds).encode(), 0o100644)
        from .vehicle_fixture import control_properties
        members['bench-vehicle.properties'] = (control_properties(), 0o100644)
        if diagnostics:
            members['bench-thread-dump'] = (b'enabled', 0o100644)
        for name, key in {"bench-platform.jar": "adapter_jar", "mesa-runtime.tar.gz": "mesa_archive",
                          "egl-forward-trace.so": "egl_bridge"}.items():
            members[name] = (config[key].read_bytes(), 0o100644)
        profile_path = PROJECT / 'local/vehicle-profile.json'
        if profile_path.exists():
            import json
            profile = json.loads(profile_path.read_bytes())
            vehicle_type = profile['encoded_vehicle_type']
            if (not isinstance(vehicle_type, str) or len(vehicle_type) != 21
                    or not vehicle_type.isascii() or not vehicle_type.isdecimal()):
                raise ValueError('Expected the supplied 21-digit encoded vehicle type')
            if profile.get('native_diagnosis'):
                properties = Path(profile['diagnosis_properties']).read_bytes()
                if hashlib.sha256(properties).hexdigest() != profile['diagnosis_properties_sha256']:
                    raise ValueError('Private diagnosis properties differ from the supplied profile')
                members['bench-diagApi.properties'] = (properties, 0o100644)
                coding = Path(profile['coding_path']).read_bytes()
                if len(coding) != 25 or hashlib.sha256(coding).hexdigest() != profile['coding_sha256']:
                    raise ValueError('Supplied original coding record differs')
                members['bench-ric-id3-coding.bin'] = (coding, 0o100644)
                if profile.get('diagnosis_records_path'):
                    from .car_configuration import diagnosis_records
                    records, _ = diagnosis_records(profile, PROJECT.parents[1] / profile['diagnosis_records_path'])
                    members['bench-diagnosis-records.properties'] = (records, 0o100644)
            else:
                members['bench-encoded-vehicle-type'] = (vehicle_type.encode('ascii'), 0o100644)
            from .car_configuration import checksums
            imported_configuration = checksums(profile)
            if imported_configuration is not None:
                members['bench-car-configuration.sha256'] = (imported_configuration, 0o100644)
        members["navigation-log.py"]=((guest/"navigation-log.py").read_bytes(),0o100644)
        fixtures = payloads(language)
        for resource in ("settings", "strategies", "components", "requestedSettings", "changeTransactions"):
            members[f"bench-language-{resource}.json"] = (fixtures[f"language/{resource}.json"], 0o100644)
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for name, content in fixtures.items():
                if name.startswith("language/"):
                    continue
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(content), 0o644
                archive.addfile(member, io.BytesIO(content))
        members["welcome-fixtures.tar"] = (data.getvalue(), 0o100644)
    overlay = b"".join(cpio_entry(name, data, mode_bits, 900000 + i)
                       for i, (name, (data, mode_bits)) in enumerate(members.items()))
    overlay += cpio_entry("TRAILER!!!", b"", 0, 999999)
    target = config["output"] / f"{mode}-initramfs.gz"
    target.write_bytes(config["initramfs"].read_bytes() + gzip.compress(overlay, mtime=0))
    identities = [{"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, (data, _) in members.items()]
    return target, identities
