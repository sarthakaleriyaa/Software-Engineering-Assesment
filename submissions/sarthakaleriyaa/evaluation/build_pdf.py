"""Regenerate documents/kestrel_it_security_policy.pdf from security_policy_pages.txt.

Pages in the source file are separated by a line containing only "=====".
Run from the submission folder:  python evaluation/build_pdf.py
"""

import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from tests.helpers import make_pdf  # noqa: E402


def main() -> None:
    source = (HERE / "security_policy_pages.txt").read_text(encoding="utf-8")
    pages = []
    for raw_page in source.split("\n=====\n"):
        lines = []
        for paragraph in raw_page.strip().split("\n"):
            lines.extend(textwrap.wrap(paragraph, 85) or [""])
        pages.append("\n".join(lines))

    target = HERE / "documents" / "kestrel_it_security_policy.pdf"
    target.write_bytes(make_pdf(pages))
    print(f"Wrote {target.name} with {len(pages)} pages")


if __name__ == "__main__":
    main()
