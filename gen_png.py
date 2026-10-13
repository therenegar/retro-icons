#!/usr/bin/env python3
"""Convert Windows ICO files to solid-background PNG previews.

Directly handles uncompressed classic Windows DIB icons (1/4/8/24/32 bpp),
including files with extra trailing padding that can confuse Pillow's masks.
PNG-compressed frames and unfamiliar ICO formats use Pillow as fallback.

Usage (from repo root):
    python3 regen_png_fixed_v2.py
    python3 regen_png_fixed_v2.py --colors 16
    python3 regen_png_fixed_v2.py --background FFFFFF --size 32
"""
import argparse
from pathlib import Path
import struct
from PIL import Image


def ico_frames(path):
    data = path.read_bytes()
    if len(data) < 6 or struct.unpack_from('<HHH', data, 0)[:2] != (0, 1):
        raise ValueError('Not a Windows ICO')
    n = struct.unpack_from('<H', data, 4)[0]
    if 6 + n * 16 > len(data):
        raise ValueError('Truncated ICO directory')
    for i in range(n):
        at = 6 + 16 * i
        w, h, _, _, planes, bits, length, start = struct.unpack_from('<BBBBHHII', data, at)
        w, h = w or 256, h or 256
        if start + length > len(data) or length < 40:
            continue
        yield (w, h, bits, data[start:start + length])


def decode_dib(w, h, frame, bg):
    if frame[:8] == b'\x89PNG\r\n\x1a\n':
        with Image.open(__import__('io').BytesIO(frame)) as png:
            return flatten(png.convert('RGBA'), bg)
    if len(frame) < 40:
        return None
    size, dw, dh, planes, bpp, compression, image_size, _, _, clr_used, _ = struct.unpack_from('<IiiHHIIiiII', frame, 0)
    if size < 40 or size > len(frame) or dw != w or abs(dh) != h * 2:
        return None
    if planes != 1 or bpp not in (1, 4, 8, 24, 32) or compression != 0:
        return None
    palette_count = (clr_used or (1 << bpp)) if bpp <= 8 else 0
    if palette_count > (1 << bpp):
        return None
    xor_offset = size + 4 * palette_count
    xor_stride = ((w * bpp + 31) // 32) * 4
    and_stride = ((w + 31) // 32) * 4
    mask_offset = xor_offset + xor_stride * h
    if mask_offset + and_stride * h > len(frame):
        return None
    pal = [tuple(frame[size + 4*i + j] for j in (2, 1, 0)) for i in range(palette_count)]
    out = Image.new('RGB', (w, h), bg)
    pixels = out.load()
    bottom_up = dh > 0
    for y in range(h):
        yy = h - y - 1 if bottom_up else y
        xr, ar = xor_offset + yy*xor_stride, mask_offset + yy*and_stride
        for x in range(w):
            masked = (frame[ar + x//8] >> (7 - (x % 8))) & 1
            if masked:
                continue
            if bpp == 1:
                idx = (frame[xr + x//8] >> (7 - x%8)) & 1
                color = pal[idx]
            elif bpp == 4:
                v = frame[xr + x//2]
                idx = (v >> 4) if x%2 == 0 else (v & 15)
                color = pal[idx] if idx < len(pal) else bg
            elif bpp == 8:
                idx = frame[xr+x]
                color = pal[idx] if idx < len(pal) else bg
            elif bpp == 24:
                z = xr + 3*x
                color = frame[z+2], frame[z+1], frame[z]
            else:
                z = xr + 4*x
                alpha = frame[z+3]
                rgb = frame[z+2], frame[z+1], frame[z]
                # Respect embedded alpha for 32-bit icons; mask is already applied.
                color = tuple((rgb[c]*alpha + bg[c]*(255-alpha)+127)//255 for c in range(3))
            pixels[x, y] = color
    return out


def flatten(im, bg):
    rgba = im.convert('RGBA')
    base = Image.new('RGBA', rgba.size, (*bg, 255))
    base.alpha_composite(rgba)
    return base.convert('RGB')


def process(path, png_dir, size, bg, colors):
    frames = list(ico_frames(path))
    choices = sorted(frames, key=lambda f: (f[0:2] == (size, size), min(f[0], f[1]), f[2]), reverse=True)
    picture = None
    for w, h, bits, buf in choices:
        try:
            picture = decode_dib(w, h, buf, bg)
            if picture is not None:
                break
        except (ValueError, IndexError, struct.error):
            continue
    if picture is None:
        # Fallback for unusual ICO variants.
        with Image.open(path) as ico:
            if hasattr(ico, 'ico') and (size, size) in ico.ico.sizes():
                picture = flatten(ico.ico.getimage((size, size)), bg)
            else:
                picture = flatten(ico, bg)
    if picture.size != (size, size):
        picture = picture.resize((size, size), Image.Resampling.NEAREST)
    if colors:
        picture = picture.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    dst = png_dir / (path.stem + '.png')
    picture.save(dst, format='PNG', optimize=True)
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ico-dir', type=Path, default=Path('ICO'))
    ap.add_argument('--png-dir', type=Path, default=Path('PNG'))
    ap.add_argument('--size', type=int, default=32)
    ap.add_argument('--background', default='FFFFFF', help='six-digit RGB hex colour')
    ap.add_argument('--colors', type=int, choices=(16, 256), default=256,
                    help='maximum PNG palette size (default: 256; use 16 for old previews)')
    args = ap.parse_args()
    if len(args.background) != 6:
        ap.error('--background must be RRGGBB')
    try:
        bg = tuple(bytes.fromhex(args.background))
    except ValueError:
        ap.error('--background must be RRGGBB')
    args.png_dir.mkdir(parents=True, exist_ok=True)
    files = sorted((p for p in args.ico_dir.iterdir() if p.is_file() and p.suffix.lower()=='.ico'), key=lambda p:p.name.lower())
    errors=[]
    for i,p in enumerate(files,1):
        try: process(p,args.png_dir,args.size,bg,args.colors)
        except Exception as e: errors.append((p.name,str(e)))
        if i%250==0:print(f'{i}/{len(files)} processed')
    print(f'Done: {len(files)-len(errors)} PNGs, {len(errors)} failures')
    for p,e in errors:print('FAILED',p,e)

if __name__ == '__main__':main()
