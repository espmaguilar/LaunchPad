"""Tiny dependency-free PDF writer: Helvetica text, a few line styles, multiple pages.
Used by make_lab_manual.py. Pages are lists of (style, text) tuples.

Styles: title, h1, h2, body, bullet, small, gap
"""
import textwrap
from pathlib import Path

OUT = Path(__file__).parent / "example.pdf"
CONTENT = [[("title", "Example"), ("body", "Pass your own pages to build(pages_content, out, title).")]]

STYLES = {  # font, size, leading, indent, wrap width (chars)
    "title": ("F2", 20, 28, 0, 60),
    "h1": ("F2", 13, 20, 0, 80),
    "h2": ("F2", 11.5, 20, 0, 85),
    "body": ("F1", 10, 14, 0, 95),
    "bullet": ("F1", 10, 14, 14, 90),
    "small": ("F1", 8.5, 12, 0, 115),
    "gap": ("F1", 10, 10, 0, 1),
}


def esc(t):
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def page_stream(lines):
    y, ops = 740, []
    for style, text in lines:
        font, size, lead, indent, width = STYLES[style]
        if style in ("h2",):
            y -= 6
        wrapped = textwrap.wrap(text, width) or [""]
        for i, part in enumerate(wrapped):
            x = 72 + indent
            if style == "bullet" and i == 0:
                ops.append(f"BT /F1 {size} Tf {72 + 4} {y} Td (-) Tj ET")
            ops.append(f"BT /{font} {size} Tf {x} {y} Td ({esc(part)}) Tj ET")
            y -= lead
        if style == "bullet":
            y -= 3
    return "\n".join(ops).encode("latin-1")


def build(pages_content=None, out=None, title="Document"):
    pages_content = pages_content or CONTENT
    out_path = Path(out or OUT)
    objs = []  # index 0 -> object 1

    def add(body):
        objs.append(body)
        return len(objs)

    catalog = add(None)
    pages = add(None)
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for lines in pages_content:
        stream = page_stream(lines)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(
            f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {content} 0 R >>".encode()))
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages} 0 R >>".encode()
    objs[pages - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                       f"/Count {len(kids)} >>").encode()

    out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R "
            f"/Info << /Title ({esc(title)}) >> >>\nstartxref\n{xref}\n%%EOF\n").encode()
    out_path.write_bytes(bytes(out))
    print(f"wrote {out_path} ({len(out):,} bytes, {len(pages_content)} pages)")


if __name__ == "__main__":
    build()
