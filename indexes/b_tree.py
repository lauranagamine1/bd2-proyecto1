import os
import struct

PAGE_SIZE = 4096
PTR_SIZE = 8
DATA_SIZE = 200

#B → is_leaf (0 o 1)
#h → número de claves
#q → next_leaf (solo hojas)

HEADER_FMT = "<Bhq"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# Tipos de clave soportados: 'int' -> entero 8 bytes, 'float' -> doble 8 bytes, 'str' -> UTF-8 64 bytes
_KEY_FORMATS: dict[str, tuple] = {
    'int':   ('<q', 8),
    'float': ('<d', 8),
    'str':   (None, 64),
}


class Node:
    def __init__(self, page_id, is_leaf):
        self.page_id = page_id
        self.is_leaf = is_leaf
        self.keys = []
        self.children = []   # nodos internos
        self.values = []     # hojas
        self.next_leaf = -1

    def serialize(self, key_fmt: str | None, key_size: int) -> bytes:
        header = struct.pack(HEADER_FMT, int(self.is_leaf), len(self.keys), self.next_leaf)
        body = bytearray()

        def _pack_key(k):
            if key_fmt is None:  # str
                # Codifica y trunca en un límite de carácter seguro para
                # nunca almacenar una secuencia UTF-8 multibyte incompleta.
                encoded = str(k).encode('utf-8')
                if len(encoded) > key_size:
                    encoded = str(k).encode('utf-8')[:key_size].decode('utf-8', errors='ignore').encode('utf-8')
                return encoded.ljust(key_size, b'\x00')[:key_size]
            return struct.pack(key_fmt, k)

        if self.is_leaf:
            for k, v in zip(self.keys, self.values):
                body += _pack_key(k)
                v = v[:DATA_SIZE]
                body += v.ljust(DATA_SIZE, b'\x00')
        else:
            body += struct.pack('<q', self.children[0])
            for i in range(len(self.keys)):
                body += _pack_key(self.keys[i])
                body += struct.pack('<q', self.children[i + 1])

        return (header + body).ljust(PAGE_SIZE, b'\x00')

    @classmethod
    def from_bytes(cls, page_id, raw, key_fmt: str | None, key_size: int):
        is_leaf, n, next_leaf = struct.unpack_from(HEADER_FMT, raw, 0)
        node = cls(page_id, bool(is_leaf))
        node.next_leaf = next_leaf

        offset = HEADER_SIZE

        def _unpack_key(buf, off):
            if key_fmt is None:  # cadena
                k = buf[off:off + key_size].rstrip(b'\x00').decode('utf-8', errors='ignore')
            else:
                k = struct.unpack_from(key_fmt, buf, off)[0]
            return k, off + key_size

        if node.is_leaf:
            for _ in range(n):
                k, offset = _unpack_key(raw, offset)
                v = raw[offset:offset + DATA_SIZE].rstrip(b'\x00')
                offset += DATA_SIZE
                node.keys.append(k)
                node.values.append(v)
        else:
            child = struct.unpack_from('<q', raw, offset)[0]
            offset += 8
            node.children.append(child)

            for _ in range(n):
                k, offset = _unpack_key(raw, offset)
                child = struct.unpack_from('<q', raw, offset)[0]
                offset += 8
                node.keys.append(k)
                node.children.append(child)

        return node

class BPlusTree:
    def __init__(self, path, buffer_manager=None, key_type: str = 'int'):
        self._path = path + '.bpt'
        self._meta = path + '.meta'
        self._bm = buffer_manager

        if not os.path.exists(self._path):
            self._key_type = key_type
            self._setup_key_format()
            open(self._path, 'wb').close()
            self._root = self._new_page(True)
            self._save_meta()
        else:
            self._load_meta()
            self._setup_key_format()

    def _setup_key_format(self):
        fmt, size = _KEY_FORMATS.get(self._key_type, ('<q', 8))
        self._key_fmt = fmt
        self._key_size = size
        leaf_entry = self._key_size + DATA_SIZE
        internal_entry = self._key_size + PTR_SIZE
        self._leaf_max = (PAGE_SIZE - HEADER_SIZE) // leaf_entry
        self._internal_max = (PAGE_SIZE - HEADER_SIZE - PTR_SIZE) // internal_entry
        self._leaf_min = self._leaf_max // 2
        self._internal_min = self._internal_max // 2



    def _read(self, pid):
        if self._bm:
            raw = self._bm.read_page(self._path, pid)
        else:
            with open(self._path, 'rb') as f:
                f.seek(pid * PAGE_SIZE) # Mover a la pagina pid en el disco
                raw = f.read(PAGE_SIZE)
        return Node.from_bytes(pid, raw, self._key_fmt, self._key_size)

    def _write(self, node):
        raw = node.serialize(self._key_fmt, self._key_size)
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
        with open(self._meta, 'wb') as f:
            f.write(struct.pack('<q', self._root))
            kt = self._key_type.encode('utf-8').ljust(8, b'\x00')
            f.write(kt)

    def _load_meta(self):
        with open(self._meta, 'rb') as f:
            self._root = struct.unpack('<q', f.read(8))[0]
            kt_bytes = f.read(8)
            if kt_bytes:
                self._key_type = kt_bytes.rstrip(b'\x00').decode('utf-8')
            else:
                self._key_type = 'int'  # compatibilidad con versiones anteriores



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

            if len(node.keys) > self._leaf_max:
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

        if len(node.keys) > self._internal_max:
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

    # REMOVE

    def remove(self, key):
        """Elimina la clave del árbol. Retorna True si fue encontrada y eliminada."""
        found = self._remove_from(self._root, key)

        # Si la raíz quedó como nodo interno sin claves, colapsarla
        root = self._read(self._root)
        if not root.is_leaf and len(root.keys) == 0:
            self._root = root.children[0]
            self._save_meta()

        return found

    def _remove_from(self, pid, key):
        node = self._read(pid)

        if node.is_leaf:
            try:
                i = node.keys.index(key)
            except ValueError:
                return False
            node.keys.pop(i)
            node.values.pop(i)
            self._write(node)
            return True

        # Determinar en qué hijo descender
        i = 0
        while i < len(node.keys) and key >= node.keys[i]:
            i += 1

        child_pid = node.children[i]
        found = self._remove_from(child_pid, key)

        if not found:
            return False

        # Releer el hijo para verificar underflow
        child = self._read(child_pid)
        min_keys = self._leaf_min if child.is_leaf else self._internal_min

        if len(child.keys) >= min_keys:
            return True

        # Underflow: intentar redistribuir desde hermano izquierdo
        if i > 0:
            left_pid = node.children[i - 1]
            left = self._read(left_pid)
            left_min = self._leaf_min if left.is_leaf else self._internal_min

            if len(left.keys) > left_min:
                if child.is_leaf:
                    # Rotación derecha: mover la última entrada del izquierdo al frente del hijo
                    child.keys.insert(0, left.keys.pop())
                    child.values.insert(0, left.values.pop())
                    node.keys[i - 1] = child.keys[0]
                else:
                    # Bajar separador del padre, subir la última clave del izquierdo
                    child.keys.insert(0, node.keys[i - 1])
                    child.children.insert(0, left.children.pop())
                    node.keys[i - 1] = left.keys.pop()
                self._write(left)
                self._write(child)
                self._write(node)
                return True

        # Underflow: intentar redistribuir desde hermano derecho
        if i < len(node.children) - 1:
            right_pid = node.children[i + 1]
            right = self._read(right_pid)
            right_min = self._leaf_min if right.is_leaf else self._internal_min

            if len(right.keys) > right_min:
                if child.is_leaf:
                    # Rotación izquierda: mover la primera entrada del derecho al final del hijo
                    child.keys.append(right.keys.pop(0))
                    child.values.append(right.values.pop(0))
                    node.keys[i] = right.keys[0]
                else:
                    # Bajar separador del padre, subir la primera clave del derecho
                    child.keys.append(node.keys[i])
                    child.children.append(right.children.pop(0))
                    node.keys[i] = right.keys.pop(0)
                self._write(right)
                self._write(child)
                self._write(node)
                return True

        # --- Se debe fusionar ---
        if i > 0:
            # Fusionar hijo en el hermano izquierdo; bajar separador del padre para nodos internos
            left_pid = node.children[i - 1]
            left = self._read(left_pid)

            if child.is_leaf:
                left.keys.extend(child.keys)
                left.values.extend(child.values)
                left.next_leaf = child.next_leaf
            else:
                left.keys.append(node.keys[i - 1])
                left.keys.extend(child.keys)
                left.children.extend(child.children)

            node.keys.pop(i - 1)
            node.children.pop(i)

            self._write(left)
            self._write(node)
        else:
            # Fusionar hermano derecho en el hijo; bajar separador del padre para nodos internos
            right_pid = node.children[i + 1]
            right = self._read(right_pid)

            if child.is_leaf:
                child.keys.extend(right.keys)
                child.values.extend(right.values)
                child.next_leaf = right.next_leaf
            else:
                child.keys.append(node.keys[i])
                child.keys.extend(right.keys)
                child.children.extend(right.children)

            node.keys.pop(i)
            node.children.pop(i + 1)

            self._write(child)
            self._write(node)

        return True

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

