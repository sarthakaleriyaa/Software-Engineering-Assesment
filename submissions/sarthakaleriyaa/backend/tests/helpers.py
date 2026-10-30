"""Shared helpers for tests."""


def _text_stream(text: str) -> bytes:
    lines = []
    for line in text.split("\n"):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"({safe}) Tj T*")
    return f"BT /F1 11 Tf 14 TL 60 740 Td {' '.join(lines)} ET".encode("latin-1")


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal, valid multi-page PDF; newlines in a page become lines.

    Hand-rolled so tests need no PDF-writing dependency.
    """
    objects: list[bytes] = []
    page_ids = []
    font_id = 3 + 2 * len(pages)

    for index, text in enumerate(pages):
        page_id = 3 + 2 * index
        content_id = page_id + 1
        page_ids.append(page_id)
        stream = _text_stream(text)
        objects.append(
            (
                f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>\nendobj\n"
            ).encode()
        )
        objects.append(
            f"{content_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream\nendobj\n"
        )

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    header = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>\nendobj\n".encode(),
    ]
    font = (
        f"{font_id} 0 obj\n<< /Type /Font /Subtype /Type1 "
        "/BaseFont /Helvetica >>\nendobj\n"
    ).encode()

    body = header + objects + [font]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in body:
        offsets.append(len(out))
        out += obj

    xref_at = len(out)
    out += f"xref\n0 {len(body) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def signup_and_login(client, email: str, password: str = "CorrectPassword123") -> str:
    response = client.post("/auth/signup", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
