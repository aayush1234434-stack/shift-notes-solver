"""Import entrypoint so ./run also works from outside the repository."""

from src.pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
