from pathlib import Path
import csv
import shutil
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
INDEXES_DIR = BASE_DIR / "indexes"
if str(INDEXES_DIR) not in sys.path:
    sys.path.append(str(INDEXES_DIR))

from extendible_hashing import ExtendibleHash, MAX_GLOBAL_DEPTH


CSV_PATH = BASE_DIR / "dataset" / "Airbnb_Open_Data.csv"
TEST_HASH_PATH = BASE_DIR / "data" / "tests" / "extendible_hashing"


def reset_test_data():
    if TEST_HASH_PATH.exists():
        shutil.rmtree(TEST_HASH_PATH)
    TEST_HASH_PATH.mkdir(parents=True, exist_ok=True)


def build_hash(folder_name: str, use_buffer: bool = False):
    folder = TEST_HASH_PATH / folder_name
    shutil.rmtree(folder, ignore_errors=True)
    return ExtendibleHash(str(folder), use_buffer=use_buffer)

def load_csv_ids(limit: int = 120):
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        for record_id, row in enumerate(reader):
            if not row or all((value or "").strip() == "" for value in row.values()):
                continue
            rows.append((int(row["id"]), record_id))
            if len(rows) >= limit:
                break
    return rows


def test():
    print("\n" + "=" * 60)
    print("EXTENDIBLE HASH - FLUJO REAL CON CSV Y PAGINADO")

    reset_test_data()
    h = build_hash("csv_airbnb")

    entries = load_csv_ids(limit=120)
    print("Total de claves cargadas desde dataset:", len(entries))
    print("Buckets por pagina:", h.buckets_per_page)

    print("\nInsertando ids del CSV como key y record_id del CSV como value...")
    for key, record_id in entries:
        h.insert(key, record_id)

    print("\nEstado fisico final del hash:")
    h.print_file()

    print("\nVerificando search en ids reales del dataset:")
    sample_positions = [0, 1, 25, 60, 119]
    for pos in sample_positions:
        key, expected_record_id = entries[pos]
        found = h.search(key)
        print(f"search({key}) -> {found}")
        assert found == expected_record_id

    print("\nProbando delete y reinsert en datos reales:")
    key_to_delete, expected_record_id = entries[10]
    removed = h.delete(key_to_delete)
    print(f"delete({key_to_delete}) -> {removed}")
    assert removed is True
    assert h.search(key_to_delete) is None

    h.insert(key_to_delete, expected_record_id)
    restored = h.search(key_to_delete)
    print(f"search({key_to_delete}) despues de reinsert -> {restored}")
    assert restored == expected_record_id

if __name__ == "__main__":
    test()
