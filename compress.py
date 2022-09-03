#!/usr/bin/env python3

import collections
import heapq
import itertools
import json

sprites = json.loads(open("data.json").read())


def rle(data):
    counts = []
    runs = []
    for x, g in itertools.groupby(data):
        counts.append(len(list(g)))
        runs.append(x)
    return (counts, runs)


def he(data):
    heap = [
        (count, i, val)
        for i, (val, count) in enumerate(collections.Counter(data).items())
    ]
    heapq.heapify(heap)
    while len(heap) > 1:
        c1, _, v1 = heapq.heappop(heap)
        c2, _, v2 = heapq.heappop(heap)
        heapq.heappush(heap, (c1 + c2, -len(heap), (v1, v2)))

    encode = {}
    acc = []

    def dfs(node):
        if type(node) == int:
            encode[node] = acc[::]
            return
        l, r = node
        acc.append(0)
        dfs(l)
        acc.pop()
        acc.append(1)
        dfs(r)
        acc.pop()

    dfs(heap[0][2])
    return sum((encode[x] for x in data), start=[])


print(len(sprites[0]))
counts, runs = rle(sprites[0])
print(len(he(sprites[0])))
print(len(he(counts)))
print(len(he(runs)))
