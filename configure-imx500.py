#!/usr/bin/env python3
"""Pin the dedicated IMX500 camera; installer must hold the updater lock."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

BEGIN = '# BEGIN trail-camera IMX500 (managed)'
END = '# END trail-camera IMX500 (managed)'
BLOCK = f'{BEGIN}\n[all]\ncamera_auto_detect=0\ndtoverlay=imx500\n{END}\n'
# Camera sensors and camera bridges/multiplexers cannot safely be combined here.
CAMERA = re.compile(r'^(?:imx\d+|ov\d+|arducam|camera|arducam-pivariety|'
                    r'adv728|tc358743|gs|irs|vc-mipi|st-vgxy61|mira|og0|sc\d+)')


def transform(text):
    """Preserve unrelated configuration and place our effective settings last."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('Malformed managed IMX500 block; review config.txt.')
        del lines[starts[0]:ends[0] + 1]
    for index, line in enumerate(lines):
        match = re.match(r'^\s*dtoverlay\s*=\s*([^#\r\n]+)', line)
        if not match:
            continue
        overlay = match[1].strip()
        name = overlay.split(',', 1)[0].strip()
        if name == 'imx500' and ',' not in overlay:
            lines[index] = '# Moved into managed IMX500 block: ' + line
        elif CAMERA.match(name):
            raise ValueError(f'Explicit camera overlay requires review: {overlay}')
    return ''.join(lines).rstrip('\n') + '\n\n' + BLOCK


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(path, state, boot_id, check=False):
    original = path.read_text()
    updated = transform(original)
    if original == updated:
        return False
    if check:
        return True
    backup = path.with_name(path.name + '.trail-camera-imx500.bak')
    if not backup.exists():
        atomic_write(backup, original)
    # Persist first: interrupted config writes must still receive a reboot on retry.
    atomic_write(state / 'reboot-pending.json', json.dumps({'boot_id': boot_id}) + '\n')
    atomic_write(path, updated)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/boot/firmware/config.txt'))
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/trail-camera-updates'))
    parser.add_argument('--check', action='store_true', help='Preview whether configuration would change')
    args = parser.parse_args()
    boot = '' if args.check else Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    try:
        changed = configure(args.config, args.state_dir, boot, check=args.check)
    except (OSError, ValueError) as error:
        parser.exit(1, f'IMX500 configuration failed: {error}\n')
    print('IMX500 configuration ' + ('would change.' if args.check and changed else
                                    'updated; reboot pending.' if changed else 'already configured.'))


if __name__ == '__main__':
    main()
