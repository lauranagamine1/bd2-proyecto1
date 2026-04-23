import struct
import os
import math
from typing import NamedTuple

PAGE_SIZE = 4096
DATA_SIZE = 160

# layout de entrada: MBR (4×f64) + payload (child_page i64 o data bytes)
MBR_FMT    = "4d"
MBR_SIZE   = struct.calcsize(MBR_FMT)
CHILD_SIZE = struct.calcsize("q")
ENTRY_SIZE = MBR_SIZE + max(CHILD_SIZE, DATA_SIZE)

# cabecera de nodo: is_leaf (u8) + n_entries (i16)
HEADER_FMT  = "<Bh"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

M = (PAGE_SIZE - HEADER_SIZE) // ENTRY_SIZE
m = max(1, M // 2)


class MBR(NamedTuple):
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @staticmethod
    def from_point(x: float, y: float) -> "MBR":
        return MBR(x, y, x, y)

    def area(self) -> float:
        return (self.x_max - self.x_min) * (self.y_max - self.y_min)

    def enlargement(self, other: "MBR") -> float:
        return self.merge(other).area() - self.area()

    def merge(self, other: "MBR") -> "MBR":
        return MBR(
            min(self.x_min, other.x_min),
            min(self.y_min, other.y_min),
            max(self.x_max, other.x_max),
            max(self.y_max, other.y_max),
        )

    def intersects_circle(self, cx: float, cy: float, r: float) -> bool:
        # punto más cercano del rectángulo al centro
        nx = max(self.x_min, min(cx, self.x_max))
        ny = max(self.y_min, min(cy, self.y_max))
        return math.hypot(cx - nx, cy - ny) <= r

    def intersects(self, other: "MBR") -> bool:
        return (self.x_min <= other.x_max and self.x_max >= other.x_min and
                self.y_min <= other.y_max and self.y_max >= other.y_min)


def _pack_entry(mbr: MBR, payload: bytes) -> bytes:
    raw = struct.pack("<" + MBR_FMT, *mbr)
    payload = payload[:ENTRY_SIZE - MBR_SIZE].ljust(ENTRY_SIZE - MBR_SIZE, b"\x00")
    return raw + payload


def _pack_child(mbr: MBR, child_page: int) -> bytes:
    raw = struct.pack("<" + MBR_FMT, *mbr)
    child = struct.pack("<q", child_page)
    padding = (ENTRY_SIZE - MBR_SIZE - CHILD_SIZE) * b"\x00"
    return raw + child + padding


def _unpack_mbr(raw: bytes, offset: int) -> MBR:
    return MBR(*struct.unpack_from("<" + MBR_FMT, raw, offset))


def _unpack_child(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<q", raw, offset + MBR_SIZE)[0]


def _unpack_data(raw: bytes, offset: int) -> bytes:
    start = offset + MBR_SIZE
    return raw[start:start + DATA_SIZE].rstrip(b"\x00")


class RTree:
    def __init__(self, filepath: str, buffer_manager=None):
        self._path = filepath + ".rtree"
        self._meta = filepath + ".rtree.meta"
        self._bm   = buffer_manager

        if not os.path.exists(self._path):
            open(self._path, "wb").close()
            self._root = self._new_page(is_leaf=True)
            self._save_meta()
        else:
            self._load_meta()

    def _save_meta(self):
        with open(self._meta, "wb") as f:
            f.write(struct.pack("<qq", self._root, self._page_count()))

    def _load_meta(self):
        with open(self._meta, "rb") as f:
            self._root, _ = struct.unpack("<qq", f.read(16))

    def _page_count(self) -> int:
        return os.path.getsize(self._path) // PAGE_SIZE

    def _read_page(self, page_id: int) -> bytes:
        if self._bm:
            return self._bm.read_page(self._path, page_id)
        with open(self._path, "rb") as f:
            f.seek(page_id * PAGE_SIZE)
            raw = f.read(PAGE_SIZE)
        return raw.ljust(PAGE_SIZE, b"\x00")

    def _write_page(self, page_id: int, raw: bytes):
        raw = raw[:PAGE_SIZE].ljust(PAGE_SIZE, b"\x00")
        if self._bm:
            self._bm.write_page(self._path, page_id, raw)
        else:
            with open(self._path, "r+b") as f:
                f.seek(page_id * PAGE_SIZE)
                f.write(raw)

    def _new_page(self, is_leaf: bool) -> int:
        header = struct.pack(HEADER_FMT, int(is_leaf), 0)
        raw = header.ljust(PAGE_SIZE, b"\x00")
        if self._bm:
            return self._bm.append_page(self._path, raw)
        with open(self._path, "ab") as f:
            page_id = f.tell() // PAGE_SIZE
            f.write(raw)
        return page_id

    def _parse_node(self, raw: bytes) -> tuple[bool, list]:
        is_leaf, n = struct.unpack_from(HEADER_FMT, raw, 0)
        is_leaf = bool(is_leaf)
        entries = []
        for i in range(n):
            off = HEADER_SIZE + i * ENTRY_SIZE
            mbr = _unpack_mbr(raw, off)
            if is_leaf:
                entries.append((mbr, _unpack_data(raw, off)))
            else:
                entries.append((mbr, _unpack_child(raw, off)))
        return is_leaf, entries

    def _serialize_node(self, is_leaf: bool, entries: list) -> bytes:
        header = struct.pack(HEADER_FMT, int(is_leaf), len(entries))
        body = bytearray()
        for mbr, payload in entries:
            if is_leaf:
                body += _pack_entry(mbr, payload if isinstance(payload, bytes) else b"")
            else:
                body += _pack_child(mbr, payload)
        return (header + bytes(body)).ljust(PAGE_SIZE, b"\x00")

    def _node_mbr(self, entries: list) -> MBR:
        mbr = entries[0][0]
        for e in entries[1:]:
            mbr = mbr.merge(e[0])
        return mbr

    # --- add ---

    def add(self, x: float, y: float, data: bytes):
        mbr = MBR.from_point(x, y)
        result = self._insert(self._root, mbr, data)
        if result is not None:
            # split en la raíz: sube un nivel creando una nueva raíz
            new_root = self._new_page(is_leaf=False)
            left_mbr, left_page, right_mbr, right_page = result
            raw = self._serialize_node(False, [(left_mbr, left_page), (right_mbr, right_page)])
            self._write_page(new_root, raw)
            self._root = new_root
            self._save_meta()

    def _insert(self, page_id: int, mbr: MBR, data: bytes):
        raw = self._read_page(page_id)
        is_leaf, entries = self._parse_node(raw)

        if is_leaf:
            entries.append((mbr, data))
            if len(entries) > M:
                return self._split(page_id, is_leaf, entries)
            self._write_page(page_id, self._serialize_node(True, entries))
            return None

        idx = self._choose_subtree(entries, mbr)
        child_page = entries[idx][1]
        result = self._insert(child_page, mbr, data)

        if result is not None:
            left_mbr, left_page, right_mbr, right_page = result
            entries[idx] = (left_mbr, left_page)
            entries.append((right_mbr, right_page))
            if len(entries) > M:
                return self._split(page_id, is_leaf, entries)
            entries[idx] = (self._recompute_mbr(left_page), left_page)
        else:
            entries[idx] = (self._recompute_mbr(child_page), child_page)

        self._write_page(page_id, self._serialize_node(False, entries))
        return None

    def _choose_subtree(self, entries: list, mbr: MBR) -> int:
        best_idx, best_enl, best_area = 0, float("inf"), float("inf")
        for i, (e_mbr, _) in enumerate(entries):
            enl  = e_mbr.enlargement(mbr)
            area = e_mbr.area()
            if enl < best_enl or (enl == best_enl and area < best_area):
                best_idx, best_enl, best_area = i, enl, area
        return best_idx

    def _recompute_mbr(self, page_id: int) -> MBR:
        _, entries = self._parse_node(self._read_page(page_id))
        return self._node_mbr(entries)

    # split cuadrático de Guttman
    def _split(self, page_id: int, is_leaf: bool, entries: list):
        s1, s2 = self._pick_seeds(entries)
        group1, group2 = [entries[s1]], [entries[s2]]
        remaining = [e for i, e in enumerate(entries) if i not in (s1, s2)]

        while remaining:
            if len(group1) + len(remaining) == m:
                group1.extend(remaining); break
            if len(group2) + len(remaining) == m:
                group2.extend(remaining); break
            e = self._pick_next(remaining, group1, group2)
            remaining.remove(e)
            d1 = self._node_mbr(group1).enlargement(e[0])
            d2 = self._node_mbr(group2).enlargement(e[0])
            if d1 < d2 or (d1 == d2 and len(group1) <= len(group2)):
                group1.append(e)
            else:
                group2.append(e)

        right_page = self._new_page(is_leaf)
        self._write_page(page_id,    self._serialize_node(is_leaf, group1))
        self._write_page(right_page, self._serialize_node(is_leaf, group2))
        return (self._node_mbr(group1), page_id,
                self._node_mbr(group2), right_page)

    def _pick_seeds(self, entries: list) -> tuple[int, int]:
        worst, s1, s2 = -float("inf"), 0, 1
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                merged = entries[i][0].merge(entries[j][0])
                waste  = merged.area() - entries[i][0].area() - entries[j][0].area()
                if waste > worst:
                    worst, s1, s2 = waste, i, j
        return s1, s2

    def _pick_next(self, remaining: list, g1: list, g2: list):
        mbr1 = self._node_mbr(g1)
        mbr2 = self._node_mbr(g2)
        best_e, best_diff = None, -float("inf")
        for e in remaining:
            diff = abs(mbr1.enlargement(e[0]) - mbr2.enlargement(e[0]))
            if diff > best_diff:
                best_diff, best_e = diff, e
        return best_e

    # --- range_search ---

    def range_search(self, cx: float, cy: float, radius: float) -> list[bytes]:
        results = []
        self._range_search(self._root, cx, cy, radius, results)
        return results

    def _range_search(self, page_id: int, cx, cy, r, results):
        raw = self._read_page(page_id)
        is_leaf, entries = self._parse_node(raw)
        for mbr, payload in entries:
            if not mbr.intersects_circle(cx, cy, r):
                continue
            if is_leaf:
                if math.hypot(cx - mbr.x_min, cy - mbr.y_min) <= r:
                    results.append(payload)
            else:
                self._range_search(payload, cx, cy, r, results)

    # --- knn ---

    def knn(self, cx: float, cy: float, k: int) -> list[tuple[float, bytes]]:
        import heapq
        # best-first: el heap mezcla nodos y puntos hoja bajo la misma distancia
        heap = [(0.0, self._root, False)]
        results = []

        while heap and len(results) < k:
            dist, item, is_result = heapq.heappop(heap)
            if is_result:
                results.append((dist, item))
                continue
            raw = self._read_page(item)
            is_leaf, entries = self._parse_node(raw)
            for mbr, payload in entries:
                if is_leaf:
                    d = math.hypot(cx - mbr.x_min, cy - mbr.y_min)
                    heapq.heappush(heap, (d, payload, True))
                else:
                    d = self._min_dist_mbr(cx, cy, mbr)
                    heapq.heappush(heap, (d, payload, False))

        return results

    def _min_dist_mbr(self, cx, cy, mbr: MBR) -> float:
        nx = max(mbr.x_min, min(cx, mbr.x_max))
        ny = max(mbr.y_min, min(cy, mbr.y_max))
        return math.hypot(cx - nx, cy - ny)

    def dump(self, page_id: int | None = None, depth: int = 0):
        if page_id is None:
            page_id = self._root
        raw = self._read_page(page_id)
        is_leaf, entries = self._parse_node(raw)
        kind = "LEAF" if is_leaf else "NODE"
        print(f"{'  ' * depth}[{kind} page={page_id}] {len(entries)} entradas")
        for mbr, payload in entries:
            if is_leaf:
                print(f"{'  ' * depth}  MBR={mbr} data={payload!r}")
            else:
                print(f"{'  ' * depth}  MBR={mbr} -> page {payload}")
                self.dump(payload, depth + 1)
