import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def diagnosis_records(profile, source):
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile['source_sha256']:
        raise ValueError('Supplied diagnosis source changed')
    properties = Path(profile['diagnosis_properties']).read_bytes()
    if hashlib.sha256(properties).hexdigest() != profile['diagnosis_properties_sha256']:
        raise ValueError('Diagnosis schema changed')
    fingerprint = next(line.split('=', 1)[1] for line in properties.decode('ascii').splitlines()
                       if line.startswith('metadata.fingerprint='))
    schema = {}
    for _, namespace, fields in re.findall(r'(\w+)\(([0-9a-f]+)\):([^;]*);', fingerprint):
        for field in fields.split(','):
            key, size = field.split('.')
            schema.setdefault(int(key, 16), []).append((int(namespace, 16), int(size)))
    seen, applied, excluded, lines = set(), [], [], []
    for line in raw.decode('ascii').splitlines():
        if not line.strip():
            continue
        numbers = [int(value) for value in line.split()]
        key, values = numbers[0], numbers[1:]
        if key in seen or not values or any(not 0 <= value <= 255 for value in values):
            raise ValueError('Invalid or duplicate supplied diagnosis record')
        seen.add(key)
        candidates = schema.get(key, [])
        if len(candidates) != 1 or candidates[0][1] != len(values):
            excluded.append({'key': key, 'bytes': len(values), 'schema': candidates})
            continue
        namespace, _ = candidates[0]
        data = bytes(values)
        lines.append((namespace, key, data.hex()))
        applied.append({'namespace': namespace, 'key': key, 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()})
    if not any(namespace == 33554432 and key == 1536 for namespace, key, _ in lines):
        raise ValueError('Supplied diagnosis has no matching coding record')
    result = ''.join(f'{namespace}/{key}={value}\n' for namespace, key, value in sorted(lines))
    return result.encode('ascii'), {'source_sha256': hashlib.sha256(raw).hexdigest(),
                                    'applied': applied, 'excluded': excluded}


def checksums(profile):
    name = profile.get('hmi_configuration_manifest')
    if name is None:
        return None
    raw = Path(name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile['hmi_configuration_manifest_sha256']:
        raise ValueError('Imported car configuration manifest changed')
    manifest = json.loads(raw)
    if manifest['profile'] != 'original-car-hmi-data-v1':
        raise ValueError('Unexpected car configuration import profile')
    if manifest['encoded_vehicle_type'] != profile['encoded_vehicle_type']:
        raise ValueError('Imported vehicle resource key differs')
    source = Path(manifest['source_root']).resolve(strict=True)
    seen = set()
    lines = []
    for row in manifest['files']:
        name = row['path']
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name
                or any(ord(c) < 32 or c == '\\' for c in name)
                or not name.startswith(('vw/hmi/config/', 'vw/hmi/Resources/skin0/Data/'))
                or name in seen):
            raise ValueError('Unexpected imported car configuration path')
        seen.add(name)
        member = source.joinpath(*path.parts)
        if member.is_symlink() or not member.resolve(strict=True).is_relative_to(source):
            raise ValueError('Imported car configuration left its source tree')
        data = member.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != row['bytes'] or digest != row['sha256']:
            raise ValueError('Imported car configuration bytes changed: ' + name)
        lines.append(digest + '  ' + name + '\n')
    key = profile['encoded_vehicle_type']
    if not any('/' + key + '/' in name and name.endswith('/Climate/climate.clm') for name in seen):
        raise ValueError('Imported configuration has no climate layout for the selected vehicle')
    return ''.join(lines).encode('ascii')
