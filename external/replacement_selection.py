import os
import sys
import struct
import heapq
import tempfile

# Asegurar que se puedan importar los módulos de indexes
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "indexes"))
try:
    from file_manager import FileManager
except ImportError:
    # Fallback si se ejecuta desde otro lugar
    sys.path.insert(0, "indexes")
    from file_manager import FileManager

PAGE_SIZE = 4096

class ReplacementSelection:
    """
    Algoritmo de Replacement Selection para generar runs largos en disco.
    """

    def __init__(
        self,
        file_manager: FileManager,
        record_size: int,
        key_offset: int,
        key_format: str,
    ):
        self._fm = file_manager
        self._record_size = record_size
        self._key_offset = key_offset
        self._key_format = key_format
        self._key_size = struct.calcsize(key_format)
        self._records_per_page = PAGE_SIZE // record_size
        if self._records_per_page == 0:
            raise ValueError("El registro es más grande que una página")

    def generate_runs(self, input_path: str, output_dir: str, buffer_pages: int = 10) -> dict:
        """
        Genera runs usando Replacement Selection.
        Retorna estadísticas del proceso.
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # Reader para el archivo de entrada
        reader = _RunReader(input_path, self._fm, self._record_size, self._records_per_page)
        
        # Tamaño del heap en registros
        heap_capacity = buffer_pages * self._records_per_page
        
        # Heaps
        active_heap = [] # list of (key, raw_record)
        frozen_heap = [] # list of (key, raw_record)
        
        # Llenar el heap inicial
        for _ in range(heap_capacity):
            rec = reader.next()
            if rec is None:
                break
            heapq.heappush(active_heap, (self._extract_key(rec), rec))
            
        run_count = 0
        run_paths = []
        last_key = None
        
        writer = None
        total_records_written = 0
        
        def start_new_run():
            nonlocal writer, run_count
            if writer:
                writer.flush()
            run_path = os.path.join(output_dir, f"run_{run_count}.bin")
            run_paths.append(run_path)
            run_count += 1
            return _RunWriter(run_path, self._fm, self._record_size, self._records_per_page)

        if active_heap:
            writer = start_new_run()
        else:
            # Archivo vacío
            return {
                "num_runs": 0,
                "run_paths": [],
                "total_records": 0,
                "reads": self._fm.stats["reads"],
                "writes": self._fm.stats["writes"],
                "avg_run_size_pages": 0
            }

        while active_heap or frozen_heap:
            if not active_heap:
                # El run actual termina, el heap frío se vuelve activo
                active_heap = frozen_heap
                frozen_heap = []
                last_key = None
                writer = start_new_run()
            
            # 3. El registro mínimo del heap se escribe al run actual
            key, rec = heapq.heappop(active_heap)
            writer.write(rec)
            total_records_written += 1
            last_key = key
            
            # 4. El siguiente registro del input se lee
            next_rec = reader.next()
            if next_rec is not None:
                next_key = self._extract_key(next_rec)
                if next_key >= last_key:
                    # Entra al heap activo (mismo run)
                    heapq.heappush(active_heap, (next_key, next_rec))
                else:
                    # Va al heap frío (próximo run)
                    heapq.heappush(frozen_heap, (next_key, next_rec))
                    
        if writer:
            writer.flush()
            
        # Estadísticas
        stats = self._fm.stats
        stats["num_runs"] = run_count
        stats["run_paths"] = run_paths
        stats["total_records"] = total_records_written
        stats["avg_run_size_pages"] = (stats["writes"] / run_count) if run_count > 0 else 0
        
        return stats

    def _extract_key(self, raw: bytes):
        raw_key = raw[self._key_offset: self._key_offset + self._key_size]
        val = struct.unpack(self._key_format, raw_key)[0]
        if isinstance(val, bytes):
            return val.rstrip(b"\x00")
        return val

class _RunReader:
    """Lee registros página a página para evitar cargar todo en RAM."""
    def __init__(self, path: str, fm: FileManager, record_size: int, records_per_page: int):
        self._path = path
        self._fm = fm
        self._record_size = record_size
        self._records_per_page = records_per_page
        self._page_idx = 0
        self._buffer = []
        self._total_pages = fm.page_count(path)

    def next(self) -> bytes | None:
        if not self._buffer:
            if self._page_idx >= self._total_pages:
                return None
            page = self._fm.read_page(self._path, self._page_idx)
            self._page_idx += 1
            for slot in range(self._records_per_page):
                offset = slot * self._record_size
                raw = page[offset : offset + self._record_size]
                if len(raw) < self._record_size or raw == b"\x00" * self._record_size:
                    break
                self._buffer.append(raw)
        
        return self._buffer.pop(0) if self._buffer else None

class _RunWriter:
    """Escribe registros página a página."""
    def __init__(self, path: str, fm: FileManager, record_size: int, records_per_page: int):
        self._path = path
        self._fm = fm
        self._record_size = record_size
        self._records_per_page = records_per_page
        self._page_id = 0
        self._buffer = []
        # Asegurar que el archivo existe y está vacío
        with open(path, "wb") as f:
            pass

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
            page[offset : offset + self._record_size] = rec
        self._fm.write_page(self._path, self._page_id, bytes(page))
        self._page_id += 1
        self._buffer = []

    def flush(self):
        self._flush_page()
