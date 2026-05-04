import threading

class RWLock:
    def __init__(self):
        self._condition = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = None
        self._holding_txs = set() 

    def acquire_shared(self, tx_id: str, deadlock_check_callback=None, check_interval=0.1):
        with self._condition:
            while self._writer is not None and self._writer != tx_id:
                if deadlock_check_callback:
                    deadlock_check_callback(self._holding_txs.copy())
                self._condition.wait(timeout=check_interval)
            
            self._readers += 1
            self._holding_txs.add(tx_id)

    def acquire_exclusive(self, tx_id: str, deadlock_check_callback=None, check_interval=0.1):
        with self._condition:
            while (self._writer is not None and self._writer != tx_id) or \
                  (self._readers > 0 and not (self._readers == 1 and tx_id in self._holding_txs)):
                
                if deadlock_check_callback:
                    deadlock_check_callback(self._holding_txs.copy())
                self._condition.wait(timeout=check_interval)
            
            if self._writer != tx_id:
                if tx_id in self._holding_txs:
                    self._readers -= 1
                    
                self._writer = tx_id
                self._holding_txs.add(tx_id)

    def release(self, tx_id: str):
        with self._condition:
            if tx_id in self._holding_txs:
                if self._writer == tx_id:
                    self._writer = None
                    self._holding_txs.remove(tx_id)
                else:
                    self._readers -= 1
                    self._holding_txs.remove(tx_id)
                self._condition.notify_all()

    def get_holders(self) -> set:
        with self._condition:
            return self._holding_txs.copy()
