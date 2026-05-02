import threading
from collections import OrderedDict
import os
import sys
sys.path.insert(0, "../manager")

TransactionLogger = None
if os.environ.get("BD2_TRANSACTION_LOG") == "1":
    try:
        from transaction_log import TransactionLogger
    except ImportError:
        try:
            from manager.transaction_log import TransactionLogger
        except ImportError:
            TransactionLogger = None

from file_manager import FileManager

DEFAULT_POOL_SIZE = 64 


class BufferManager:

    def __init__(self, file_manager: FileManager, pool_size: int = DEFAULT_POOL_SIZE):
        self._fm = file_manager
        self._pool_size = pool_size
        
        self._pool: OrderedDict[tuple, list] = OrderedDict()
        
        self._pool_lock = threading.Lock()
        self._page_locks = {}
        
        self._hits   = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        with self._pool_lock:
            return {
                "hits":   self._hits,
                "misses": self._misses,
                "pool_used": len(self._pool),
                "pool_size": self._pool_size,
            }

    def reset_stats(self):
        with self._pool_lock:
            self._hits = self._misses = 0

    def _get_page_lock(self, key):
        with self._pool_lock:
            if key not in self._page_locks:
                self._page_locks[key] = threading.RLock()
            return self._page_locks[key]

    def lock_page(self, path: str, page_id: int):
        key = (path, page_id)
        lock = self._get_page_lock(key)
        if TransactionLogger:
            TransactionLogger().log(threading.current_thread().name, "ACQUIRE", f"Pidiendo lock para {key}")
        lock.acquire()
        if TransactionLogger:
            TransactionLogger().log(threading.current_thread().name, "LOCKED", f"Lock obtenido para {key}")

    def unlock_page(self, path: str, page_id: int):
        key = (path, page_id)
        lock = self._get_page_lock(key)
        lock.release()
        if TransactionLogger:
            TransactionLogger().log(threading.current_thread().name, "RELEASED", f"Lock liberado para {key}")

    def read_page(self, path: str, page_id: int) -> bytes:
        key = (path, page_id)
        
        if TransactionLogger:
            TransactionLogger().log(threading.current_thread().name, "READ", f"Leyendo {key}")
            
        with self._pool_lock:
            if key in self._pool:
                self._hits += 1
                self._pool.move_to_end(key)
                self._pool[key][2] += 1 # pin
                return self._pool[key][0]
            
            self._misses += 1
        data = self._fm.read_page(path, page_id)
        
        with self._pool_lock:
            if key in self._pool:
                self._pool.move_to_end(key)
                self._pool[key][2] += 1
                return self._pool[key][0]
                
            self._load_into_pool(key, data, dirty=False)
            self._pool[key][2] += 1
            return data

    def write_page(self, path: str, page_id: int, data: bytes):
        key = (path, page_id)
        
        if TransactionLogger:
            TransactionLogger().log(threading.current_thread().name, "WRITE", f"Escribiendo {key}")
            
        with self._pool_lock:
            if key in self._pool:
                self._pool[key][0] = data
                self._pool[key][1] = True
                self._pool.move_to_end(key)
            else:
                self._load_into_pool(key, data, dirty=True)

    def append_page(self, path: str, data: bytes) -> int:
        page_id = self._fm.append_page(path, data)
        key = (path, page_id)
        
        if TransactionLogger:
            TransactionLogger().log(threading.current_thread().name, "APPEND", f"Nueva página {key}")
            
        with self._pool_lock:
            self._load_into_pool(key, data, dirty=False)
        return page_id

    def unpin_page(self, path: str, page_id: int):
        key = (path, page_id)
        with self._pool_lock:
            if key in self._pool:
                if self._pool[key][2] > 0:
                    self._pool[key][2] -= 1

    def flush(self, path: str | None = None):
        with self._pool_lock:
            for key, (data, dirty, pin_count) in list(self._pool.items()):
                if dirty and (path is None or key[0] == path):
                    self._fm.write_page(key[0], key[1], data)
                    self._pool[key][1] = False

    def invalidate(self, path: str):
        with self._pool_lock:
            keys = [k for k in self._pool if k[0] == path]
            for k in keys:
                del self._pool[k]

    def flush_all(self):
        self.flush()

    def _load_into_pool(self, key: tuple, data: bytes, dirty: bool):
        if len(self._pool) >= self._pool_size:
            self._evict()
        self._pool[key] = [data, dirty, 0]

    def _evict(self):
        evict_key = None
        for k, v in self._pool.items():
            if v[2] == 0: 
                evict_key = k
                break
                
        if evict_key is None:
            evict_key, _ = next(iter(self._pool.items()))
            
        data, dirty, _ = self._pool[evict_key]
        if dirty:
            self._fm.write_page(evict_key[0], evict_key[1], data)
        del self._pool[evict_key]
