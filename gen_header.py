#!/usr/bin/env python3

from collections import Counter, OrderedDict, defaultdict, namedtuple
from functools import partial
from heapq import heapify, heappop, heappush
from math import ceil, log2
from multiprocessing import Pool
from os import path
from sys import stderr

import PIL.Image

SCRIPT_DIR = path.dirname(path.realpath(__file__))
VERSIONS_DIR = path.join(
    SCRIPT_DIR,
    "sprites",
    "sprites",
    "pokemon",
    "versions",
)
SPRITES = [
    # ("generation-iii", "emerald", 386, True),
    ("generation-iii", "firered-leafgreen", 2, True),
    # ("generation-iii", "ruby-sapphire", 386, True),
    # ("generation-iv", "diamond-pearl", 493, True),
    # ("generation-iv", "heartgold-soulsilver", 493, True),
    # ("generation-iv", "platinum", 493, True),
    # ("generation-v", "black-white", 650, True),
    # ("generation-vii", "icons", 807, False),
]
LZ77_FIELDS = ["dys", "dxs", "runlen", "values"]

Huffman = namedtuple("Huffman", ["bits", "form", "perm", "data2bits"])
Lz77 = namedtuple("Lz77", LZ77_FIELDS)


def pixel(sprite, shiny, x, y):
    r1, g1, b1, a1 = sprite.getpixel((x, y))
    r2, g2, b2, a2 = shiny.getpixel((x, y))
    if not a1 and not a2:
        return None, None
    # Some gen 3 sprites have inaccurate alpha channels.
    if not a1 or not a2:
        raise Exception("Bad alpha channel")
    return (r1 // 8, g1 // 8, b1 // 8), (r2 // 8, g2 // 8, b2 // 8)


def create_colormap(sprite, shiny):
    counter = Counter()
    n, m = sprite.size
    for y in range(m):
        for x in range(n):
            counter[pixel(sprite, shiny, x, y)] -= 1
    if len(counter) > 16:
        raise Exception("Excess colors in palette")
    del counter[(None, None)]
    colormap = OrderedDict({(None, None): 0})
    for _, color in sorted(zip(counter.values(), counter.keys())):
        colormap[color] = len(colormap)
    return colormap


def lz77(data, width, data2bits):
    def nbits(output):
        return sum(d2b[out] for out, d2b in zip(output, data2bits))

    n = len(data)
    dp = [(0, 0)] * n
    for i in reversed(range(n)):
        size, lst = dp[i + 1] if i + 1 < n else (0, None)
        out = Lz77(0, 128, 1, data[i])
        ans = (size + nbits(out), (out, lst))
        for j in range(i):
            for k in range(j, n - i + j):
                if data[k] != data[k + i - j]:
                    break
                runlen = k - j + 1
                y1, x1 = divmod(j, width)
                y2, x2 = divmod(i, width)
                dy, dx = y2 - y1, x2 - x1 + 128
                if not (0 <= dx < 256 and 0 <= dy < 256):
                    continue
                index = i + runlen + 1
                size, lst = dp[index] if index < n else (0, None)
                # TODO: don't output nxt if i + runlen == n
                nxt = data[i + runlen] if i + runlen < n else 0
                out = Lz77(dy, dx, runlen, nxt)
                ans = min(ans, (size + nbits(out), (out, lst)))
        dp[i] = ans

    node = dp[0][1]
    ans = []
    while node is not None:
        first, rest = node
        ans.append(first)
        node = rest
    return ans


def he(data):
    counter = Counter(data)
    shannon = 0
    total = sum(counter.values())
    for count in counter.values():
        p = count / total
        shannon -= count * log2(p)
    shannon = ceil(shannon)

    heap = [(counter[i], i, i) for i in range(256)]
    heapify(heap)
    while len(heap) > 1:
        c1, _, v1 = heappop(heap)
        c2, _, v2 = heappop(heap)
        heappush(heap, (c1 + c2, -len(heap), (v1, v2)))
    tree = heap[0][2]

    data2bits = {}
    acc = []
    form = []
    perm = []

    # TODO: only output non-zero counted values
    def dfs(node):
        if type(node) == int:
            form.append(1)
            data2bits[node] = acc[::]
            perm.append(node)
            return
        form.append(0)
        l, r = node
        acc.append(0)
        dfs(l)
        acc.pop()
        acc.append(1)
        dfs(r)
        acc.pop()

    dfs(tree)
    bits = [y for x in data for y in data2bits[x]]
    print(
        "%d/%d (+%.1fB) (+%.2f%%)"
        % (
            len(bits),
            shannon,
            (len(bits) - shannon) / 8,
            100 * (len(bits) / shannon - 1),
        ),
        file=stderr,
    )
    return Huffman(bits, form, perm, data2bits)


def byte_encode(bits):
    while len(bits) % 8 != 0:
        bits.append(0)
    print("{")
    for i in range(0, len(bits), 8):
        encoded = 0
        for bit in bits[i : i + 8]:
            encoded *= 2
            encoded += bit
        print("0x%02X," % encoded, end="")
    print("}")


def output_huffman(form, perm):
    print("{")
    byte_encode(list(form))
    print(",{")
    for x in perm:
        print("0x%02X," % x, end="")
    print("}}")


def read_image(sprites_dir, shiny_dir, id):
    basename = "%d.png" % (id + 1)
    pathname = path.join(sprites_dir, basename)
    sprite = PIL.Image.open(pathname).convert("RGBA")
    if shiny_dir:
        shiny_path = path.join(shiny_dir, basename)
        shiny = PIL.Image.open(shiny_path).convert("RGBA")
    else:
        shiny = sprite

    try:
        colormap = create_colormap(sprite, shiny)
    except Exception as e:
        print(e, "in", pathname, file=stderr)
        return None

    image = []
    xl, yl, xh, yh = sprite.getbbox()
    for y in range(yl, yh):
        for x in range(xl, xh):
            color = pixel(sprite, shiny, x, y)
            image.append(colormap[color])
    width = xh - xl
    return ((width, yh - yl), colormap, image)


def read_images(pool):
    images = []
    for gen, game, max_id, has_shiny in SPRITES:
        sprites_dir = path.join(VERSIONS_DIR, gen, game)
        shiny_dir = path.join(sprites_dir, "shiny") if has_shiny else None
        read = partial(read_image, sprites_dir, shiny_dir)
        images.extend(x for x in pool.map(read, range(max_id)) if x)
    return images


def compress_image(d2bs, input):
    (w, h), _, uncompressed = input
    return ((w, h), lz77(uncompressed, w, d2bs))


def compress_images(uncompressed, pool):
    colormaps = []
    for _, colormap, _ in uncompressed:
        regular_colormap = []
        shiny_colormap = []
        for regular, shiny in list(colormap)[1:]:
            regular_colormap.extend(regular)
            shiny_colormap.extend(shiny)
        colormaps.append((regular_colormap, shiny_colormap))
    colors = he([x for pairs in colormaps for colormap in pairs for x in colormap])

    d2bs = [[1] * 256] * len(LZ77_FIELDS)
    for _ in range(3):
        sizes, streams = zip(*pool.map(partial(compress_image, d2bs), uncompressed))

        all_streams = [[] for _ in range(len(LZ77_FIELDS))]
        for stream in streams:
            for t in stream:
                for i, x in enumerate(t):
                    all_streams[i].append(x)
        lz = Lz77(*pool.map(he, all_streams))

        d2bs = [[len(huffman.data2bits[d]) for d in range(256)] for huffman in lz]
        bitstreams = []
        bitlens = []
        for stream, colormap_pair in zip(streams, colormaps):
            bitstream = []
            for t in stream:
                for huffman, x in zip(lz, t):
                    bitstream.extend(huffman.data2bits[x])
            for colormap in colormap_pair:
                for value in colormap:
                    bitstream.extend(colors.data2bits[value])
            bitlens.append(len(bitstream))
            bitstreams.extend(bitstream)
        print("%.3fKB" % ((len(bitstreams) + 7) // 8 / 1000), file=stderr)
    return sizes, colors, bitstreams, bitlens, lz


def output(sizes, colors, bitstream, bitlens, lz):
    print('#include "types.h"')
    print("static const Sprite sprite_data[] = {")
    for (w, h), bitlen in zip(sizes, bitlens):
        print("{%d,%d,%d}," % (w, h, bitlen))
    print("};")
    print("static const uint8_t bitstream[] =")
    byte_encode(bitstream)
    print(";")
    print("const Sprites sprites = {")
    print("sprite_data,")
    print("%d," % len(sizes))
    print("{")
    for field in lz:
        output_huffman(field.form, field.perm)
        print(",")
    print("},")
    output_huffman(colors.form, colors.perm)
    print(",bitstream};")


def main():
    pool = Pool()
    uncompressed_images = read_images(pool)
    output(*compress_images(uncompressed_images, pool))


if __name__ == "__main__":
    main()
