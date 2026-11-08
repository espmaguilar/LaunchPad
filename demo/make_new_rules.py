"""Generate demo/new_rules.pdf: a one-page FICTIONAL policy of simple, camera-checkable
conduct rules for testing AI-vision rules live (gestures, phones, headphones, walking).

Run: python demo/make_new_rules.py
"""
from pathlib import Path

from make_sample_policy import build

OUT = Path(__file__).parent / "new_rules.pdf"

CONTENT = [[
    ("title", "NEW RULES"),
    ("h1", "Test Booth Conduct Rules - AI Vision Demo"),
    ("small", "Policy No. TB-001  |  Effective November 7, 2026  |  Fictional - for testing SafeSight AI-vision rules."),
    ("gap", ""),
    ("h2", "1. Purpose"),
    ("body", "These rules exist to test camera-based checks of behaviour, not PPE. Each rule can be judged "
             "from a single camera frame, except rule 2.6, which tests the limits of still-image checks."),
    ("h2", "2. Test Booth"),
    ("body", "Supervisor: booth.supervisor@brightwaterlabs.example"),
    ("bullet", "2.1 Do not show the number two with your fingers (two raised fingers, peace sign). "
               "Ref: TB-001 Sec. 2.1. Severity: Other-than-serious. Potential fine: $250."),
    ("bullet", "2.2 No raised hands: hands must stay below shoulder height (no waving). "
               "Ref: TB-001 Sec. 2.2. Severity: Other-than-serious. Potential fine: $200."),
    ("bullet", "2.3 No phones in use (holding or looking at a phone). Ref: TB-001 Sec. 2.3. "
               "Severity: Serious. Potential fine: $500."),
    ("bullet", "2.4 No headphones or earbuds. Ref: TB-001 Sec. 2.4. Severity: Other-than-serious. "
               "Potential fine: $300."),
    ("bullet", "2.5 Do not cover your face with your hands. Ref: TB-001 Sec. 2.5. "
               "Severity: Other-than-serious. Potential fine: $150."),
    ("bullet", "2.6 No walking inside the booth: remain standing still or seated. Ref: TB-001 Sec. 2.6. "
               "Severity: Other-than-serious. Potential fine: $100."),
    ("h2", "3. Penalties"),
    ("body", "Amounts are potential fine exposure for demo purposes only, not assessed fines."),
]]

if __name__ == "__main__":
    build(CONTENT, OUT, title="New Rules - Test Booth TB-001")
