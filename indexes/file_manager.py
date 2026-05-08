import os

PAGE_SIZE = 4096


class FileManager:
    """
    Única capa que toca disco. Todos los índices pasan por aquí.
    Las páginas son bloques de PAGE_SIZE bytes identificados por (path, page_id).
    """

    def __init__(self):
        self._reads  = 0
        self._writes = 0

    @property
    def stats(self) -> dict:
        return {"reads": self._reads, "writes": self._writes}

    def reset_stats(self):
        self._reads = self._writes = 0

    def read_page(self, path: str, page_id: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(page_id * PAGE_SIZE)
            raw = f.read(PAGE_SIZE)
        self._reads += 1
        return raw.ljust(PAGE_SIZE, b"\x00")

    def write_page(self, path: str, page_id: int, data: bytes):
        data = data[:PAGE_SIZE].ljust(PAGE_SIZE, b"\x00")
        current_pages = self.page_count(path)

        if current_pages <= page_id:
            missing_pages = page_id - current_pages + 1
            with open(path, "ab") as f:
                for _ in range(missing_pages):
                    f.write(b"\x00" * PAGE_SIZE)

        with open(path, "r+b") as f:
            f.seek(page_id * PAGE_SIZE)
            f.write(data)
        self._writes += 1

    def append_page(self, path: str, data: bytes) -> int:
        data = data[:PAGE_SIZE].ljust(PAGE_SIZE, b"\x00")
        with open(path, "ab") as f:
            page_id = f.tell() // PAGE_SIZE
            f.write(data)
        self._writes += 1
        return page_id

    def page_count(self, path: str) -> int:
        if not os.path.exists(path):
            return 0
        return os.path.getsize(path) // PAGE_SIZE

    def read_bytes(self, path: str, offset: int, size: int) -> bytes:
        """Lee bytes arbitrarios del archivo y contabiliza el acceso."""
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(size)
        self._reads += 1
        return raw

    def write_bytes(self, path: str, offset: int, data: bytes):
        """Escribe bytes arbitrarios en el archivo y contabiliza el acceso."""
        mode = "r+b" if os.path.getsize(path) > 0 else "wb"
        with open(path, mode) as f:
            f.seek(offset)
            f.write(data)
        self._writes += 1

    def ensure_file(self, path: str):
        if not os.path.exists(path):
            open(path, "wb").close()
