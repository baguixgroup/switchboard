"""Tazrivo's Converge mark, shared by vector assets and native app icons."""
from __future__ import annotations
import re

NAME = 'Tazrivo'
GREEN = '#173D35'
COPPER = '#C87845'
IVORY = '#F8F5ED'
# Broad routes join a common stem. These same paths generate every logo size.
PATHS = (
    'M16 56 L16 40 Q16 24 32 24 L80 24 C104 24 120 45 128 72 C136 45 152 24 176 24 L224 24 Q240 24 240 40 L240 56 L184 56 C156 56 140 76 128 108 C116 76 100 56 72 56 Z',
    'M16 68 L70 68 C104 68 124 96 124 128 L124 232 L108 232 Q92 232 92 216 L92 134 C92 108 80 100 60 100 L48 100 C28 100 16 87 16 68 Z',
    'M240 68 L186 68 C152 68 132 96 132 128 L132 232 L148 232 Q164 232 164 216 L164 134 C164 108 176 100 196 100 L208 100 C228 100 240 87 240 68 Z',
)


def mark_svg(*, app_icon=False, monochrome=False, light=False):
    top = IVORY if light or app_icon else GREEN
    colors = [top] * 3 if monochrome else [top, COPPER, COPPER]
    background = f'<rect width="256" height="256" rx="55" fill="{GREEN}"/>' if app_icon else ''
    transform = ' transform="translate(28.16 28.16) scale(.78)"' if app_icon else ''
    paths = ''.join(f'<path d="{path}" fill="{color}"/>' for path, color in zip(PATHS, colors))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="Tazrivo Converge mark">{background}<g{transform}>{paths}</g></svg>\n'


def _polygon(path):
    tokens = re.findall(r'[MLCQZ]|-?\d+(?:\.\d+)?', path)
    points = []; x = y = 0.0; index = 0
    while index < len(tokens):
        command = tokens[index]; index += 1
        count = {'M':2, 'L':2, 'C':6, 'Q':4, 'Z':0}[command]
        values = list(map(float, tokens[index:index+count])); index += count
        if command in ('M', 'L'):
            x, y = values; points.append((x, y))
        elif command in ('C', 'Q'):
            start = (x, y)
            for step in range(1, 25):
                t = step / 24; u = 1-t
                if command == 'C':
                    x = u**3*start[0] + 3*u*u*t*values[0] + 3*u*t*t*values[2] + t**3*values[4]
                    y = u**3*start[1] + 3*u*u*t*values[1] + 3*u*t*t*values[3] + t**3*values[5]
                else:
                    x = u*u*start[0] + 2*u*t*values[0] + t*t*values[2]
                    y = u*u*start[1] + 2*u*t*values[1] + t*t*values[3]
                points.append((x, y))
    return points


def draw_icon(size=64, *, maskable=False):
    from PIL import Image, ImageDraw
    if not 1 <= size <= 2048:raise ValueError('Choose an icon size between 1 and 2048.')
    side = size*4
    image = Image.new('RGBA', (side, side), (0,0,0,0)); draw = ImageDraw.Draw(image)
    pad = 0 if maskable else side*.06
    draw.rounded_rectangle((pad,pad,side-pad,side-pad), radius=0 if maskable else side*.22, fill=GREEN)
    scale = .65 if maskable else .78; offset = (1-scale)/2
    for path, color in zip(PATHS, (IVORY,COPPER,COPPER)):
        points = [(side*(offset+x/256*scale),side*(offset+y/256*scale)) for x,y in _polygon(path)]
        draw.polygon(points,fill=color)
    return image.resize((size,size),Image.Resampling.LANCZOS)
