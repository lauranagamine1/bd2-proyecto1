from collections import OrderedDict
from file_manager import FileManager

DEFAULT_POOL_SIZE = 64  # páginas en RAM


class BufferManager:
    """
    Pool de páginas en memoria con política de reemplazo LRU.

    Cuando un índice pide una página, el buffer la busca primero en el pool.
    Si no está (miss), la trae del disco vía FileManager y desaloja la página
    menos usada si el pool está lleno. Las páginas modificadas se marcan dirty
    y se flushean al disco antes de ser desalojadas.
    """

    def __init__(self, file_manager: FileManager, pool_size: int = DEFAULT_POOL_SIZE):
        self._fm = file_manager
        self._pool_size = pool_size
        # clave: (path, page_id) → valor: [data, dirty]
        # OrderedDict mantiene orden de uso: el último accedido va al final
        self._pool: OrderedDict[tuple, list] = OrderedDict()
        self._hits   = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        return {
            "hits":   self._hits,
            "misses": self._misses,
            "pool_used": len(self._pool),
            "pool_size": self._pool_size,
        }

    def reset_stats(self):
        self._hits = self._misses = 0

    def read_page(self, path: str, page_id: int) -> bytes:
        key = (path, page_id)
        if key in self._pool:
            self._hits += 1
            self._pool.move_to_end(key)
            return self._pool[key][0]

        self._misses += 1
        data = self._fm.read_page(path, page_id)
        self._load_into_pool(key, data, dirty=False)
        return data

    def write_page(self, path: str, page_id: int, data: bytes):
        key = (path, page_id)
        if key in self._pool:
            self._pool[key][0] = data
            self._pool[key][1] = True
            self._pool.move_to_end(key)
        else:
            self._load_into_pool(key, data, dirty=True)

    def append_page(self, path: str, data: bytes) -> int:
        # append siempre va directo a disco para obtener el page_id correcto
        page_id = self._fm.append_page(path, data)
        key = (path, page_id)
        self._load_into_pool(key, data, dirty=False)
        return page_id

    def flush(self, path: str | None = None):
        """Escribe al disco todas las páginas dirty del path dado (o todas)."""
        for key, (data, dirty) in list(self._pool.items()):
            if dirty and (path is None or key[0] == path):
                self._fm.write_page(key[0], key[1], data)
                self._pool[key][1] = False

    def invalidate(self, path: str):
        """Descarta todas las páginas cacheadas de un archivo (útil tras truncar)."""
        keys = [k for k in self._pool if k[0] == path]
        for k in keys:
            del self._pool[k]

    def flush_all(self):
        self.flush()

    def _load_into_pool(self, key: tuple, data: bytes, dirty: bool):
        if len(self._pool) >= self._pool_size:
            self._evict()
        self._pool[key] = [data, dirty]

    def _evict(self):
        # desaloja el elemento más antiguo (LRU = primero del OrderedDict)
        evict_key, (data, dirty) = next(iter(self._pool.items()))
        if dirty:
            self._fm.write_page(evict_key[0], evict_key[1], data)
        del self._pool[evict_key]
