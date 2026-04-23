# BD2 - Proyecto 1: Heider BD

**Integrantes:** 
- Laura Nagamine
- Sofia Ku

---

Link al PPT: (faltaaa)

Link al informe: (falta)

## ¿Qué es esto?

Un motor de base de datos construido desde cero en Python. Implementa su propio parser SQL, índices en disco y un buffer pool en RAM. Tiene un frontend en React donde puedes escribir y ejecutar consultas directamente.

## Estructura

```
├── parser/
│   ├── scanner.py           # tokenizador
│   ├── parser.py            # parser SQL → AST
│   └── gramatica.md         # gramática EBNF del lenguaje
├── indexes/
│   ├── sequential_file.py   # índice secuencial con archivo auxiliar
│   ├── r_tree.py            # R-Tree para datos espaciales
│   ├── b_tree.py
│   ├── extendible_hashing.py
│   ├── file_manager.py      # única capa que toca disco
│   └── buffer_manager.py    # pool LRU de páginas en RAM
├── engine.py                # conecta parser con índices
├── api.py                   # API REST con FastAPI
└── frontend/                # interfaz en React + Vite
```

## SQL soportado

```sql
-- Crear tabla con índice
CREATE TABLE clientes (id INT INDEX SEQUENTIAL, nombre VARCHAR(50), edad INT);
CREATE TABLE lugares  (nombre VARCHAR(50), coords POINT INDEX RTREE);

-- Cargar desde CSV
CREATE TABLE ventas (id INT INDEX SEQUENTIAL, monto FLOAT) FROM FILE 'ventas.csv';

-- Insertar
INSERT INTO clientes VALUES (1, 'Ana', 28);

-- Consultar todo
SELECT * FROM clientes;

-- Igualdad y rango
SELECT * FROM clientes WHERE id = 1;
SELECT * FROM clientes WHERE id BETWEEN 1 AND 10;

-- Búsqueda espacial
SELECT * FROM lugares WHERE coords IN (POINT(-77.03, -12.04), RADIUS 5.0);
SELECT * FROM lugares WHERE coords IN (POINT(-77.03, -12.04), K 3);

-- Eliminar
DELETE FROM clientes WHERE id = 1;
```

## Índices implementados

| Índice | Keyword | Búsqueda |
|---|---|---|
| Sequential File | `SEQUENTIAL` | igualdad, rango |
| R-Tree | `RTREE` | radio, KNN |
| B-Tree | `BTREE` | — |
| Extendible Hashing | `HASH` | — |

El **Sequential File** mantiene un archivo principal ordenado (`.bin`) y un auxiliar de desbordamiento (`.aux`). Las inserciones van al auxiliar; cuando llega a 10 registros se hace un merge y se reordena todo. Las búsquedas usan búsqueda binaria sobre el principal y lineal sobre el auxiliar.

El **Buffer Manager** implementa un pool LRU de 64 páginas (4096 bytes c/u). Todas las lecturas y escrituras pasan por él antes de llegar a disco, lo que reduce drásticamente los accesos físicos.

## Cómo correrlo

**Backend:**
```bash
pip install fastapi uvicorn pydantic
uvicorn api:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

Abrir `http://localhost:5173`. El frontend muestra syntax highlighting del SQL y estadísticas de disco/buffer por cada consulta ejecutada.
