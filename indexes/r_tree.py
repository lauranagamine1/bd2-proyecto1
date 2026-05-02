import struct
import os
import math
import heapq

PAGE_SIZE = 4096
DATA_SIZE = 160

# layout de entrada: Rect (4×f64) + payload (child_page i64 o data bytes)
MBR_FMT    = "4d"
MBR_SIZE   = struct.calcsize(MBR_FMT)
CHILD_SIZE = struct.calcsize("q")
ENTRY_SIZE = MBR_SIZE + max(CHILD_SIZE, DATA_SIZE)

# cabecera de nodo: is_leaf (u8) + n_entries (i16)
HEADER_FMT  = "<Bh"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

M = (PAGE_SIZE - HEADER_SIZE) // ENTRY_SIZE
m = max(1, M // 2)

class Rect:
    def __init__(self, x_min: float, y_min: float, x_max: float, y_max: float):
        self.x_min = x_min
        self.y_min = y_min
        self.x_max = x_max
        self.y_max = y_max

    @staticmethod
    def from_point(x: float, y: float) -> 'Rect':
        return Rect(x, y, x, y)

    def area(self) -> float:
        return (self.x_max - self.x_min) * (self.y_max - self.y_min)

    def enlargement(self, other: 'Rect') -> float:
        return self.merge(other).area() - self.area()

    def merge(self, other: 'Rect') -> 'Rect':
        return Rect(
            min(self.x_min, other.x_min),
            min(self.y_min, other.y_min),
            max(self.x_max, other.x_max),
            max(self.y_max, other.y_max),
        )

    def intersects(self, other: 'Rect') -> bool:
        return not (
            self.x_max < other.x_min or
            self.x_min > other.x_max or
            self.y_max < other.y_min or
            self.y_min > other.y_max
        )

    def contains(self, other: 'Rect') -> bool:
        return (
            self.x_min <= other.x_min and
            self.y_min <= other.y_min and
            self.x_max >= other.x_max and
            self.y_max >= other.y_max
        )

    def intersects_circle(self, cx: float, cy: float, r: float) -> bool:
        nx = max(self.x_min, min(cx, self.x_max))
        ny = max(self.y_min, min(cy, self.y_max))
        return math.hypot(cx - nx, cy - ny) <= r
        
    def min_dist(self, cx: float, cy: float) -> float:
        nx = max(self.x_min, min(cx, self.x_max))
        ny = max(self.y_min, min(cy, self.y_max))
        return math.hypot(cx - nx, cy - ny)

class Entry:
    def __init__(self, rect: Rect):
        self.rect = rect
        
class Leaf(Entry):
    def __init__(self, rect: Rect, data: bytes):
        super().__init__(rect)
        self.data = data
        
class Branch(Entry):
    def __init__(self, rect: Rect, child_page: int):
        super().__init__(rect)
        self.child_page = child_page

class Node:
    def __init__(self, page_id: int, is_leaf: bool):
        self.page_id = page_id
        self.is_leaf = is_leaf
        self.entries = [] # lista de Leaf o Branch

    def compute_mbr(self) -> Rect:
        if not self.entries:
            return Rect(0,0,0,0)
        rect = self.entries[0].rect
        for e in self.entries[1:]:
            rect = rect.merge(e.rect)
        return rect

    def serialize(self) -> bytes:
        header = struct.pack(HEADER_FMT, int(self.is_leaf), len(self.entries))
        body = bytearray()
        for e in self.entries:
            raw_rect = struct.pack("<" + MBR_FMT, e.rect.x_min, e.rect.y_min, e.rect.x_max, e.rect.y_max)
            if self.is_leaf:
                payload = e.data[:DATA_SIZE].ljust(ENTRY_SIZE - MBR_SIZE, b"\x00")
            else:
                payload = struct.pack("<q", e.child_page).ljust(ENTRY_SIZE - MBR_SIZE, b"\x00")
            body += raw_rect + payload
        return (header + bytes(body)).ljust(PAGE_SIZE, b"\x00")

    @classmethod
    def from_bytes(cls, page_id: int, raw: bytes) -> 'Node':
        is_leaf, n = struct.unpack_from(HEADER_FMT, raw, 0)
        node = cls(page_id, bool(is_leaf))
        for i in range(n):
            off = HEADER_SIZE + i * ENTRY_SIZE
            coords = struct.unpack_from("<" + MBR_FMT, raw, off)
            rect = Rect(*coords)
            payload_off = off + MBR_SIZE
            if node.is_leaf:
                data = raw[payload_off:payload_off + DATA_SIZE].rstrip(b"\x00")
                node.entries.append(Leaf(rect, data))
            else:
                child_page = struct.unpack_from("<q", raw, payload_off)[0]
                node.entries.append(Branch(rect, child_page))
        return node


from contextlib import contextmanager

class RTree:
    def __init__(self, filepath: str, buffer_manager=None):
        self._path = filepath + ".rtree"
        self._meta = filepath + ".rtree.meta"
        self._bm   = buffer_manager
        
        # Meta lock para cuando se cambia el _root_page
        self._meta_lock = __import__("threading").Lock()

        if not os.path.exists(self._path):
            open(self._path, "wb").close()
            self._root_page = self._new_page(is_leaf=True)
            self._save_meta()
        else:
            self._load_meta()

    @contextmanager
    def _lock_and_pin(self, page_id: int):
        if self._bm:
            self._bm.lock_page(self._path, page_id)
            try:
                yield
            finally:
                self._bm.unlock_page(self._path, page_id)
                self._bm.unpin_page(self._path, page_id)
        else:
            yield

    def _save_meta(self):
        with open(self._meta, "wb") as f:
            f.write(struct.pack("<qq", self._root_page, self._page_count()))

    def _load_meta(self):
        with open(self._meta, "rb") as f:
            self._root_page, _ = struct.unpack("<qq", f.read(16))

    def _page_count(self) -> int:
        return os.path.getsize(self._path) // PAGE_SIZE

    def _read_node(self, page_id: int) -> Node:
        if self._bm:
            raw = self._bm.read_page(self._path, page_id)
        else:
            with open(self._path, "rb") as f:
                f.seek(page_id * PAGE_SIZE)
                raw = f.read(PAGE_SIZE)
        return Node.from_bytes(page_id, raw.ljust(PAGE_SIZE, b"\x00"))

    def _write_node(self, node: Node):
        raw = node.serialize()
        if self._bm:
            self._bm.write_page(self._path, node.page_id, raw)
        else:
            with open(self._path, "r+b") as f:
                f.seek(node.page_id * PAGE_SIZE)
                f.write(raw)

    def _new_page(self, is_leaf: bool) -> int:
        header = struct.pack(HEADER_FMT, int(is_leaf), 0)
        raw = header.ljust(PAGE_SIZE, b"\x00")
        if self._bm:
            page_id = self._bm.append_page(self._path, raw)
        else:
            with open(self._path, "ab") as f:
                page_id = f.tell() // PAGE_SIZE
                f.write(raw)
        return page_id

    def add(self, x: float, y: float, data: bytes):
        rect = Rect.from_point(x, y)
        leaf = Leaf(rect, data)
        
        with self._meta_lock:
            result = self._insert(self._root_page, leaf)
            
            if result:
                n1, n2 = result
                new_root_page = self._new_page(is_leaf=False)
                new_root = Node(new_root_page, is_leaf=False)
                new_root.entries = [
                    Branch(n1.compute_mbr(), n1.page_id),
                    Branch(n2.compute_mbr(), n2.page_id)
                ]
                self._write_node(new_root)
                self._root_page = new_root_page
                self._save_meta()

    def _insert(self, page_id: int, entry: Entry):
        with self._lock_and_pin(page_id):
            node = self._read_node(page_id)
            
            if node.is_leaf:
                node.entries.append(entry)
                if len(node.entries) > M:
                    return self._split(node)
                self._write_node(node)
                return None
                
            best_branch = self._choose_subtree(node, entry.rect)
            result = self._insert(best_branch.child_page, entry)
            
            if result:
                n1, n2 = result
                node.entries.remove(best_branch)
                node.entries.append(Branch(n1.compute_mbr(), n1.page_id))
                node.entries.append(Branch(n2.compute_mbr(), n2.page_id))
                
                if len(node.entries) > M:
                    return self._split(node)
            else:
                child_node = self._read_node(best_branch.child_page)
                best_branch.rect = child_node.compute_mbr()
                if self._bm: self._bm.unpin_page(self._path, best_branch.child_page)
                
            self._write_node(node)
            return None

    def _choose_subtree(self, node: Node, rect: Rect) -> Branch:
        best = None
        min_enl = float("inf")
        min_area = float("inf")
        
        for entry in node.entries:
            enl = entry.rect.enlargement(rect)
            area = entry.rect.area()
            if enl < min_enl or (enl == min_enl and area < min_area):
                min_enl = enl
                min_area = area
                best = entry
                
        return best

    def _split(self, node: Node):
        entries = node.entries[:]
        i1, i2 = self._pick_seeds(entries)
        
        e1 = entries.pop(max(i1, i2))
        e2 = entries.pop(min(i1, i2))
        
        group1 = [e1]
        group2 = [e2]
        
        rect1 = e1.rect
        rect2 = e2.rect
        
        while entries:
            if len(group1) + len(entries) == m:
                group1.extend(entries)
                break
            if len(group2) + len(entries) == m:
                group2.extend(entries)
                break
                
            e = self._pick_next(entries, rect1, rect2)
            entries.remove(e)
            
            d1 = rect1.enlargement(e.rect)
            d2 = rect2.enlargement(e.rect)
            
            if d1 < d2 or (d1 == d2 and len(group1) <= len(group2)):
                group1.append(e)
                rect1 = rect1.merge(e.rect)
            else:
                group2.append(e)
                rect2 = rect2.merge(e.rect)
                
        node.entries = group1
        self._write_node(node)
        
        new_page_id = self._new_page(is_leaf=node.is_leaf)
        n2 = Node(new_page_id, node.is_leaf)
        n2.entries = group2
        self._write_node(n2)
        
        return node, n2

    def _pick_seeds(self, entries: list) -> tuple[int, int]:
        max_d = -float("inf")
        pair = (0, 1)
        
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                r1 = entries[i].rect
                r2 = entries[j].rect
                merged = r1.merge(r2)
                d = merged.area() - r1.area() - r2.area()
                if d > max_d:
                    max_d = d
                    pair = (i, j)
        return pair
        
    def _pick_next(self, entries: list, r1: Rect, r2: Rect):
        best_e = None
        best_diff = -float("inf")
        for e in entries:
            diff = abs(r1.enlargement(e.rect) - r2.enlargement(e.rect))
            if diff > best_diff:
                best_diff = diff
                best_e = e
        return best_e

    def remove(self, x: float, y: float, data: bytes = None) -> bool:
        rect = Rect.from_point(x, y)
        eliminated = []
        with self._meta_lock:
            if self._remove_recursive(self._root_page, rect, data, eliminated):
                for e, is_leaf in eliminated:
                    self._reinsert_subtree(e, is_leaf)
                
                root_node = self._read_node(self._root_page)
                if not root_node.is_leaf and len(root_node.entries) == 1:
                    self._root_page = root_node.entries[0].child_page
                    self._save_meta()
                if self._bm: self._bm.unpin_page(self._path, self._root_page)
                return True
        return False
        
    def _remove_recursive(self, page_id: int, rect: Rect, data: bytes, eliminated: list) -> bool:
        with self._lock_and_pin(page_id):
            node = self._read_node(page_id)
            
            if node.is_leaf:
                for i, e in enumerate(node.entries):
                    if abs(e.rect.x_min - rect.x_min) < 1e-9 and abs(e.rect.y_min - rect.y_min) < 1e-9:
                        if data is None or e.data == data:
                            node.entries.pop(i)
                            self._write_node(node)
                            return True
                return False
                
            else:
                removed_something = False
                for i, e in enumerate(node.entries):
                    if e.rect.intersects(rect):
                        if self._remove_recursive(e.child_page, rect, data, eliminated):
                            removed_something = True
                            
                            child_node = self._read_node(e.child_page)
                            if len(child_node.entries) < m:
                                eliminated.extend([(ce, child_node.is_leaf) for ce in child_node.entries])
                                node.entries.pop(i)
                            else:
                                e.rect = child_node.compute_mbr()
                                
                            if self._bm: self._bm.unpin_page(self._path, e.child_page)
                            self._write_node(node)
                            return True
                            
                return removed_something

    def _reinsert_subtree(self, e: Entry, is_leaf: bool):
        if is_leaf:
            self.add(e.rect.x_min, e.rect.y_min, e.data)
        else:
            self._extract_and_reinsert(e.child_page)
            
    def _extract_and_reinsert(self, page_id: int):
        node = self._read_node(page_id)
        for e in node.entries:
            if node.is_leaf:
                self.add(e.rect.x_min, e.rect.y_min, e.data)
            else:
                self._extract_and_reinsert(e.child_page)

    def range_search(self, cx: float, cy: float, radius: float) -> list[bytes]:
        results = []
        self._range_search(self._root_page, cx, cy, radius, results)
        return results

    def _range_search(self, page_id: int, cx: float, cy: float, r: float, results: list):
        with self._lock_and_pin(page_id):
            node = self._read_node(page_id)
            for e in node.entries:
                if not e.rect.intersects_circle(cx, cy, r):
                    continue
                if node.is_leaf:
                    if math.hypot(cx - e.rect.x_min, cy - e.rect.y_min) <= r:
                        results.append(e.data)
                else:
                    self._range_search(e.child_page, cx, cy, r, results)

    def knn(self, cx: float, cy: float, k: int) -> list[tuple[float, bytes]]:
        with self._meta_lock:
            heap = [(0.0, 0, self._root_page, False)]
            next_seq = 1
        results = []

        while heap and len(results) < k:
            dist, _, item, is_result = heapq.heappop(heap)
            if is_result:
                results.append((dist, item))
                continue
            
            with self._lock_and_pin(item):
                node = self._read_node(item)
                for e in node.entries:
                    if node.is_leaf:
                        d = math.hypot(cx - e.rect.x_min, cy - e.rect.y_min)
                        heapq.heappush(heap, (d, next_seq, e.data, True))
                    else:
                        d = e.rect.min_dist(cx, cy)
                        heapq.heappush(heap, (d, next_seq, e.child_page, False))
                    next_seq += 1

        return results
