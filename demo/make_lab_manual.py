"""Generate demo/color_change_lab.pdf: a one-page teaching-lab manual using SAFE
household items (water, food coloring, baking soda, vinegar, measuring spoon, clear cup).
Six visually distinct steps for testing LabScribe; step 1 is safety-critical (goggles).

Run: python demo/make_lab_manual.py
"""
from pathlib import Path

from pdfgen import build

OUT = Path(__file__).parent / "color_change_lab.pdf"

CONTENT = [[
    ("title", "Color-Change Reaction"),
    ("h1", "Teaching Lab - Student Procedure"),
    ("small", "Lab TL-04  |  Grade level: middle school  |  Time: 10 minutes  |  Household materials only"),
    ("gap", ""),
    ("h2", "Materials"),
    ("body", "Clear plastic cup, water, blue food coloring, baking soda, white vinegar, measuring spoon "
             "(1 teaspoon and 1 tablespoon), paper towels, safety goggles."),
    ("h2", "Safety"),
    ("body", "Wear safety goggles for the whole lab: vinegar stings if it splashes into your eyes. "
             "Do not taste any materials. Wipe up spills right away."),
    ("h2", "Procedure"),
    ("body", "Step 1: SAFETY - Put on safety goggles before handling any materials."),
    ("body", "Step 2: Fill the clear cup halfway with water."),
    ("body", "Step 3: Add 3 drops of blue food coloring to the water and stir with the spoon until the "
             "color is even."),
    ("body", "Step 4: Add 1 teaspoon of baking soda to the cup with the measuring spoon and stir until "
             "the white powder dissolves."),
    ("body", "Step 5: SAFETY - Keep goggles on. Slowly pour 2 tablespoons of vinegar into the cup and "
             "watch the mixture fizz and foam."),
    ("body", "Step 6: When the fizzing stops, observe the color and foam, then pour the mixture into "
             "the sink and rinse the cup."),
    ("h2", "Questions"),
    ("body", "What gas makes the bubbles? Did the blue color change when the vinegar was added?"),
]]

if __name__ == "__main__":
    build(CONTENT, OUT, title="Color-Change Reaction (Teaching Lab)")
