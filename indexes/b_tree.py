import os
import struct

PAGE_SIZE = 4096
KEY_SIZE = 4
PTR_SIZE = 8
DATA_SIZE = 200

HEADER_FMT = "<Bhq"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

LEAF_ENTRY_SIZE = KEY_SIZE + DATA_SIZE
INTERNAL_ENTRY_SIZE = KEY_SIZE + PTR_SIZE

# máximo número de claves
LEAF_MAX = (PAGE_SIZE - HEADER_SIZE) // LEAF_ENTRY_SIZE
INTERNAL_MAX = (PAGE_SIZE - HEADER_SIZE - PTR_SIZE) // INTERNAL_ENTRY_SIZE


class Node:
    def __init__(self, page_id, is_leaf):
        self.page_id = page_id
        self.is_leaf = is_leaf
        self.keys = []
        self.children = []   # internos
        self.values = []     # hojas
        self.next_leaf = -1

    def serialize(self):
        header = struct.pack(HEADER_FMT, int(self.is_leaf), len(self.keys), self.next_leaf)
        body = bytearray()

        if self.is_leaf:
            for k, v in zip(self.keys, self.values):
                body += struct.pack("<i", k)
                v = v[:DATA_SIZE]
                body += v.ljust(DATA_SIZE, b"\x00")
        else:
            body += struct.pack("<q", self.children[0])
            for i in range(len(self.keys)):
                body += struct.pack("<i", self.keys[i])
                body += struct.pack("<q", self.children[i+1])

        return (header + body).ljust(PAGE_SIZE, b"\x00")

    @classmethod
    def from_bytes(cls, page_id, raw):
        is_leaf, n, next_leaf = struct.unpack_from(HEADER_FMT, raw, 0)
        node = cls(page_id, bool(is_leaf))
        node.next_leaf = next_leaf

        offset = HEADER_SIZE

        if node.is_leaf:
            for _ in range(n):
                k = struct.unpack_from("<i", raw, offset)[0]
                offset += 4
                v = raw[offset:offset+DATA_SIZE].rstrip(b"\x00")
                offset += DATA_SIZE
                node.keys.append(k)
                node.values.append(v)
        else:
            child = struct.unpack_from("<q", raw, offset)[0]
            offset += 8
            node.children.append(child)

            for _ in range(n):
                k = struct.unpack_from("<i", raw, offset)[0]
                offset += 4
                child = struct.unpack_from("<q", raw, offset)[0]
                offset += 8
                node.keys.append(k)
                node.children.append(child)

        return node

class BPlusTree:
    def __init__(self, path, buffer_manager=None):
        self._path = path + ".bpt"
        self._meta = path + ".meta"
        self._bm = buffer_manager

        if not os.path.exists(self._path):
            open(self._path, "wb").close()
            self._root = self._new_page(True)
            self._save_meta()
        else:
            self._load_meta()

    

    def _read(self, pid):
        if self._bm:
            raw = self._bm.read_page(self._path, pid)
        else:
            with open(self._path, "rb") as f:
                f.seek(pid * PAGE_SIZE)
                raw = f.read(PAGE_SIZE)
        return Node.from_bytes(pid, raw)

    def _write(self, node):
        raw = node.serialize()
        if self._bm:
            self._bm.write_page(self._path, node.page_id, raw)
        else:
            with open(self._path, "r+b") as f:
                f.seek(node.page_id * PAGE_SIZE)
                f.write(raw)

    def _new_page(self, is_leaf):
        header = struct.pack(HEADER_FMT, int(is_leaf), 0, -1)
        raw = header.ljust(PAGE_SIZE, b"\x00")

        if self._bm:
            return self._bm.append_page(self._path, raw)

        with open(self._path, "ab") as f:
            pid = f.tell() // PAGE_SIZE
            f.write(raw)
        return pid


    def _save_meta(self):
        with open(self._meta, "wb") as f:
            f.write(struct.pack("<q", self._root))

    def _load_meta(self):
        with open(self._meta, "rb") as f:
            self._root = struct.unpack("<q", f.read(8))[0]



    def _find_leaf(self, key):
        node = self._read(self._root)

        while not node.is_leaf:
            i = 0
            while i < len(node.keys) and key >= node.keys[i]:
                i += 1
            node = self._read(node.children[i])

        return node

    def search(self, key):
        leaf = self._find_leaf(key)
        for i, k in enumerate(leaf.keys):
            if k == key:
                return leaf.values[i]
        return None


    def add(self, key, value):
        res = self._insert(self._root, key, value)

        if res:
            k, new_pid = res
            new_root = self._new_page(False)
            root = Node(new_root, False)

            root.keys = [k]
            root.children = [self._root, new_pid]

            self._write(root)
            self._root = new_root
            self._save_meta()

    def _insert(self, pid, key, value):
        node = self._read(pid)

        if node.is_leaf:
            self._insert_leaf(node, key, value)

            if len(node.keys) > LEAF_MAX:
                return self._split_leaf(node)

            self._write(node)
            return None

        i = 0
        while i < len(node.keys) and key >= node.keys[i]:
            i += 1

        res = self._insert(node.children[i], key, value)

        if not res:
            return None

        k, new_pid = res
        self._insert_internal(node, k, new_pid)

        if len(node.keys) > INTERNAL_MAX:
            return self._split_internal(node)

        self._write(node)
        return None

    def _insert_leaf(self, node, key, value):
        i = 0
        while i < len(node.keys) and key > node.keys[i]:
            i += 1
        node.keys.insert(i, key)
        node.values.insert(i, value)

    def _insert_internal(self, node, key, child):
        i = 0
        while i < len(node.keys) and key > node.keys[i]:
            i += 1
        node.keys.insert(i, key)
        node.children.insert(i+1, child)


    def _split_leaf(self, node):
        mid = len(node.keys)//2

        new_pid = self._new_page(True)
        new = Node(new_pid, True)

        new.keys = node.keys[mid:]
        new.values = node.values[mid:]

        node.keys = node.keys[:mid]
        node.values = node.values[:mid]

        new.next_leaf = node.next_leaf
        node.next_leaf = new_pid

        self._write(node)
        self._write(new)

        return new.keys[0], new_pid

    def _split_internal(self, node):
        mid = len(node.keys)//2
        up_key = node.keys[mid]

        new_pid = self._new_page(False)
        new = Node(new_pid, False)

        new.keys = node.keys[mid+1:]
        new.children = node.children[mid+1:]

        node.keys = node.keys[:mid]
        node.children = node.children[:mid+1]

        self._write(node)
        self._write(new)

        return up_key, new_pid


    def range_search(self, lo, hi):
        res = []
        node = self._find_leaf(lo)

        while node:
            for k, v in zip(node.keys, node.values):
                if k > hi:
                    return res
                if lo <= k <= hi:
                    res.append(v)

            if node.next_leaf == -1:
                break
            node = self._read(node.next_leaf)

        return res

    def scan_all(self):
        node = self._read(self._root)

        while not node.is_leaf:
            node = self._read(node.children[0])

        res = []
        while node:
            res.extend(node.values)
            if node.next_leaf == -1:
                break
            node = self._read(node.next_leaf)
        return res

