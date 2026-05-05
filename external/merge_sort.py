import os
import sys
import struct
import heapq
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "indexes"))
from file_manager import FileManager

PAGE_SIZE = 4096


class MergeSort:
    """
    External merge sort sobre un archivo de registros de tamaño fijo.

    Parámetros
    ----------
    file_manager : FileManager
        Capa de I/O compartida con el resto del sistema.
    record_size : int
        Tamaño en bytes de cada registro (fijo).
    key_offset : int
        Byte de inicio de la clave dentro del registro.
    key_format : str
        Formato struct de la clave (ej. 'i' para int, 'd' para double, '50s' para str).
    buffer_pages : int
        Número de páginas de buffer disponibles (B >= 3).
    """

    def __init__(
        self,
        file_manager: FileManager,
        record_size: int,
        key_offset: int,
        key_format: str,
        buffer_pages: int = 8,
    ):
        if buffer_pages < 3:
            raise ValueError("Se necesitan al menos 3 páginas de buffer")
        self._fm = file_manager
        self._record_size = record_size
        self._key_offset = key_offset
        self._key_format = key_format
        self._key_size = struct.calcsize(key_format)
        self._buffer_pages = buffer_pages
        self._records_per_page = PAGE_SIZE // record_size
        if self._records_per_page == 0:
            raise ValueError("El registro es más grande que una página")

    # ------------------------------------------------------------------ #
    # API pública                                                          #
    # ------------------------------------------------------------------ #

    def sort(self, input_path: str, output_path: str) -> dict:
        """
        Ordena el archivo `input_path` y escribe el resultado en `output_path`.
        Retorna estadísticas de I/O.
        """
        self._fm.reset_stats()

        total_records = self._record_count(input_path)
        if total_records == 0:
            open(output_path, "wb").close()
            return self._fm.stats

        run_files = self._phase1_create_runs(input_path)
        self._phase2_merge(run_files, output_path)

        stats = self._fm.stats
        stats["passes"] = self._passes_needed(len(run_files))
        stats["runs_initial"] = len(run_files)
        stats["records_sorted"] = total_records
        return stats

    # ------------------------------------------------------------------ #
    # Fase 1: crear runs ordenados                                        #
    # ------------------------------------------------------------------ #

    def _phase1_create_runs(self, input_path: str) -> list[str]:
        total_pages = self._fm.page_count(input_path)
        run_files = []
        page_idx = 0

        while page_idx < total_pages:
            records = []
            pages_read = 0

            while pages_read < self._buffer_pages and page_idx < total_pages:
                page = self._fm.read_page(input_path, page_idx)
                for slot in range(self._records_per_page):
                    offset = slot * self._record_size
                    raw = page[offset: offset + self._record_size]
                    if len(raw) < self._record_size or raw == b"\x00" * self._record_size:
                        break
                    records.append(raw)
                page_idx += 1
                pages_read += 1

            if not records:
                break

            records.sort(key=self._extract_key)

            run_path = self._tmp_file()
            run_files.append(run_path)
            self._write_records(run_path, records)

        return run_files

    # ------------------------------------------------------------------ #
    # Fase 2+: merge de runs                                              #
    # ------------------------------------------------------------------ #

    def _phase2_merge(self, run_files: list[str], output_path: str):
        fan_in = self._buffer_pages - 1  # una página de salida, resto de entrada

        while len(run_files) > 1:
            next_runs = []
            for i in range(0, len(run_files), fan_in):
                batch = run_files[i: i + fan_in]
                if len(batch) == 1 and len(run_files) > fan_in:
                    next_runs.extend(batch)
                    continue
                merged = self._tmp_file()
                self._merge_runs(batch, merged)
                next_runs.append(merged)
                for f in batch:
                    os.remove(f)
            run_files = next_runs

        # copia el run final al destino
        final = run_files[0]
        total_pages = self._fm.page_count(final)
        open(output_path, "wb").close()
        for pid in range(total_pages):
            page = self._fm.read_page(final, pid)
            self._fm.write_page(output_path, pid, page)
        os.remove(final)

    def _merge_runs(self, run_paths: list[str], output_path: str):
        """Merge N runs ordenados en un archivo de salida usando un min-heap."""
        readers = [_RunReader(p, self._fm, self._record_size, self._records_per_page)
                   for p in run_paths]
        writer = _RunWriter(output_path, self._fm, self._record_size, self._records_per_page)

        # heap: (clave, índice_run, raw_record)
        heap = []
        for i, reader in enumerate(readers):
            rec = reader.next()
            if rec is not None:
                heapq.heappush(heap, (self._extract_key(rec), i, rec))

        while heap:
            key, i, rec = heapq.heappop(heap)
            writer.write(rec)
            nxt = readers[i].next()
            if nxt is not None:
                heapq.heappush(heap, (self._extract_key(nxt), i, nxt))

        writer.flush()
        for r in readers:
            r.close()

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _extract_key(self, raw: bytes):
        raw_key = raw[self._key_offset: self._key_offset + self._key_size]
        val = struct.unpack(self._key_format, raw_key)[0]
        if isinstance(val, bytes):
            return val.rstrip(b"\x00")
        return val

    def _record_count(self, path: str) -> int:
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return size // self._record_size

    def _write_records(self, path: str, records: list[bytes]):
        writer = _RunWriter(path, self._fm, self._record_size, self._records_per_page)
        for rec in records:
            writer.write(rec)
        writer.flush()

    def _tmp_file(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".run")
        os.close(fd)
        return path

    def _passes_needed(self, num_runs: int) -> int:
        if num_runs <= 1:
            return 1
        fan_in = self._buffer_pages - 1
        passes = 1
        runs = num_runs
        while runs > 1:
            runs = (runs + fan_in - 1) // fan_in
            passes += 1
        return passes


class _RunReader:
    """Lee un run página a página."""

    def __init__(self, path: str, fm: FileManager, record_size: int, records_per_page: int):
        self._path = path
        self._fm = fm
        self._record_size = record_size
        self._records_per_page = records_per_page
        self._page_idx = 0
        self._buffer: list[bytes] = []
        self._total_pages = fm.page_count(path)
        self._load_page()

    def _load_page(self):
        self._buffer = []
        while self._page_idx < self._total_pages and not self._buffer:
            page = self._fm.read_page(self._path, self._page_idx)
            self._page_idx += 1
            for slot in range(self._records_per_page):
                offset = slot * self._record_size
                raw = page[offset: offset + self._record_size]
                if len(raw) < self._record_size or raw == b"\x00" * self._record_size:
                    break
                self._buffer.append(raw)
        self._pos = 0

    def next(self) -> bytes | None:
        if self._pos >= len(self._buffer):
            self._load_page()
        if self._pos >= len(self._buffer):
            return None
        rec = self._buffer[self._pos]
        self._pos += 1
        return rec

    def close(self):
        pass


class _RunWriter:
    """Escribe registros a un run página a página."""

    def __init__(self, path: str, fm: FileManager, record_size: int, records_per_page: int):
        self._path = path
        self._fm = fm
        self._record_size = record_size
        self._records_per_page = records_per_page
        self._page_id = 0
        self._buffer: list[bytes] = []
        open(path, "wb").close()

    def write(self, record: bytes):
        self._buffer.append(record)
        if len(self._buffer) >= self._records_per_page:
            self._flush_page()

    def _flush_page(self):
        if not self._buffer:
            return
        page = bytearray(PAGE_SIZE)
        for i, rec in enumerate(self._buffer):
            offset = i * self._record_size
            page[offset: offset + self._record_size] = rec
        self._fm.write_page(self._path, self._page_id, bytes(page))
        self._page_id += 1
        self._buffer = []

    def flush(self):
        self._flush_page()
