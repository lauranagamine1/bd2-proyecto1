import hashlib
import os
import shutil
import struct
import tempfile

from file_manager import FileManager, PAGE_SIZE


PAGE_HEADER_FORMAT = "H"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)
ENTRY_HEADER_FORMAT = "IH"
ENTRY_HEADER_SIZE = struct.calcsize(ENTRY_HEADER_FORMAT)
TYPE_NONE = b"N"
TYPE_BOOL = b"B"
TYPE_INT = b"I"
TYPE_FLOAT = b"F"
TYPE_STR = b"S"
TYPE_DICT = b"D"


class ExternalHashing:
    """
    External hashing basado en particiones/buckets en disco.

    Las filas se escriben en un archivo binario compartido por todos los buckets.
    Los buckets son lógicos: el directorio en memoria recuerda qué páginas tienen
    entradas de cada bucket. Así, una página puede contener entradas de varios
    buckets y no se reserva una página completa para cada bucket vacío o pequeño.
    """

    def __init__(
        self,
        bucket_count: int = 16,
        base_dir: str | None = None,
        buffer_manager=None,
        cleanup_on_close: bool | None = None,
    ):
        self.bucket_count = max(1, int(bucket_count))
        self._owns_dir = base_dir is None if cleanup_on_close is None else cleanup_on_close
        self.base_dir = base_dir or tempfile.mkdtemp(prefix="external_hash_")
        self.bm = buffer_manager
        self.fm = buffer_manager._fm if buffer_manager else FileManager()
        self.data_path = os.path.join(self.base_dir, "external_hashing.dat")
        self._bucket_pages: list[list[int]] = [[] for _ in range(self.bucket_count)]
        self._current_page_id = -1
        self._page_count = 0
        os.makedirs(self.base_dir, exist_ok=True)
        self.fm.ensure_file(self.data_path)

    def build(self, rows, key_name: str):
        self.clear_buckets()
        for row in rows:
            self.insert(row[key_name], row)

    def insert(self, key, row: dict):
        bucket_id = self._bucket_id(key)

        payload = self._pack_payload(key, row)
        entry = struct.pack(ENTRY_HEADER_FORMAT, bucket_id, len(payload)) + payload
        if len(entry) > PAGE_SIZE - PAGE_HEADER_SIZE:
            raise ValueError("ExternalHashing entry is larger than one page")

        page_id, page = self._writable_page(len(entry))
        used = self._page_used(page)

        page[used:used + len(entry)] = entry
        self._set_page_used(page, used + len(entry))
        self._write_page(self.data_path, page_id, bytes(page))

        if page_id not in self._bucket_pages[bucket_id]:
            self._bucket_pages[bucket_id].append(page_id)

    def search(self, key):
        bucket_id = self._bucket_id(key)

        matches = []
        for page_id in self._bucket_pages[bucket_id]:
            page = self._read_page(self.data_path, page_id)
            used = self._page_used(page)
            pos = PAGE_HEADER_SIZE

            while pos + ENTRY_HEADER_SIZE <= used:
                stored_bucket, size = struct.unpack(
                    ENTRY_HEADER_FORMAT,
                    page[pos:pos + ENTRY_HEADER_SIZE],
                )
                pos += ENTRY_HEADER_SIZE
                payload = page[pos:pos + size]
                pos += size

                if stored_bucket != bucket_id:
                    continue
                stored_key, row = self._unpack_payload(payload)
                if stored_key == key:
                    matches.append(row)
        return matches

    def clear_buckets(self):
        if self.bm:
            self.bm.invalidate(self.data_path)
        if os.path.exists(self.data_path):
            os.remove(self.data_path)
        self.fm.ensure_file(self.data_path)
        self._bucket_pages = [[] for _ in range(self.bucket_count)]
        self._current_page_id = -1
        self._page_count = 0

    def close(self):
        if self.bm:
            self.bm.flush_all()
        if self._owns_dir and os.path.exists(self.base_dir):
            shutil.rmtree(self.base_dir)

    def _bucket_id(self, key):
        raw_key = self._pack_value(key)
        digest = hashlib.sha256(raw_key).digest()
        return int.from_bytes(digest[:8], "big") % self.bucket_count

    def _read_page(self, path: str, page_id: int) -> bytes:
        if self.bm:
            return self.bm.read_page(path, page_id)
        return self.fm.read_page(path, page_id)

    def _write_page(self, path: str, page_id: int, data: bytes):
        if self.bm:
            self.bm.write_page(path, page_id, data)
        else:
            self.fm.write_page(path, page_id, data)

    @staticmethod
    def _empty_page() -> bytes:
        page = bytearray(PAGE_SIZE)
        page[:PAGE_HEADER_SIZE] = struct.pack(PAGE_HEADER_FORMAT, PAGE_HEADER_SIZE)
        return bytes(page)

    def _writable_page(self, entry_size: int) -> tuple[int, bytearray]:
        if self._current_page_id == -1:
            self._current_page_id = self._page_count
            self._page_count += 1
            return self._current_page_id, bytearray(self._empty_page())

        page = bytearray(self._read_page(self.data_path, self._current_page_id))
        used = self._page_used(page)
        if used + entry_size <= PAGE_SIZE:
            return self._current_page_id, page

        self._current_page_id = self._page_count
        self._page_count += 1
        return self._current_page_id, bytearray(self._empty_page())

    @staticmethod
    def _page_used(page: bytes | bytearray) -> int:
        used = struct.unpack(PAGE_HEADER_FORMAT, page[:PAGE_HEADER_SIZE])[0]
        return used or PAGE_HEADER_SIZE

    @staticmethod
    def _set_page_used(page: bytearray, used: int):
        page[:PAGE_HEADER_SIZE] = struct.pack(PAGE_HEADER_FORMAT, used)

    @classmethod
    def _pack_payload(cls, key, row: dict) -> bytes:
        return cls._pack_value(key) + cls._pack_value(row)

    @classmethod
    def _unpack_payload(cls, payload: bytes):
        key, pos = cls._unpack_value(payload, 0)
        row, pos = cls._unpack_value(payload, pos)
        if pos != len(payload):
            raise ValueError("ExternalHashing payload has trailing bytes")
        return key, row

    @classmethod
    def _pack_value(cls, value) -> bytes:
        if value is None:
            return TYPE_NONE
        if isinstance(value, bool):
            return TYPE_BOOL + struct.pack("?", value)
        if isinstance(value, int) and not isinstance(value, bool):
            return TYPE_INT + struct.pack("q", value)
        if isinstance(value, float):
            return TYPE_FLOAT + struct.pack("d", value)
        if isinstance(value, str):
            raw = value.encode("utf-8")
            return TYPE_STR + struct.pack("I", len(raw)) + raw
        if isinstance(value, dict):
            items = list(value.items())
            data = bytearray(TYPE_DICT + struct.pack("H", len(items)))
            for key, item_value in items:
                if not isinstance(key, str):
                    raise TypeError("ExternalHashing only supports string keys in row dictionaries")
                key_raw = key.encode("utf-8")
                data.extend(struct.pack("H", len(key_raw)))
                data.extend(key_raw)
                data.extend(cls._pack_value(item_value))
            return bytes(data)
        raise TypeError(f"ExternalHashing cannot pack value of type {type(value).__name__}")

    @classmethod
    def _unpack_value(cls, raw: bytes, pos: int):
        tag = raw[pos:pos + 1]
        pos += 1

        if tag == TYPE_NONE:
            return None, pos
        if tag == TYPE_BOOL:
            return struct.unpack("?", raw[pos:pos + 1])[0], pos + 1
        if tag == TYPE_INT:
            return struct.unpack("q", raw[pos:pos + 8])[0], pos + 8
        if tag == TYPE_FLOAT:
            return struct.unpack("d", raw[pos:pos + 8])[0], pos + 8
        if tag == TYPE_STR:
            size = struct.unpack("I", raw[pos:pos + 4])[0]
            pos += 4
            return raw[pos:pos + size].decode("utf-8"), pos + size
        if tag == TYPE_DICT:
            count = struct.unpack("H", raw[pos:pos + 2])[0]
            pos += 2
            result = {}
            for _ in range(count):
                key_size = struct.unpack("H", raw[pos:pos + 2])[0]
                pos += 2
                key = raw[pos:pos + key_size].decode("utf-8")
                pos += key_size
                value, pos = cls._unpack_value(raw, pos)
                result[key] = value
            return result, pos

        raise ValueError(f"Unknown ExternalHashing type tag: {tag!r}")
