"""Package the exact self-contained public record verifier; --check refuses drift."""
from pathlib import Path
import argparse

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = (root/'tools/verify_goal_record.py').read_bytes()
    target = root/'src/connectthebots_server/goal_verifier.py'
    if args.check:
        if not target.is_file() or target.read_bytes() != source:
            parser.exit(1,'Packaged goal verifier differs; run site/tools/sync_goal_verifier.py\n')
        print('Packaged goal verifier matches the standalone source.')
    else:
        target.write_bytes(source)
        print('Packaged standalone goal verifier synchronized.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
