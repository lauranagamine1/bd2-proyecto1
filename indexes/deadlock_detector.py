import threading

class DeadlockDetectedError(Exception):
    pass

class DeadlockDetector:
    _instance = None
    _singleton_lock = threading.Lock()

    def __new__(cls):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super(DeadlockDetector, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.wait_for_graph = {}
        self.graph_lock = threading.Lock()

    def add_edge(self, waiting_tx: str, holding_tx: str):
        if waiting_tx == holding_tx:
            return
            
        with self.graph_lock:
            if waiting_tx not in self.wait_for_graph:
                self.wait_for_graph[waiting_tx] = set()
            self.wait_for_graph[waiting_tx].add(holding_tx)

    def remove_edges(self, tx_id: str):
        with self.graph_lock:
            if tx_id in self.wait_for_graph:
                del self.wait_for_graph[tx_id]
                
    def clear_all_dependencies(self, tx_id: str):
        with self.graph_lock:
            if tx_id in self.wait_for_graph:
                del self.wait_for_graph[tx_id]
            for waiting_tx, holding_set in self.wait_for_graph.items():
                if tx_id in holding_set:
                    holding_set.remove(tx_id)

    def has_cycle(self, start_tx: str) -> bool:
        with self.graph_lock:
            visited = set()
            
            def dfs(current, path_visited):
                if current in path_visited:
                    return True
                if current in visited:
                    return False
                
                visited.add(current)
                path_visited.add(current)
                
                for neighbor in self.wait_for_graph.get(current, set()):
                    if dfs(neighbor, path_visited):
                        return True
                
                path_visited.remove(current)
                return False

            return dfs(start_tx, set())
