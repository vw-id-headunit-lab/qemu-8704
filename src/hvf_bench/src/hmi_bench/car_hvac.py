import hashlib
import json
from pathlib import Path


def objects(profile_path=None):
    profile_path = Path(profile_path) if profile_path else Path(__file__).resolve().parents[2] / 'local/vehicle-profile.json'
    profile = json.loads(profile_path.read_text())
    name = profile.get('hvac_capture_json')
    if name is None:
        return None
    path = Path(name)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != profile['hvac_capture_sha256']:
        raise ValueError('Imported HVAC capture changed')
    capture = json.loads(raw)
    if capture['profile'] != 'original-car-hvac-snapshot-v1' or capture['service_version'] != '3.3.0':
        raise ValueError('Unexpected imported HVAC service schema')
    values = capture['objects']
    uris = set()
    for resource, rows in values.items():
        if not resource.isidentifier() or not isinstance(rows, list):
            raise ValueError('Invalid imported HVAC collection')
        for row in rows:
            uri = '/hvac/' + resource + '/' + row['id']
            if row['uri'] != uri or uri in uris:
                raise ValueError('Invalid imported HVAC object identity')
            uris.add(uri)
    def validate(value):
        if isinstance(value, dict):
            uri = value.get('uri')
            if uri is not None and uri not in uris and uri not in capture['unresolved_references']:
                raise ValueError('Missing imported HVAC reference: ' + str(uri))
            for item in value.values():
                validate(item)
        elif isinstance(value, list):
            for item in value:
                validate(item)
    validate(values)
    return values
