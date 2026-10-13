#!/usr/bin/env python3
"""Regenerate opaque 16-colour PNG previews from Windows ICO files.
Corrects 4-bpp ICO files with trailing bitmap padding that Pillow can mistake
for part of the AND transparency mask (black lines at the top of previews).
Run in the repository root: python3 regen_png_fixed.py
"""
from pathlib import Path
import struct
from PIL import Image

ICO_DIR = Path('ICO')
PNG_DIR = Path('PNG')
BACKGROUND = (255, 255, 255)
SIZE = (32, 32)


def read_four_bit_ico(path):
    b = path.read_bytes()
    if len(b) < 62 or struct.unpack_from('<HHH', b, 0)[:2] != (0, 1):
        return None
    count = struct.unpack_from('<H', b, 4)[0]
    frames = []
    for i in range(count):
        entry = 6 + i * 16
        if entry + 16 > len(b):
            break
        w, h, palette_count, _, planes, depth, length, start = struct.unpack_from('<BBBBHHII', b, entry)
        w, h = w or 256, h or 256
        if depth != 4 or start + length > len(b) or start + 40 > len(b):
            continue
        hdr = struct.unpack_from('<IiiHHIIiiII', b, start)
        hdrlen, dw, dh, _, bits, compression, *_ = hdr
        if bits != 4 or compression != 0 or dw != w or abs(dh) != 2 * h:
            continue
        colors = hdr[-2] or 16
        if not (1 <= colors <= 16):
            continue
        palstart = start + hdrlen
        xorstart = palstart + colors * 4
        xorstride = ((w * 4 + 31) // 32) * 4
        andstride = ((w + 31) // 32) * 4
        andstart = xorstart + xorstride * h
        if andstart + andstride * h > start + length:
            continue
        palette = [tuple(b[palstart + j * 4 + k] for k in (2, 1, 0)) for j in range(colors)]
        out = Image.new('RGB', (w, h), BACKGROUND)
        px = out.load()
        for y in range(h):
            iy = y if dh < 0 else h - 1 - y
            xr = xorstart + iy * xorstride
            ar = andstart + iy * andstride
            for x in range(w):
                # AND mask bit 1 means transparent.
                transparent = (b[ar + x // 8] >> (7 - (x % 8))) & 1
                if not transparent:
                    v = b[xr + x // 2]
                    idx = (v >> 4) if x % 2 == 0 else (v & 15)
                    px[x, y] = palette[idx] if idx < len(palette) else BACKGROUND
        frames.append(out)
    if not frames:
        return None
    return next((im for im in frames if im.size == SIZE), max(frames, key=lambda im: im.width * im.height))


def process(path):
    im = read_four_bit_ico(path)
    if im is None:
        with Image.open(path) as ico:
            if hasattr(ico, 'ico') and SIZE in ico.ico.sizes():
                im = ico.ico.getimage(SIZE)
            else:
                im = ico.copy()
            rgba = im.convert('RGBA')
            bg = Image.new('RGBA', rgba.size, BACKGROUND + (255,))
            bg.alpha_composite(rgba)
            im = bg.convert('RGB')
    if im.size != SIZE:
        im = im.resize(SIZE, Image.Resampling.NEAREST)
    # Already 16-colour in the usual 4-bpp case; quantizing guarantees <=16
    # palette entries for all fallback ICO formats.
    im = im.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    target = PNG_DIR / (path.stem + '.png')
    im.save(target, optimize=True)
    return target


def main():
    PNG_DIR.mkdir(exist_ok=True)
    files = sorted((p for p in ICO_DIR.iterdir() if p.is_file() and p.suffix.lower() == '.ico'), key=lambda p: p.name.lower())
    if not files:
        raise SystemExit('No ICO files found in ICO/')
    errors = []
    for idx, file in enumerate(files, 1):
        try:
            process(file)
        except Exception as e:
            errors.append((file.name, str(e)))
        if idx % 250 == 0:
            print(f'Processed {idx}/{len(files)}')
    print(f'Completed: {len(files) - len(errors)} converted, {len(errors)} failed')
    for name, err in errors:
        print('FAILED:', name, err)


if __name__ == '__main__':
    main()
