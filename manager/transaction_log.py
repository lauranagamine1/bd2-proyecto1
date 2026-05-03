import os
import threading
from datetime import datetime

class TransactionLogger:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, log_file="data/transactions.log"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TransactionLogger, cls).__new__(cls)
                cls._instance._init(log_file)
            return cls._instance

    def _init(self, log_file):
        self.log_file = log_file
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(self.log_file, "w") as f:
            f.write(f"--- Transaction Log Started at {datetime.now()} ---\n")

    def log(self, thread_name: str, operation: str, details: str):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = f"[{timestamp}] [Thread: {thread_name:^12}] {operation:^12} | {details}\n"
        
        with self._lock:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry)

    def log_causal(self, tx_id: str, lock_type: str, page_id: str, state: str, extra: str = ""):
        if state == "UNLOCK":
            msg = f"[{tx_id}] UNLOCK página {page_id}\n"
        else:
            msg = f"[{tx_id}] LOCK {lock_type.upper()} página {page_id} -> {state}"
            if extra:
                msg += f" ({extra})"
            msg += "\n"
            
        with self._lock:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(msg)
