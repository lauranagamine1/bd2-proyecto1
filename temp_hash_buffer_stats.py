import shutil
from pathlib import Path

from engine import Engine


BASE = Path("temp_hash_buffer_data")


def show(label, engine):
    stats = engine.stats()
    print(f"\n--- {label} ---")
    print("disk reads:   ", stats["disk"]["reads"])
    print("disk writes:  ", stats["disk"]["writes"])
    print("buffer hits:  ", stats["buffer"]["hits"])
    print("buffer misses:", stats["buffer"]["misses"])
    print("pool used:    ", stats["buffer"]["pool_used"])


def run():
    if BASE.exists():
        shutil.rmtree(BASE)

    engine = Engine(data_dir=str(BASE))

    engine.reset_stats()
    print(engine.run("CREATE TABLE h (id INT INDEX HASH, nombre VARCHAR(30));"))
    show("CREATE TABLE con HASH", engine)

    engine.reset_stats()
    print(engine.run("INSERT INTO h VALUES (1, 'Ana');"))
    show("INSERT usando HASH", engine)

    engine.reset_stats()
    print(engine.run("SELECT * FROM h WHERE id = 1;"))
    show("SELECT usando HASH en el mismo Engine", engine)

    reloaded = Engine(data_dir=str(BASE))
    reloaded.reset_stats()
    print(reloaded.run("SELECT * FROM h WHERE id = 1;"))
    show("SELECT despues de reiniciar Engine", reloaded)

    shutil.rmtree(BASE)


if __name__ == "__main__":
    run()
