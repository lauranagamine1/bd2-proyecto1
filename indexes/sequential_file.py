import struct
import os

K_MAX_AUX = 10
PAGE_SIZE  = 4096

KEY_FMT   = "d"
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


class SequentialFile:
    def __init__(self, filepath: str):
        self._main = filepath + ".bin"
        self._aux  = filepath + ".aux"
        self._disk_reads  = 0
        self._disk_writes = 0
        for path in (self._main, self._aux):
            if not os.path.exists(path):
                open(path, "wb").close()

    @property
    def disk_accesses(self) -> dict:
        return {"reads": self._disk_reads, "writes": self._disk_writes}

    def reset_stats(self):
        self._disk_reads = self._disk_writes = 0

    def _read_record(self, f, offset: int) -> tuple | None:
        f.seek(offset)
        raw = f.read(RECORD_SIZE)
        self._disk_reads += 1
        if len(raw) < RECORD_SIZE:
            return None
        return _unpack(raw)

    def _write_record(self, f, offset: int, key, data, next_off, deleted):
        f.seek(offset)
        f.write(_pack(key, data, next_off, deleted))
        self._disk_writes += 1

    def _record_count(self, path: str) -> int:
        return os.path.getsize(path) // RECORD_SIZE

    def add(self, key: float, data: bytes):
        with open(self._aux, "r+b" if os.path.getsize(self._aux) > 0 else "wb") as f:
            offset = self._record_count(self._aux) * RECORD_SIZE
            self._write_record(f, offset, key, data, NO_NEXT, 0)
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
        with open(self._main, "rb") as f:
            while lo <= hi:
                mid = (lo + hi) // 2
                rec = self._read_record(f, mid * RECORD_SIZE)
                if rec is None:
                    break
                rkey, rdata, _, deleted = rec
                if deleted:
                    hi = mid - 1
                    continue
                if rkey == key:
                    return rdata
                elif rkey < key:
                    lo = mid + 1
                else:
                    hi = mid - 1
        return None

    def _linear_search_aux(self, key: float) -> bytes | None:
        n = self._record_count(self._aux)
        with open(self._aux, "rb") as f:
            for i in range(n):
                rec = self._read_record(f, i * RECORD_SIZE)
                if rec is None:
                    break
                rkey, rdata, _, deleted = rec
                if not deleted and rkey == key:
                    return rdata
        return None

    def range_search(self, low: float, high: float) -> list[bytes]:
        results = []
        n = self._record_count(self._main)
        if n > 0:
            lo, hi = 0, n - 1
            start = n
            with open(self._main, "rb") as f:
                # localiza el primer registro >= low con búsqueda binaria
                while lo <= hi:
                    mid = (lo + hi) // 2
                    rec = self._read_record(f, mid * RECORD_SIZE)
                    if rec is None:
                        break
                    rkey, _, _, deleted = rec
                    if rkey >= low and not deleted:
                        start = mid
                        hi = mid - 1
                    elif rkey < low:
                        lo = mid + 1
                    else:
                        hi = mid - 1
                for i in range(start, n):
                    rec = self._read_record(f, i * RECORD_SIZE)
                    if rec is None:
                        break
                    rkey, rdata, _, deleted = rec
                    if rkey > high:
                        break
                    if not deleted:
                        results.append(rdata)

        # el auxiliar no está ordenado, hay que recorrerlo completo
        with open(self._aux, "rb") as f:
            for i in range(self._record_count(self._aux)):
                rec = self._read_record(f, i * RECORD_SIZE)
                if rec is None:
                    break
                rkey, rdata, _, deleted = rec
                if not deleted and low <= rkey <= high:
                    results.append(rdata)

        return results

    def remove(self, key: float) -> bool:
        if self._mark_deleted_main(key):
            return True
        return self._mark_deleted_aux(key)

    def _mark_deleted_main(self, key: float) -> bool:
        n = self._record_count(self._main)
        lo, hi = 0, n - 1
        with open(self._main, "r+b") as f:
            while lo <= hi:
                mid = (lo + hi) // 2
                rec = self._read_record(f, mid * RECORD_SIZE)
                if rec is None:
                    break
                rkey, rdata, next_off, deleted = rec
                if rkey == key:
                    if not deleted:
                        self._write_record(f, mid * RECORD_SIZE, rkey, rdata, next_off, 1)
                    return True
                elif rkey < key:
                    lo = mid + 1
                else:
                    hi = mid - 1
        return False

    def _mark_deleted_aux(self, key: float) -> bool:
        n = self._record_count(self._aux)
        with open(self._aux, "r+b") as f:
            for i in range(n):
                rec = self._read_record(f, i * RECORD_SIZE)
                if rec is None:
                    break
                rkey, rdata, next_off, deleted = rec
                if not deleted and rkey == key:
                    self._write_record(f, i * RECORD_SIZE, rkey, rdata, next_off, 1)
                    return True
        return False

    def _rebuild(self):
        records = []

        def _collect(path):
            n = self._record_count(path)
            with open(path, "rb") as f:
                for i in range(n):
                    rec = self._read_record(f, i * RECORD_SIZE)
                    if rec and not rec[3]:
                        records.append((rec[0], rec[1]))

        _collect(self._main)
        _collect(self._aux)
        records.sort(key=lambda r: r[0])

        with open(self._main, "wb") as f:
            for key, data in records:
                self._write_record(f, f.tell(), key, data, NO_NEXT, 0)

        open(self._aux, "wb").close()

    def dump(self):
        print("=== PRINCIPAL ===")
        self._dump_file(self._main)
        print("=== AUXILIAR ===")
        self._dump_file(self._aux)

    def _dump_file(self, path: str):
        n = self._record_count(path)
        with open(path, "rb") as f:
            for i in range(n):
                rec = self._read_record(f, i * RECORD_SIZE)
                if rec:
                    key, data, next_off, deleted = rec
                    status = "DEL" if deleted else "OK "
                    print(f"  [{i}] key={key} data={data!r} next={next_off} {status}")
