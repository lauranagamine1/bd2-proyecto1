# Gramática

---

## Leyenda (EBNF)

| Símbolo | Significado |
|---|---|
| `::=` | definición de regla |
| `\|` | alternativa |
| `{ }` | cero o más repeticiones |
| `[ ]` | opcional (cero o una vez) |
| `( )` | agrupación |
| `' '` | terminal literal |
| `MAYÚSCULAS` | terminal (token) |
| `minúsculas` | no-terminal |

---

## Gramática

```ebnf
program ::= { statement ';' }

statement ::=
    'CREATE' 'TABLE' IDENTIFIER
        '(' column_def { ',' column_def } ')'
        [ 'FROM' 'FILE' PATH ]
    | 'SELECT' '*' 'FROM' IDENTIFIER [ 'WHERE' condition ]
    | 'INSERT' 'INTO' IDENTIFIER 'VALUES' '(' value { ',' value } ')'
    | 'DELETE' 'FROM' IDENTIFIER 'WHERE' IDENTIFIER '=' value

column_def ::=
    IDENTIFIER data_type [ 'INDEX' index_type ]

data_type ::=
    'INT' | 'FLOAT' | 'VARCHAR' '(' INT ')' | 'BOOL' | 'POINT'

index_type ::=
    'SEQUENTIAL' | 'HASH' | 'BTREE' | 'RTREE'

condition ::=
    IDENTIFIER '=' value
    | IDENTIFIER 'BETWEEN' value 'AND' value
    | IDENTIFIER 'IN' '(' point ',' 'RADIUS' NUMBER ')'
    | IDENTIFIER 'IN' '(' point ',' 'K' INT ')'

value ::=
    INT | FLOAT | STRING | BOOLEAN | point

point ::=
    'POINT' '(' NUMBER ',' NUMBER ')'

IDENTIFIER ::= LETTER { LETTER | DIGIT | '_' }
INT        ::= [ '-' ] DIGIT { DIGIT }
FLOAT      ::= [ '-' ] DIGIT { DIGIT } '.' DIGIT { DIGIT }
NUMBER     ::= INT | FLOAT
STRING     ::= "'" { CHAR } "'"
BOOLEAN    ::= 'TRUE' | 'FALSE'
PATH       ::= "'" { CHAR } "'"
LETTER     ::= 'a' | ... | 'z' | 'A' | ... | 'Z'
DIGIT      ::= '0' | ... | '9'
CHAR       ::= cualquier carácter excepto "'"
```

---

## Autómata del Scanner (AFD por categoría de token)

El scanner reconoce cada token mediante un autómata finito determinista (AFD). Los estados relevantes son:

```
Estado inicial → q0

IDENTIFICADOR / PALABRA CLAVE:
  q0 --[a-zA-Z]--> q1 --[a-zA-Z0-9_]--> q1  (acepta en q1)
  Si el lexema es una palabra clave reservada, se emite el token de esa keyword.

ENTERO:
  q0 --['-']--> q2 --[0-9]--> q3 --[0-9]--> q3  (acepta en q3)
  q0 --[0-9]--> q3

FLOTANTE (extensión de ENTERO):
  q3 --['.']--> q4 --[0-9]--> q5 --[0-9]--> q5  (acepta en q5)

CADENA:
  q0 --["'"]--> q6 --[cualquier char ≠ "'"]--> q6 --["'"]--> q7  (acepta en q7)

SÍMBOLO (un carácter):
  ( ) , ; = *  →  token directo desde q0
```

---

## Mapeo sentencia → método del índice

| Sentencia SQL | Método del índice |
|---|---|
| `SELECT * FROM tabla` | `index.scan_all()` |
| `SELECT ... WHERE col = v` | `index.search(v)` |
| `SELECT ... WHERE col BETWEEN v1 AND v2` | `index.range_search(v1, v2)` |
| `SELECT ... WHERE col IN (POINT(...), RADIUS r)` | `index.range_search(point, r)` |
| `SELECT ... WHERE col IN (POINT(...), K k)` | `index.knn(point, k)` |
| `INSERT INTO ... VALUES (...)` | `index.add(record)` |
| `DELETE FROM ... WHERE col = v` | `index.remove(v)` |
| `CREATE TABLE ... FROM FILE path` | carga CSV + `index.add(record)` por fila |

---

## Notas de implementación

- El parser es **case-insensitive** para las palabras clave (`SELECT`, `select`, `Select` son equivalentes).
- El tipo de índice a usar en cada operación se determina por la declaración `INDEX <tipo>` de la columna en `CREATE TABLE`.
- Si una columna no tiene `INDEX`, no soporta búsqueda directa por esa columna.
- `DELETE` solo soporta condición de igualdad (no rango), ya que `remove` recibe una clave puntual.
- `POINT` solo es válido como valor en columnas de tipo `POINT` con `INDEX RTREE`.
- `FLOAT` tiene precedencia sobre `INT` cuando el lexema contiene `'.'`.
- `TRUE` y `FALSE` se reconocen como `BOOLEAN`, no como `IDENTIFIER`.
