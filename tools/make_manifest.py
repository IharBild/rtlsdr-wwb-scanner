"""Write tools/manifest_<release>.json for the wwb_scanner folder of this
repository:  python tools/make_manifest.py V22.0"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qa_common  # noqa: E402


def main(release):
    root = os.path.join(os.path.dirname(HERE), 'wwb_scanner')
    files = qa_common.make_manifest(root)
    path = os.path.join(HERE, 'manifest_%s.json' % release)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'release': release, 'files': files}, f, indent=1, sort_keys=True)
        f.write('\n')
    print('%s: %d files' % (path, len(files)))


if __name__ == '__main__':
    main(sys.argv[1])
