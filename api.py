import sys
import time

sys.path.insert(0, "parser")
sys.path.insert(0, "indexes")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from engine import Engine, EngineError

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = Engine()


class QueryRequest(BaseModel):
    sql: str


@app.post("/query")
def run_query(req: QueryRequest):
    engine.reset_stats()
    t0 = time.perf_counter()
    try:
        results = engine.run(req.sql)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "results": results,
        "stats": {
            **engine.stats(),
            "time_ms": round(elapsed_ms, 2),
        },
    }


@app.get("/stats")
def get_stats():
    return engine.stats()
