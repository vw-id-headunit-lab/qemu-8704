
import json
import re


CENTER_GPU_HISTORY = dict(path='/machine/exynos-mali-g3d1', property='vm3-center-target-history')
DIGEST = re.compile(r'[a-f0-9]{64}')


class CenterEventCadence:
    def __init__(self):
        self.next_probe = 0
        self.capture_count = 0
        self.attempts = {}
        self.confirmed = set()

    def probe_due(self, now):
        if self.capture_count >= 8 or now < self.next_probe:
            return False
        self.next_probe = now + .8
        return True

    def claim(self, raw, now, last_capture):
        if self.capture_count >= 8 or now - last_capture < 1.6:
            return None
        if not isinstance(raw, str) or len(raw) > 8192:
            raise ValueError('Invalid center GPU history size')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('version') != 1 or type(data.get('generation')) is not int:
            raise ValueError('Unsupported center GPU history')
        targets = data.get('targets')
        if not isinstance(targets, list) or len(targets) > 16:
            raise ValueError('Invalid center GPU history targets')
        candidates = []
        for row in targets:
            if not isinstance(row, dict):
                raise ValueError('Malformed center GPU history target')
            f, c = row.get('footprint'), row.get('content')
            g, rgb, age = row.get('generation'), row.get('rgb'), row.get('age_ns')
            if (type(g) is int and 0 < g <= data['generation'] and g == row.get('color_generation') and
                    type(rgb) is int and 0 < rgb <= 1560 * 700 and
                    type(age) is int and 0 <= age <= 6_000_000_000 and
                    row.get('bytes') == 1560 * 700 * 4 and row.get('pitch') == 6272 and
                    row.get('clear') is False and isinstance(f, str) and DIGEST.fullmatch(f) and
                    isinstance(c, str) and DIGEST.fullmatch(c) and
                    g not in self.confirmed and self.attempts.get(g, 0) < 2):
                candidates.append((g, f, c))
        if not candidates:
            return None
        g, f, c = max(candidates)
        self.capture_count += 1
        self.attempts[g] = self.attempts.get(g, 0) + 1
        return dict(generation=g, footprint_sha256=f, content_sha256=c, attempt=self.attempts[g])

    def confirm(self, candidate, proof, capture_start):
        if not isinstance(proof, dict):
            return False
        latest = proof.get('latest_write')
        at = proof.get('capture_monotonic')
        if (type(at) in (int, float) and at >= capture_start and isinstance(latest, dict) and
                latest.get('generation') == candidate['generation'] and
                proof.get('active_footprint_sha256') == candidate['footprint_sha256'] and
                proof.get('active_content_sha256') == candidate['content_sha256'] and
                proof.get('latest_write_content_matches') is True):
            self.confirmed.add(candidate['generation'])
            return True
        return False
