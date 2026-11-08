"""Generate demo/sample_safety_policy.pdf - a realistic, FICTIONAL company PPE policy
for demoing policy ingestion. No dependencies (writes raw PDF).

Run: python demo/make_sample_policy.py
"""
import textwrap
from pathlib import Path

OUT = Path(__file__).parent / "sample_safety_policy.pdf"

# (style, text). Styles: title, h1, h2, body, bullet, small, gap
CONTENT = [
    [
        ("title", "BRIGHTWATER LABS"),
        ("h1", "Workplace Safety Policy - Personal Protective Equipment (PPE)"),
        ("small", "Policy No. EHS-200  |  Revision 3  |  Effective January 6, 2026  |  Owner: EHS Department"),
        ("small", "Fictional company - sample document for SafeSight demos."),
        ("gap", ""),
        ("h2", "1. Purpose"),
        ("body", "This policy defines the personal protective equipment required in each work area of "
                 "the Brightwater Labs facility, the regulation each requirement is based on, and the "
                 "potential penalty exposure associated with non-compliance."),
        ("h2", "2. Scope"),
        ("body", "Applies to all employees, contractors, and visitors entering the work areas listed in "
                 "Section 4. Office areas and break rooms have no PPE requirements."),
        ("h2", "3. Enforcement and Penalty Exposure"),
        ("body", "Area supervisors are responsible for enforcing this policy. Where a dollar amount is "
                 "listed in Section 4, it is the potential fine exposure the company has assessed for that "
                 "requirement. Where no amount is listed, the current OSHA maximum penalty for the cited "
                 "standard applies. Amounts describe potential exposure, not fines that have been assessed."),
        ("h2", "4. Zone Requirements"),
        ("h2", "4.1 Chemistry Laboratory (Chem Lab)"),
        ("body", "Supervisor: chemlab.supervisor@brightwaterlabs.example"),
        ("bullet", "Chemical-resistant nitrile gloves at all times when handling reagents. "
                   "Ref: Sec. 4.1(a), 29 CFR 1910.138. Severity: Serious. Potential fine: $18,500 per violation."),
        ("bullet", "Half-face respirator or N95 mask when working with volatile solvents or powders. "
                   "Ref: Sec. 4.1(b), 29 CFR 1910.134. Severity: Serious. Potential fine: $16,550."),
        ("bullet", "Indirect-vent chemical splash goggles. Ref: Sec. 4.1(c), 29 CFR 1910.133. "
                   "Severity: Serious. Potential fine: $16,550."),
    ],
    [
        ("h2", "4.2 Fabrication Shop"),
        ("body", "Supervisor: fab.supervisor@brightwaterlabs.example"),
        ("bullet", "Hard hat (ANSI Z89.1 Type I) whenever overhead cranes are in operation. "
                   "Ref: Sec. 4.2(a), 29 CFR 1910.135. Severity: Serious. Potential fine: $12,000 per violation."),
        ("bullet", "Cut-resistant gloves (ANSI A4 or higher) when handling sheet metal. "
                   "Ref: Sec. 4.2(b), 29 CFR 1910.138. Severity: Serious."),
        ("bullet", "Safety glasses with side shields. Ref: Sec. 4.2(c), 29 CFR 1910.133."),
        ("h2", "4.3 Clean Room"),
        ("body", "Supervisor: cleanroom.lead@brightwaterlabs.example"),
        ("bullet", "Surgical face mask covering nose and mouth. Ref: Sec. 4.3(a)."),
        ("bullet", "Head cover (bouffant cap) with all hair contained. Ref: Sec. 4.3(b)."),
        ("bullet", "Nitrile gloves, changed on every entry. Ref: Sec. 4.3(c), 29 CFR 1910.138. "
                   "Potential fine: $9,500."),
        ("h2", "4.4 Loading Dock"),
        ("body", "Supervisor: dock.supervisor@brightwaterlabs.example"),
        ("bullet", "Hard hat during all forklift and truck loading operations. Ref: Sec. 4.4(a), "
                   "29 CFR 1910.135. Severity: Serious."),
        ("bullet", "High-visibility safety vest (ANSI Class 2). Ref: Sec. 4.4(b). "
                   "Severity: Other-than-serious. Potential fine: $4,000."),
        ("bullet", "Steel-toe safety boots. Ref: Sec. 4.4(c), 29 CFR 1910.136. Severity: Serious."),
        ("h2", "5. Reporting"),
        ("body", "Missing or damaged PPE must be reported to the area supervisor immediately. Replacement "
                 "PPE is available at the stations at each zone entrance."),
        ("gap", ""),
        ("small", "Approved by: J. Rivera, Director of Environment, Health & Safety  |  Next review: January 2027"),
    ],
]

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


def build(pages_content=None, out=None, title="Brightwater Labs PPE Policy EHS-200"):
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
