from __future__ import annotations
from scanner import Scanner, Token, TokenType, ScannerError


def _node(kind: str, **kwargs) -> dict:
    return {"type": kind, **kwargs}


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0

    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _peek_type(self) -> TokenType:
        return self._current().type

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        if tok.type != TokenType.EOF:
            self._pos += 1
        return tok

    def _check(self, *types: TokenType) -> bool:
        return self._peek_type() in types

    def _match(self, *types: TokenType) -> bool:
        if self._check(*types):
            self._advance()
            return True
        return False

    def _expect(self, ttype: TokenType, hint: str = "") -> Token:
        if self._check(ttype):
            return self._advance()
        tok = self._current()
        msg = f"Se esperaba {ttype.name}"
        if hint:
            msg += f" ({hint})"
        msg += f", se encontró {tok.type.name} ({tok.value!r}) en línea {tok.line}, col {tok.col}"
        raise ParseError(msg)

    # --- entry point ---

    def parse(self) -> list[dict]:
        statements = []
        while not self._check(TokenType.EOF):
            statements.append(self._parse_statement())
            self._expect(TokenType.SEMICOLON)
        return statements

    # --- statement dispatch ---

    def _parse_statement(self) -> dict:
        dispatch = {
            TokenType.CREATE: self._parse_create,
            TokenType.SELECT: self._parse_select,
            TokenType.INSERT: self._parse_insert,
            TokenType.DELETE: self._parse_delete,
        }
        fn = dispatch.get(self._peek_type())
        if fn is None:
            tok = self._current()
            raise ParseError(f"Sentencia desconocida en línea {tok.line}: {tok.value!r}")
        return fn()

    # --- CREATE TABLE ---

    def _parse_create(self) -> dict:
        self._expect(TokenType.CREATE)
        self._expect(TokenType.TABLE)
        name = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.LPAREN)
        columns = [self._parse_column_def()]
        while self._match(TokenType.COMMA):
            columns.append(self._parse_column_def())
        self._expect(TokenType.RPAREN)
        file_path = None
        if self._match(TokenType.FROM):
            self._expect(TokenType.FILE)
            file_path = self._expect(TokenType.STRING).value
        return _node("CREATE_TABLE", table=name, columns=columns, file=file_path)

    def _parse_column_def(self) -> dict:
        name = self._expect(TokenType.IDENTIFIER).value
        dtype = self._parse_data_type()
        index = self._parse_index_type() if self._match(TokenType.INDEX) else None
        return {"name": name, "data_type": dtype, "index": index}

    def _parse_data_type(self) -> dict:
        t = self._peek_type()
        if t == TokenType.INT_TYPE:
            self._advance(); return {"kind": "INT"}
        if t == TokenType.FLOAT_TYPE:
            self._advance(); return {"kind": "FLOAT"}
        if t == TokenType.BOOL_TYPE:
            self._advance(); return {"kind": "BOOL"}
        if t == TokenType.POINT:
            # POINT aparece como keyword y como data type; el contexto resuelve la ambigüedad
            self._advance(); return {"kind": "POINT"}
        if t == TokenType.VARCHAR_TYPE:
            self._advance()
            self._expect(TokenType.LPAREN)
            size = self._expect(TokenType.INT).value
            self._expect(TokenType.RPAREN)
            return {"kind": "VARCHAR", "size": size}
        tok = self._current()
        raise ParseError(f"Tipo de dato desconocido en línea {tok.line}: {tok.value!r}")

    def _parse_index_type(self) -> str:
        mapping = {
            TokenType.SEQUENTIAL: "SEQUENTIAL",
            TokenType.HASH:       "HASH",
            TokenType.BTREE:      "BTREE",
            TokenType.RTREE:      "RTREE",
        }
        t = self._peek_type()
        if t in mapping:
            self._advance()
            return mapping[t]
        tok = self._current()
        raise ParseError(f"Tipo de índice inválido en línea {tok.line}: {tok.value!r}")

    # --- SELECT ---

    def _parse_select(self) -> dict:
        self._expect(TokenType.SELECT)
        self._expect(TokenType.STAR)
        self._expect(TokenType.FROM)
        table = self._expect(TokenType.IDENTIFIER).value
        condition = None
        if self._match(TokenType.WHERE):
            condition = self._parse_condition()
        order_by = None
        if self._match(TokenType.ORDER):
            self._expect(TokenType.BY)
            col = self._expect(TokenType.IDENTIFIER).value
            direction = "DESC" if self._match(TokenType.DESC) else "ASC"
            if direction == "ASC":
                self._match(TokenType.ASC)
            order_by = {"column": col, "direction": direction}
        return _node("SELECT", table=table, condition=condition, order_by=order_by)

    def _parse_condition(self) -> dict:
        col = self._expect(TokenType.IDENTIFIER).value

        if self._match(TokenType.EQUALS):
            return _node("EQUALITY", column=col, value=self._parse_value())

        if self._match(TokenType.BETWEEN):
            low = self._parse_value()
            self._expect(TokenType.AND)
            high = self._parse_value()
            return _node("RANGE", column=col, low=low, high=high)

        if self._match(TokenType.IN):
            self._expect(TokenType.LPAREN)
            pt = self._parse_point()
            self._expect(TokenType.COMMA)
            if self._match(TokenType.RADIUS):
                r = self._parse_number()
                self._expect(TokenType.RPAREN)
                return _node("SPATIAL_RADIUS", column=col, point=pt, radius=r)
            if self._match(TokenType.K):
                k = self._expect(TokenType.INT).value
                self._expect(TokenType.RPAREN)
                return _node("SPATIAL_KNN", column=col, point=pt, k=k)
            tok = self._current()
            raise ParseError(f"Se esperaba RADIUS o K en línea {tok.line}, col {tok.col}")

        tok = self._current()
        raise ParseError(f"Condición inválida en línea {tok.line}: se esperaba '=', BETWEEN o IN")

    # --- INSERT ---

    def _parse_insert(self) -> dict:
        self._expect(TokenType.INSERT)
        self._expect(TokenType.INTO)
        table = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.VALUES)
        self._expect(TokenType.LPAREN)
        values = [self._parse_value()]
        while self._match(TokenType.COMMA):
            values.append(self._parse_value())
        self._expect(TokenType.RPAREN)
        return _node("INSERT", table=table, values=values)

    # --- DELETE ---

    def _parse_delete(self) -> dict:
        self._expect(TokenType.DELETE)
        self._expect(TokenType.FROM)
        table = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.WHERE)
        col = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.EQUALS)
        return _node("DELETE", table=table, column=col, value=self._parse_value())

    # --- value literals ---

    def _parse_value(self) -> dict:
        t = self._peek_type()
        if t == TokenType.INT:
            return _node("INT", value=self._advance().value)
        if t == TokenType.FLOAT:
            return _node("FLOAT", value=self._advance().value)
        if t == TokenType.STRING:
            return _node("STRING", value=self._advance().value)
        if t == TokenType.TRUE:
            self._advance(); return _node("BOOLEAN", value=True)
        if t == TokenType.FALSE:
            self._advance(); return _node("BOOLEAN", value=False)
        if t == TokenType.POINT:
            return self._parse_point()
        tok = self._current()
        raise ParseError(f"Valor inválido en línea {tok.line}, col {tok.col}: {tok.value!r}")

    def _parse_number(self) -> float | int:
        t = self._peek_type()
        if t in (TokenType.FLOAT, TokenType.INT):
            return self._advance().value
        tok = self._current()
        raise ParseError(f"Se esperaba un número en línea {tok.line}, col {tok.col}")

    def _parse_point(self) -> dict:
        self._expect(TokenType.POINT)
        self._expect(TokenType.LPAREN)
        x = self._parse_number()
        self._expect(TokenType.COMMA)
        y = self._parse_number()
        self._expect(TokenType.RPAREN)
        return _node("POINT", x=x, y=y)


def parse_sql(source: str) -> list[dict]:
    tokens = Scanner(source).tokenize()
    return Parser(tokens).parse()


if __name__ == "__main__":
    import sys, json

    if len(sys.argv) > 1:
        src = open(sys.argv[1], encoding="utf-8").read()
    else:
        print("Ingresa consultas SQL (exit para salir):")
        lines = []
        while True:
            try:
                line = input(">>> ")
            except EOFError:
                break
            if line.strip().lower() == "exit":
                break
            lines.append(line)
        src = "\n".join(lines)

    try:
        print(json.dumps(parse_sql(src), indent=2, ensure_ascii=False))
    except (ScannerError, ParseError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
