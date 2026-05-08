import struct
import os

K_MAX_AUX = 10
PAGE_SIZE  = 4096

DATA_SIZE = 200

# layout: key (f64) | data (200 bytes) | next (i64) | deleted (i32)
RECORD_FMT  = f"<d{DATA_SIZE}sqi"
RECORD_SIZE = struct.calcsize(RECORD_FMT)

NO_NEXT = -1


def _pack(key: float, data: bytes, next_offset: int, deleted: int) -> bytes:
    data = data[:DATA_SIZE].ljust(DATA_SIZE, b"\x00")
    return struct.pack(RECORD_FMT, key, data, next_offset, deleted)


def _unpack(raw: bytes) -> tuple:
    key, data, next_offset, deleted = struct.unpack(RECORD_FMT, raw)
    return key, data.rstrip(b"\x00"), next_offset, deleted


from contextlib import contextmanager

class SequentialFile:
    def __init__(self, filepath: str, buffer_manager=None):
        self._main = filepath + ".bin"
        self._aux  = filepath + ".aux"
        self._bm   = buffer_manager
        self._fm   = buffer_manager._fm if buffer_manager else None
        for path in (self._main, self._aux):
            if not os.path.exists(path):
                open(path, "wb").close()

    @contextmanager
    def _lock_and_pin(self, path: str, page_id: int, lock_type: str = "exclusive"):
        if self._bm:
            self._bm.lock_page(path, page_id, lock_type=lock_type)
            try:
                yield
            finally:
                self._bm.unlock_page(path, page_id, lock_type=lock_type)
                self._bm.unpin_page(path, page_id)
        else:
            yield

    def _read_raw(self, path: str, offset: int, size: int) -> bytes:
        if self._bm:
            page_id = offset // PAGE_SIZE
            page_off = offset % PAGE_SIZE
            with self._lock_and_pin(path, page_id, lock_type="shared"):
                page = self._bm.read_page(path, page_id)
                return page[page_off:page_off + size]
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(size)

    def _read_all_records(self, path: str) -> list[tuple]:
        """Lee todos los registros de un archivo en orden, página a página."""
        n = self._record_count(path)
        if n == 0:
            return []
        total_pages = (n * RECORD_SIZE + PAGE_SIZE - 1) // PAGE_SIZE
        records = []
        if self._bm:
            for pid in range(total_pages):
                with self._lock_and_pin(path, pid, lock_type="shared"):
                    page = self._bm.read_page(path, pid)
                rpp = PAGE_SIZE // RECORD_SIZE
                for slot in range(rpp):
                    off = slot * RECORD_SIZE
                    raw = page[off:off + RECORD_SIZE]
                    if len(raw) < RECORD_SIZE:
                        break
                    records.append(_unpack(raw))
        else:
            with open(path, "rb") as f:
                raw = f.read()
            for i in range(len(raw) // RECORD_SIZE):
                records.append(_unpack(raw[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]))
        return records

    def _write_raw(self, path: str, offset: int, data: bytes):
        if self._bm:
            page_id  = offset // PAGE_SIZE
            page_off = offset % PAGE_SIZE
            with self._lock_and_pin(path, page_id):
                # si la página aún no existe en disco, extiende el archivo
                while page_id >= self._fm.page_count(path):
                    self._fm.append_page(path, b"\x00" * PAGE_SIZE)
                page = bytearray(self._bm.read_page(path, page_id))
                page[page_off:page_off + len(data)] = data
                self._bm.write_page(path, page_id, bytes(page))
        else:
            with open(path, "r+b" if os.path.getsize(path) > 0 else "wb") as f:
                f.seek(offset)
                f.write(data)

    def _record_count(self, path: str) -> int:
        return os.path.getsize(path) // RECORD_SIZE

    def _read_record(self, path: str, index: int) -> tuple | None:
        raw = self._read_raw(path, index * RECORD_SIZE, RECORD_SIZE)
        if len(raw) < RECORD_SIZE:
            return None
        return _unpack(raw)

    def _write_record(self, path: str, index: int, key, data, next_off, deleted):
        self._write_raw(path, index * RECORD_SIZE, _pack(key, data, next_off, deleted))

    def add(self, key: float, data: bytes):
        idx = self._record_count(self._aux)
        self._write_record(self._aux, idx, key, data, NO_NEXT, 0)
        if self._record_count(self._aux) >= K_MAX_AUX:
            self._rebuild()

    def search(self, key: float) -> bytes | None:
        result = self._binary_search(key)
        if result is not None:
            return result
        return self._linear_search_aux(key)

    def _binary_search(self, key: float) -> bytes | None:
        n = self._record_count(self._main)
        if n == 0:
            return None
        lo, hi = 0, n - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            rec = self._read_record(self._main, mid)
            if rec is None:
                break
            rkey, rdata, _, deleted = rec
            if rkey == key:
                return None if deleted else rdata
            elif rkey < key:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def _linear_search_aux(self, key: float) -> bytes | None:
        for i in range(self._record_count(self._aux)):
            rec = self._read_record(self._aux, i)
            if rec is None:
                break
            rkey, rdata, _, deleted = rec
            if not deleted and rkey == key:
                return rdata
        return None

    def range_search(self, low: float, high: float) -> list[bytes]:
        results = []
        main_records = self._read_all_records(self._main)
        n = len(main_records)
        if n > 0:
            lo, hi = 0, n - 1
            start = n
            while lo <= hi:
                mid = (lo + hi) // 2
                rkey, _, _, deleted = main_records[mid]
                if rkey >= low:
                    start = mid
                    hi = mid - 1
                else:
                    lo = mid + 1
            for i in range(start, n):
                rkey, rdata, _, deleted = main_records[i]
                if rkey > high:
                    break
                if not deleted:
                    results.append(rdata)

        for rkey, rdata, _, deleted in self._read_all_records(self._aux):
            if not deleted and low <= rkey <= high:
                results.append(rdata)

        return results

    def remove(self, key: float) -> bool:
        if self._mark_deleted(self._main, key, binary=True):
            return True
        return self._mark_deleted(self._aux, key, binary=False)

    def _mark_deleted(self, path: str, key: float, binary: bool) -> bool:
        n = self._record_count(path)
        indices = []
        if binary:
            lo, hi = 0, n - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                rec = self._read_record(path, mid)
                if rec is None:
                    break
                rkey, _, _, _ = rec
                if rkey == key:
                    indices = [mid]; break
                elif rkey < key:
                    lo = mid + 1
                else:
                    hi = mid - 1
        else:
            indices = [i for i in range(n)
                       if (r := self._read_record(path, i)) and not r[3] and r[0] == key]

        for i in indices:
            rec = self._read_record(path, i)
            if rec and not rec[3]:
                self._write_record(path, i, rec[0], rec[1], rec[2], 1)
            return True
        return False

    def _rebuild(self):
        records = []
        for path in (self._main, self._aux):
            for key, data, _, deleted in self._read_all_records(path):
                if not deleted:
                    records.append((key, data))
        records.sort(key=lambda r: r[0])

        if self._bm:
            self._bm.flush(self._main)
            self._bm.flush(self._aux)
            self._bm.invalidate(self._main)
            self._bm.invalidate(self._aux)

        # escribe directo a disco para evitar desincronía entre buffer y archivo truncado
        with open(self._main, "wb") as f:
            for key, data in records:
                f.write(_pack(key, data, NO_NEXT, 0))
        open(self._aux, "wb").close()

    def scan_all(self) -> list[bytes]:
        results = []
        for path in (self._main, self._aux):
            for _, rdata, _, deleted in self._read_all_records(path):
                if not deleted:
                    results.append(rdata)
        return results

    def dump(self):
        print("=== PRINCIPAL ===")
        self._dump_file(self._main)
        print("=== AUXILIAR ===")
        self._dump_file(self._aux)

    def _dump_file(self, path: str):
        for i in range(self._record_count(path)):
            rec = self._read_record(path, i)
            if rec:
                key, data, next_off, deleted = rec
                status = "DEL" if deleted else "OK "
                print(f"  [{i}] key={key} data={data!r} {status}")
