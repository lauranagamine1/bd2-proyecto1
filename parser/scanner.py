from enum import Enum, auto
from dataclasses import dataclass


class TokenType(Enum):
    # Keywords
    CREATE   = auto()
    TABLE    = auto()
    FROM     = auto()
    FILE     = auto()
    SELECT   = auto()
    INSERT   = auto()
    INTO     = auto()
    VALUES   = auto()
    DELETE   = auto()
    WHERE    = auto()
    BETWEEN  = auto()
    AND      = auto()
    IN       = auto()
    POINT    = auto()
    RADIUS   = auto()
    K        = auto()
    INDEX    = auto()
    # Data types
    INT_TYPE     = auto()
    FLOAT_TYPE   = auto()
    VARCHAR_TYPE = auto()
    BOOL_TYPE    = auto()
    # Index types
    SEQUENTIAL = auto()
    HASH       = auto()
    BTREE      = auto()
    RTREE      = auto()
    # Boolean literals
    TRUE  = auto()
    FALSE = auto()
    # Literals
    IDENTIFIER = auto()
    INT        = auto()
    FLOAT      = auto()
    STRING     = auto()
    # Symbols
    LPAREN    = auto()
    RPAREN    = auto()
    COMMA     = auto()
    SEMICOLON = auto()
    EQUALS    = auto()
    STAR      = auto()
    EOF = auto()


KEYWORDS: dict[str, TokenType] = {
    "create":     TokenType.CREATE,
    "table":      TokenType.TABLE,
    "from":       TokenType.FROM,
    "file":       TokenType.FILE,
    "select":     TokenType.SELECT,
    "insert":     TokenType.INSERT,
    "into":       TokenType.INTO,
    "values":     TokenType.VALUES,
    "delete":     TokenType.DELETE,
    "where":      TokenType.WHERE,
    "between":    TokenType.BETWEEN,
    "and":        TokenType.AND,
    "in":         TokenType.IN,
    "point":      TokenType.POINT,
    "radius":     TokenType.RADIUS,
    "k":          TokenType.K,
    "index":      TokenType.INDEX,
    "int":        TokenType.INT_TYPE,
    "float":      TokenType.FLOAT_TYPE,
    "varchar":    TokenType.VARCHAR_TYPE,
    "bool":       TokenType.BOOL_TYPE,
    "sequential": TokenType.SEQUENTIAL,
    "hash":       TokenType.HASH,
    "btree":      TokenType.BTREE,
    "rtree":      TokenType.RTREE,
    "true":       TokenType.TRUE,
    "false":      TokenType.FALSE,
}


@dataclass
class Token:
    type: TokenType
    value: object  # str | int | float | None
    line: int
    col: int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


class ScannerError(Exception):
    pass


class Scanner:
    def __init__(self, source: str):
        self._src = source
        self._pos = 0
        self._line = 1
        self._col = 1

    def tokenize(self) -> list[Token]:
        tokens = []
        while not self._at_end():
            self._skip_whitespace_and_comments()
            if self._at_end():
                break
            tokens.append(self._next_token())
        tokens.append(Token(TokenType.EOF, None, self._line, self._col))
        return tokens

    def _at_end(self) -> bool:
        return self._pos >= len(self._src)

    def _peek(self, offset: int = 0) -> str:
        idx = self._pos + offset
        return self._src[idx] if idx < len(self._src) else "\0"

    def _advance(self) -> str:
        ch = self._src[self._pos]
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._col = 1
        else:
            self._col += 1
        return ch

    def _skip_whitespace_and_comments(self):
        while not self._at_end():
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "-" and self._peek(1) == "-":
                while not self._at_end() and self._peek() != "\n":
                    self._advance()
            else:
                break

    def _next_token(self) -> Token:
        line, col = self._line, self._col
        ch = self._peek()

        symbols = {
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            ",": TokenType.COMMA,
            ";": TokenType.SEMICOLON,
            "=": TokenType.EQUALS,
            "*": TokenType.STAR,
        }
        if ch in symbols:
            self._advance()
            return Token(symbols[ch], ch, line, col)

        if ch == "'":
            return self._scan_string(line, col)

        # '-' solo inicia número si va seguido de dígito
        if ch.isdigit() or (ch == "-" and self._peek(1).isdigit()):
            return self._scan_number(line, col)

        if ch.isalpha() or ch == "_":
            return self._scan_identifier(line, col)

        raise ScannerError(f"Carácter inesperado {ch!r} en línea {line}, col {col}")

    def _scan_string(self, line: int, col: int) -> Token:
        self._advance()  # abre '
        buf = []
        while not self._at_end() and self._peek() != "'":
            buf.append(self._advance())
        if self._at_end():
            raise ScannerError(f"Cadena sin cerrar, abierta en línea {line}")
        self._advance()  # cierra '
        return Token(TokenType.STRING, "".join(buf), line, col)

    def _scan_number(self, line: int, col: int) -> Token:
        buf = []
        if self._peek() == "-":
            buf.append(self._advance())
        while not self._at_end() and self._peek().isdigit():
            buf.append(self._advance())
        # FLOAT si hay punto seguido de dígito
        if not self._at_end() and self._peek() == "." and self._peek(1).isdigit():
            buf.append(self._advance())
            while not self._at_end() and self._peek().isdigit():
                buf.append(self._advance())
            return Token(TokenType.FLOAT, float("".join(buf)), line, col)
        return Token(TokenType.INT, int("".join(buf)), line, col)

    def _scan_identifier(self, line: int, col: int) -> Token:
        buf = []
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            buf.append(self._advance())
        lexeme = "".join(buf)
        ttype = KEYWORDS.get(lexeme.lower(), TokenType.IDENTIFIER)
        return Token(ttype, lexeme, line, col)
