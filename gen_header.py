#!/usr/bin/env python3

from collections import Counter, defaultdict, namedtuple
from functools import partial
from heapq import heapify, heappop, heappush
from json import load
from math import ceil, log2
from multiprocessing import Pool
from os import path
from sys import stderr

import PIL.Image

SCRIPT_DIR = path.dirname(path.realpath(__file__))
ASSETS_DIR = "/home/tom/dev/local/pokemon-sprites"
MONTAGES = set(["firered", "ruby", "emerald"])

Huffman = namedtuple("Huffman", ["form", "perm", "data2bits"])


def create_palette(sprite, shiny):
    counter = Counter()
    for pair in zip(sprite, shiny):
        counter[pair] -= 1
    if len(counter) > 16:
        raise Exception("Excess colors in palette")
    transparent = ((-1, -1, -1), (-1, -1, -1))
    del counter[transparent]
    palette = {transparent: 0}
    for _, color in sorted(zip(counter.values(), counter.keys())):
        palette[color] = len(palette)
    return palette


def lz77(data, size, data2bits):
    width, _, _ = size
    # TODO: multiple representations of (dy, dx) for the same delta.
    def nbits(output):
        return sum((d2b[out] if out >= 0 else 0) for out, d2b in zip(output, data2bits))

    n = len(data)
    dp: list[tuple[int, tuple[int, int, int, int], int]] = [(0, (-1,) * 4, -1)] * n
    for i in reversed(range(n)):
        size, tail = (dp[i + 1][0], i + 1) if i + 1 < n else (0, -1)
        out = (-1 if i < width else 0, -1 if i == 0 else 128, -1, data[i])
        ans = (size + nbits(out), out, tail)
        for j in range(i):
            for k in range(j, n - i + j):
                if data[k] != data[k + i - j]:
                    break
                runlen = k - j + 1
                if runlen >= 256:
                    break
                y1, x1 = divmod(j, width)
                y2, x2 = divmod(i, width)
                dy, dx = y2 - y1, x2 - x1 + 128
                if not (0 <= dx < 256 and 0 <= dy < 256):
                    continue
                index = i + runlen + 1
                size, tail = (dp[index][0], index) if index < n else (0, -1)
                out = (
                    dy if y2 else -1,
                    dx if y2 or x2 else -1,
                    runlen if dy or dx else -1,
                    data[i + runlen] if i + runlen < n else -1,
                )
                ans = min(ans, (size + nbits(out), out, tail))
        dp[i] = ans

    node = 0
    ans = []
    while node >= 0:
        _, first, node = dp[node]
        ans.append(first)
    return ans


def huffman_encode(data):
    counter = Counter(data)
    heap = [(counter[i], i) for i in range(256)]
    heapify(heap)
    nodes: list[tuple[int, int, int]] = [(i, -1, -1) for i in range(256)]
    while len(heap) > 1:
        c1, v1 = heappop(heap)
        c2, v2 = heappop(heap)
        heappush(heap, (c1 + c2, len(nodes)))
        nodes.append((-1, v1, v2))

    data2bits = {}
    acc = []
    form = []
    perm = []

    # TODO: only output non-zero counted values
    def dfs(node: tuple[int, int, int]):
        val = node[0]
        if val >= 0:
            form.append(1)
            data2bits[val] = acc[::]
            perm.append(val)
            return
        form.append(0)
        _, l, r = node
        acc.append(0)
        dfs(nodes[l])
        acc.pop()
        acc.append(1)
        dfs(nodes[r])
        acc.pop()

    dfs(nodes[-1])

    total = sum(counter.values())
    shannon = total * log2(total)
    bitlen = 0
    for x, count in counter.items():
        shannon -= count * log2(count)
        bitlen += count * len(data2bits[x])
    print(
        "%d/%d (+%.1fB) (+%.2f%%)"
        % (
            bitlen,
            ceil(shannon),
            (bitlen - shannon) / 8,
            100 * (bitlen / shannon - 1),
        ),
        file=stderr,
    )
    return Huffman(form, perm, data2bits)


def pixel(montage, x, y):
    r, g, b, a = montage.getpixel((x, y))
    return (r // 8, g // 8, b // 8) if a else (-1, -1, -1)


def read_images():
    cmm = {(i, j): 0 for i in range(16) for j in range(16)}
    metadata = load(open(path.join(ASSETS_DIR, "metadata.json")))
    images = []
    for (w, h), group in metadata:
        spritess = defaultdict(list)
        for name, variants_id, variant_counts in group:
            if name not in MONTAGES:
                continue
            montage = PIL.Image.open(path.join(ASSETS_DIR, name + ".png")).convert(
                "RGBA"
            )
            row = 0
            for i, variant_count in enumerate(variant_counts):
                # TODO: remove
                if i >= 1:
                    continue
                for _ in range(variant_count):
                    data = []
                    shiny = []
                    for y in range(h):
                        for x in range(w):
                            data.append(pixel(montage, x, y + h * row))
                            shiny.append(pixel(montage, x + w, y + h * row))

                    palette = create_palette(data, shiny)
                    sprite = [palette[colors] for colors in zip(data, shiny)]
                    spritess[i].append((sprite, palette))

                    row += 1
        for sprites in spritess.values():
            xl = yl = 255
            xh = yh = 0
            for sprite, _ in sprites:
                for y in range(h):
                    for x in range(w):
                        if sprite[y * w + x]:
                            xl = min(xl, x)
                            yl = min(yl, y)
                            xh = max(xh, x)
                            yh = max(yh, y)
            for sprite, _ in sprites:
                sprite[:] = [
                    sprite[y * w + x]
                    for y in range(yl, yh + 1)
                    for x in range(xl, xh + 1)
                ]
            n = len(sprites)
            edges = []
            for i in range(n):
                for j in range(i + 1, n):
                    for (c1, c2), count in (
                        cmm | Counter(zip(sprites[i][0], sprites[j][0]))
                    ).items():
                        edges.append((-count, i, j, c1, c2))
            edges.sort()
            covered_from = set()
            covered_to = set()
            msf = []
            for _, i, j, c1, c2 in edges:
                if (i, j, c1) in covered_from or (i, j, c2) in covered_to:
                    continue
                covered_from.add((i, j, c1))
                covered_to.add((i, j, c2))
                msf.append((i, j, c1, c2))
            perms = [list(range(16))] + [[16] * 16 for _ in range(n - 1)]
            for i, j, c1, c2 in sorted(msf):
                perms[j][c2] = perms[i][c1]
            image_stream = []
            palettes = []
            for perm, (sprite, palette) in zip(perms, sprites):
                inv = dict(zip(perm, range(16)))
                image = [inv[c] for c in sprite]
                image_stream.extend(image)
                p = list(palette.keys())
                for key, c in palette.items():
                    p[c] = key
                palettes.extend(p[1 : max(image) + 1])
            size = (xh - xl + 1, yh - yl + 1, n)
            images.append((size, image_stream, palettes))
    return images


def compress_image(d2bs, input):
    size, uncompressed, _ = input
    return (size, lz77(uncompressed, size, d2bs))


def compress_images(uncompressed, pool):
    palettes = []
    for _, _, palette in uncompressed:
        regular_palette = []
        shiny_palette = []
        for regular, shiny in palette:
            regular_palette.extend(regular)
            shiny_palette.extend(shiny)
        palettes.append((regular_palette, shiny_palette))
    colors = huffman_encode(
        [x for pairs in palettes for palette in pairs for x in palette]
    )

    LZ77_LEN = 4
    d2bs = [[1] * 256] * LZ77_LEN
    for _ in range(3):
        sizes, streams = zip(*pool.map(partial(compress_image, d2bs), uncompressed))
        # TODO: repaletteize based on value stream.

        all_streams = [[] for _ in range(LZ77_LEN)]
        for stream in streams:
            for t in stream:
                for i, x in enumerate(t):
                    if x >= 0:
                        all_streams[i].append(x)
        lz = tuple(pool.map(huffman_encode, all_streams))

        d2bs = [[len(huffman.data2bits[d]) for d in range(256)] for huffman in lz]
        bitstreams = []
        bitlens = []
        for stream, palette_pair in zip(streams, palettes):
            bitstream = []
            for t in stream:
                for huffman, x in zip(lz, t):
                    if x >= 0:
                        bitstream.extend(huffman.data2bits[x])
            for palette in palette_pair:
                for value in palette:
                    bitstream.extend(colors.data2bits[value])
            bitlens.append(len(bitstream))
            bitstreams.extend(bitstream)
        print("%.3fKB" % ((len(bitstreams) + 7) // 8 / 1000), file=stderr)
    return sizes, colors, bitstreams, bitlens, lz


def output_bits(bits):
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
    output_bits(list(form))
    print(",{")
    for x in perm:
        print("0x%02X," % x, end="")
    print("}}")


def output(sizes, colors, bitstream, bitlens, lz):
    print('#include "types.h"')
    print("static const Sprite sprite_data[] = {")
    for (w, h, d), bitlen in zip(sizes, bitlens):
        print("{%d,%d,%d,%d}," % (w, h, d, bitlen))
    print("};")
    print("static const uint8_t bitstream[] =")
    output_bits(bitstream)
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
    uncompressed_images = read_images()
    output(*compress_images(uncompressed_images, pool))


if __name__ == "__main__":
    main()
