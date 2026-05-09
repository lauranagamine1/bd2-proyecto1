# BD2 - Proyecto 1: AIRBNB BD

**Integrantes**

| Nombre | GitHub |
|---|---|
| Laura Gabriela Nagamine Oshiro | [lauranagamine1](https://github.com/lauranagamine1) |
| Sofía Valentina Ku Paredes | [sofkp](https://github.com/sofkp) |
| Luis Anthony Romero Padilla | [LuixRom](https://github.com/LuixRom) |
| María Karolay Tamayo Hilario | [karolaytamayoh](https://github.com/karolaytamayoh) |

## ¿Qué es esto?

AIRBNB BD es un mini gestor de base de datos hecho desde cero en Python para el Proyecto 1 de Base de Datos 2. Integra parser SQL propio, archivos de registros en disco, índices por columna, buffer pool con páginas de 4 KB, API REST con FastAPI y frontend en React.

El flujo principal es:

1. El usuario escribe una consulta SQL en el frontend.
2. La API envía el texto al motor.
3. El parser convierte SQL a un AST.
4. El `Engine` gestiona scanner-parser y llama al `DBManager`.
5. El `DBManager` usa el archivo base de registros y, si existen, los índices de cada columna.
6. La respuesta vuelve al frontend con resultados, tiempo y estadísticas de disco/buffer.

## ¿Cómo correrlo con Docker?

Requisito: tener Docker Desktop abierto.

```bash
docker compose up --build
```

Se estará corriendo:

- Frontend: `http://localhost:5173`
- Backend/API: `http://localhost:8000`
- Docs de FastAPI: `http://localhost:8000/docs`

Abrir el frontend en `http://localhost:5173`

Para detenerlo:

```bash
docker compose down
```

Docker monta:

- `./data` en `/app/data`, para persistir tablas e índices generados.
- `./dataset` en `/app/dataset`, en modo solo lectura, para cargar CSVs.

## Arquitectura de almacenamiento

Cada tabla tiene un archivo base:

```text
data/tables/<tabla>/records.dat
```

Ese archivo funciona como archivo principal de registros: guarda las filas completas y asigna un `record_id` estable a cada una.

Los índices son opcionales y se crean solo cuando una columna declara `INDEX <técnica>` en el `CREATE TABLE`. Si una columna no tiene índice, las búsquedas sobre esa columna hacen scan sobre `records.dat`.

Los índices guardan la clave y un puntero/`record_id`.

## Estructura

```text
external/
  merge_sort.py          #Merge Sort
  hash_join.py           #Hash Join 
parser/
  scanner.py             # tokenizador
  parser.py              # parser SQL -> AST
  gramatica.md           # gramática EBNF
indexes/
  sequential_file.py     # Sequential File con archivo auxiliar
  b_tree.py              # B+ Tree
  extendible_hashing.py  # Extendible Hashing
  r_tree.py              # R-Tree espacial
  file_manager.py        # capa de páginas en disco
  buffer_manager.py      # buffer pool LRU
manager/
  db_manager.py          # manejo de tablas, registros e índices
  record_file.py         # archivo base de registros
  schemas.py             # tipos, columnas, tablas y records
engine.py                # conecta parser con DBManager
api.py                   # API REST con FastAPI
frontend/                # interfaz en React + Vite
dataset/                 # CSVs de prueba
```

## SQL soportado

```sql
CREATE TABLE <nombre> (<col> <tipo> [INDEX <técnica>], ...) [FROM FILE <path>];

INSERT INTO <tabla> VALUES (...);

SELECT * FROM <tabla>;
SELECT * FROM <tabla> WHERE <col> = <valor>;
SELECT * FROM <tabla> WHERE <col> BETWEEN <v1> AND <v2>;
SELECT * FROM <tabla> WHERE <col> IN (POINT(<x>, <y>), RADIUS <r>);
SELECT * FROM <tabla> WHERE <col> IN (POINT(<x>, <y>), K <k>);

DELETE FROM <tabla> WHERE <col> = <valor>;
```

Tipos soportados:

- `INT`
- `FLOAT`
- `VARCHAR(n)`
- `BOOL`
- `POINT`

Índices soportados:

| Índice | Keyword | Uso principal |
|---|---|---|
| Sequential File | `SEQUENTIAL` | igualdad y rangos numéricos |
| Extendible Hashing | `HASH` | búsqueda puntual, ideal para claves únicas |
| B+ Tree | `BTREE` | igualdad y rangos |
| R-Tree | `RTREE` | radio y KNN sobre `POINT` |

## Ejemplos para demo

El archivo [frontend/examples.txt](frontend/examples.txt) tiene consultas listas para copiar y pegar en el editor del frontend.


## Desarrollo local sin Docker

Backend:

```bash
pip install -r requirements.txt
uvicorn api:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```
