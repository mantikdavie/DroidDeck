import argparse
import hashlib
import json
from pathlib import Path


PROFILES = {
    'steam-only': ('proot', 'directaudio', 'tracking', 'powervr'),
    'all-stores': ('proot', 'directaudio', 'tracking', 'xbox', 'comet', 'butler', 'powervr'),
}


def files(directory, profile):
    if profile not in PROFILES:
        raise ValueError('Unknown add-on pack profile: ' + str(profile))
    expected = {name + '.tar.zst' for name in PROFILES[profile]}
    actual = {path.name for path in directory.glob('*.tar.zst')}
    missing = expected - actual
    if missing:
        raise ValueError('Missing add-on pack ' + sorted(missing)[0].removesuffix('.tar.zst'))
    if actual != expected:
        raise ValueError('Unexpected add-on packs for ' + profile + ': ' + ', '.join(sorted(actual - expected)))
    result = {}
    for name in PROFILES[profile]:
        path = directory / (name + '.tar.zst')
        if path.is_symlink() or not path.is_file():
            raise ValueError('Add-on pack is not a regular file: ' + name)
        checksum = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                checksum.update(block)
        result[name] = {'sha256': checksum.hexdigest(), 'size': path.stat().st_size}
    return result


def record(directory, profile):
    data = {'version': 1, 'profile': profile, 'packs': files(directory, profile)}
    (directory / 'profile.json').write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')


def verify(directory, expected_profile=None):
    try:
        data = json.loads((directory / 'profile.json').read_text())
    except (OSError, ValueError) as error:
        raise ValueError('Add-on pack profile has not been prepared. Run tools/packs/build.sh.') from error
    profile = data.get('profile')
    if data.get('version') != 1 or profile not in PROFILES:
        raise ValueError('Invalid add-on pack profile')
    if expected_profile is not None and profile != expected_profile:
        raise ValueError('Add-on pack profile does not match ' + expected_profile)
    current = files(directory, profile)
    if data.get('packs') != current:
        raise ValueError('Add-on pack checksums or sizes do not match profile.json')
    return tuple(current)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Record and verify the complete add-on pack profile.')
    parser.add_argument('action', choices=('record', 'verify'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--profile', choices=PROFILES)
    args = parser.parse_args()
    try:
        if args.action == 'record':
            if args.profile is None:
                parser.error('record requires --profile')
            record(args.directory, args.profile)
        print(' '.join(verify(args.directory, args.profile)))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
