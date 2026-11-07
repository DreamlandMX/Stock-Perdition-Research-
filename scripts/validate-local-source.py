from __future__ import annotations

import pathlib
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("repository_root_required")

    repository_root = pathlib.Path(sys.argv[1]).resolve()
    backend_src = (repository_root / "backend" / "src").resolve()

    from stock_prediction.opend import history

    module_path = pathlib.Path(history.__file__).resolve()
    module_path.relative_to(backend_src)
    reader = getattr(history, "OpenDHistoryReader", None)
    if reader is None or not callable(getattr(reader, "iter_pages", None)):
        raise RuntimeError("iter_pages_missing")


if __name__ == "__main__":
    main()
