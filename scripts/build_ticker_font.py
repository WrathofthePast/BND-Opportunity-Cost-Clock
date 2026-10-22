#!/usr/bin/env python3
"""Build public/fonts/ticker-5x7.woff2: a dot-matrix web font in the style of the lighted-dot stock tickers.

Every character is drawn from round dots on a 5-wide, 7-tall grid (narrow punctuation uses fewer columns),
with one empty column between characters. Run once after changing a glyph:
    python scripts/build_ticker_font.py
Needs: pip install fonttools brotli
"""
import math
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'public' / 'fonts' / 'ticker-5x7.woff2'
PITCH = 100            # font units from one dot to the next
RADIUS = 44            # dot radius (small gaps between dots stay visible)

# Rows from top to bottom; '1' is a lit dot.
G = {
    '0': '01110 10001 10011 10101 11001 10001 01110', '1': '010 110 010 010 010 010 111',
    '2': '01110 10001 00001 00010 00100 01000 11111', '3': '11111 00010 00100 00010 00001 10001 01110',
    '4': '00010 00110 01010 10010 11111 00010 00010', '5': '11111 10000 11110 00001 00001 10001 01110',
    '6': '00110 01000 10000 11110 10001 10001 01110', '7': '11111 00001 00010 00100 01000 01000 01000',
    '8': '01110 10001 10001 01110 10001 10001 01110', '9': '01110 10001 10001 01111 00001 00010 01100',
    'A': '01110 10001 10001 11111 10001 10001 10001', 'B': '11110 10001 10001 11110 10001 10001 11110',
    'C': '01110 10001 10000 10000 10000 10001 01110', 'D': '11100 10010 10001 10001 10001 10010 11100',
    'E': '11111 10000 10000 11110 10000 10000 11111', 'F': '11111 10000 10000 11110 10000 10000 10000',
    'G': '01110 10001 10000 10111 10001 10001 01111', 'H': '10001 10001 10001 11111 10001 10001 10001',
    'I': '01110 00100 00100 00100 00100 00100 01110', 'J': '00111 00010 00010 00010 00010 10010 01100',
    'K': '10001 10010 10100 11000 10100 10010 10001', 'L': '10000 10000 10000 10000 10000 10000 11111',
    'M': '10001 11011 10101 10101 10001 10001 10001', 'N': '10001 10001 11001 10101 10011 10001 10001',
    'O': '01110 10001 10001 10001 10001 10001 01110', 'P': '11110 10001 10001 11110 10000 10000 10000',
    'Q': '01110 10001 10001 10001 10101 10010 01101', 'R': '11110 10001 10001 11110 10100 10010 10001',
    'S': '01111 10000 10000 01110 00001 00001 11110', 'T': '11111 00100 00100 00100 00100 00100 00100',
    'U': '10001 10001 10001 10001 10001 10001 01110', 'V': '10001 10001 10001 10001 10001 01010 00100',
    'W': '10001 10001 10001 10101 10101 10101 01010', 'X': '10001 10001 01010 00100 01010 10001 10001',
    'Y': '10001 10001 10001 01010 00100 00100 00100', 'Z': '11111 00001 00010 00100 01000 10000 11111',
    'a': '00000 00000 01110 00001 01111 10001 01111', 'b': '10000 10000 10110 11001 10001 10001 11110',
    'c': '00000 00000 01110 10000 10000 10001 01110', 'd': '00001 00001 01101 10011 10001 10001 01111',
    'e': '00000 00000 01110 10001 11111 10000 01110', 'f': '00110 01001 01000 11100 01000 01000 01000',
    'g': '00000 01111 10001 10001 01111 00001 01110', 'h': '10000 10000 10110 11001 10001 10001 10001',
    'i': '00100 00000 01100 00100 00100 00100 01110', 'j': '00010 00000 00110 00010 00010 10010 01100',
    'k': '10000 10000 10010 10100 11000 10100 10010', 'l': '01100 00100 00100 00100 00100 00100 01110',
    'm': '00000 00000 11010 10101 10101 10001 10001', 'n': '00000 00000 10110 11001 10001 10001 10001',
    'o': '00000 00000 01110 10001 10001 10001 01110', 'p': '00000 00000 11110 10001 11110 10000 10000',
    'q': '00000 00000 01101 10011 01111 00001 00001', 'r': '00000 00000 10110 11001 10000 10000 10000',
    's': '00000 00000 01110 10000 01110 00001 11110', 't': '01000 01000 11100 01000 01000 01001 00110',
    'u': '00000 00000 10001 10001 10001 10011 01101', 'v': '00000 00000 10001 10001 10001 01010 00100',
    'w': '00000 00000 10001 10001 10101 10101 01010', 'x': '00000 00000 10001 01010 00100 01010 10001',
    'y': '00000 00000 10001 10001 01111 00001 01110', 'z': '00000 00000 11111 00010 00100 01000 11111',
    '$': '00100 01111 10100 01110 00101 11110 00100', '+': '00000 00100 00100 11111 00100 00100 00000',
    '-': '00000 00000 00000 11111 00000 00000 00000', '%': '11000 11001 00010 00100 01000 10011 00011',
    '/': '00000 00001 00010 00100 01000 10000 00000', '?': '01110 10001 00001 00010 00100 00000 00100',
    '=': '00000 00000 11111 00000 11111 00000 00000', '#': '01010 01010 11111 01010 11111 01010 01010',
    '&': '01100 10010 10100 01000 10101 10010 01101', '*': '00000 00100 10101 01110 10101 00100 00000',
    '<': '00010 00100 01000 10000 01000 00100 00010', '>': '01000 00100 00010 00001 00010 00100 01000',
    ',': '000 000 000 000 110 010 100', '.': '00 00 00 00 00 11 11', ':': '00 11 11 00 11 11 00',
    ';': '000 011 011 000 011 001 010', "'": '11 01 10 00 00 00 00', '!': '1 1 1 1 1 0 1',
    '(': '001 010 100 100 100 010 001', ')': '100 010 001 001 001 010 100', ' ': '000 000 000 000 000 000 000',
}
# Other characters drawn with an existing glyph
ALIAS = {'−': '-', '–': '-', '—': '-', '’': "'", '‘': "'", ' ': ' '}


def circle(pen, cx, cy, r):
    """A closed all-off-curve quadratic contour (8 control points) that traces a circle, clockwise."""
    k = r / math.cos(math.pi / 8)
    pts = [(round(cx + k * math.cos(-i * math.pi / 4)), round(cy + k * math.sin(-i * math.pi / 4))) for i in range(8)]
    pen.qCurveTo(*pts, None)
    pen.closePath()


def glyph(rows):
    pen = TTGlyphPen(None)
    grid = rows.split()
    for j, row in enumerate(grid):
        for i, bit in enumerate(row):
            if bit == '1':
                circle(pen, i * PITCH + PITCH // 2, (len(grid) - 1 - j) * PITCH + PITCH // 2, RADIUS)
    return pen.glyph(), (len(grid[0]) + 1) * PITCH


def main():
    names = {ch: f'uni{ord(ch):04X}' for ch in G}
    order = ['.notdef'] + [names[c] for c in G]
    glyphs, metrics = {}, {}
    pen = TTGlyphPen(None)
    glyphs['.notdef'], metrics['.notdef'] = pen.glyph(), (600, 0)
    for ch, rows in G.items():
        g, adv = glyph(rows)
        glyphs[names[ch]] = g
        metrics[names[ch]] = (adv, 0)
    cmap = {ord(c): names[c] for c in G}
    cmap.update({ord(a): names[t] for a, t in ALIAS.items()})

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=850, descent=-150)
    fb.setupNameTable({'familyName': 'Ticker 5x7', 'styleName': 'Regular'})
    fb.setupOS2(sTypoAscender=850, sTypoDescender=-150, usWinAscent=850, usWinDescent=150,
                sCapHeight=700, sxHeight=500, achVendID='NONE')
    fb.setupPost()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fb.font.flavor = 'woff2'
    fb.save(str(OUT))
    print(f'{OUT.relative_to(ROOT)}: {len(G)} glyphs, {OUT.stat().st_size} bytes')


if __name__ == '__main__':
    main()
