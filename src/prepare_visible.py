"""Copy only public evaluation files from the assignment archive, never .env."""

import argparse
from pathlib import Path
from zipfile import ZipFile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("work/visible"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.archive) as archive:
        for name in ("items.json", "visible_key.json", "score.py"):
            path = args.out_dir / name
            path.write_bytes(archive.read("candidate_package/" + name))
            print(path)


if __name__ == "__main__":
    main()
