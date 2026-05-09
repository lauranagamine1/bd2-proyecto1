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
        self._fm   = buffer_manager._fm if buffer_manager else None
        self._handles: dict[str, object] = {}
        for path in (self._main, self._aux):
            if not os.path.exists(path):
                open(path, "wb").close()
            self._handles[path] = open(path, "r+b")

    def _fh(self, path: str):
        """Devuelve el file handle abierto para el path."""
        return self._handles[path]

    def _read_raw(self, path: str, offset: int, size: int) -> bytes:
        fh = self._fh(path)
        fh.seek(offset)
        raw = fh.read(size)
        if self._fm:
            self._fm._reads += 1
        return raw

    def _read_all_records(self, path: str) -> list[tuple]:
        """Lee todos los registros del archivo de una sola vez."""
        fh = self._fh(path)
        fh.seek(0, 2)
        size = fh.tell()
        if size == 0:
            return []
        fh.seek(0)
        raw = fh.read()
        if self._fm:
            self._fm._reads += 1
        n = len(raw) // RECORD_SIZE
        return [_unpack(raw[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]) for i in range(n)]

    def _write_raw(self, path: str, offset: int, data: bytes):
        fh = self._fh(path)
        fh.seek(offset)
        fh.write(data)
        fh.flush()
        if self._fm:
            self._fm._writes += 1

    def _record_count(self, path: str) -> int:
        fh = self._fh(path)
        fh.seek(0, 2)
        return fh.tell() // RECORD_SIZE

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

        # truncar y reescribir: cerrar handles, escribir, reabrir
        for path in (self._main, self._aux):
            self._handles[path].close()

        with open(self._main, "wb") as f:
            for key, data in records:
                f.write(_pack(key, data, NO_NEXT, 0))
            if self._fm:
                self._fm._writes += 1
        open(self._aux, "wb").close()

        for path in (self._main, self._aux):
            self._handles[path] = open(path, "r+b")

    def scan_all(self) -> list[bytes]:
        results = []
        for path in (self._main, self._aux):
            for _, rdata, _, deleted in self._read_all_records(path):
                if not deleted:
                    results.append(rdata)
        return results

    def close(self):
        if hasattr(self, "_handles"):
            for h in self._handles.values():
                h.close()
            self._handles = {}

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
