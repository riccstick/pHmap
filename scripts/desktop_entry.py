"""PyInstaller entry point; do not freeze the scientific CLI interpreter."""

from phmap.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
