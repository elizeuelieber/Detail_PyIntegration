from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator

import mysql.connector

CONFIG_FILE = Path(os.getenv("APP_CONFIG", "/config/config.json"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))


def split_sql_statements(sql: str) -> Iterator[str]:
    """Split a MySQL dump using its active DELIMITER and SQL lexical rules.

    Semicolons inside strings, quoted identifiers, comments, and stored-program
    bodies are not treated as statement boundaries. DELIMITER directives are
    client directives and are consumed rather than sent to MySQL.
    """
    delimiter = ";"
    parts: list[str] = []
    i = 0
    quote: str | None = None
    has_content = False
    length = len(sql)

    while i < length:
        char = sql[i]

        if quote is not None:
            parts.append(char)
            if char == "\\" and quote != "`" and i + 1 < length:
                i += 1
                parts.append(sql[i])
            elif char == quote:
                if i + 1 < length and sql[i + 1] == quote:
                    i += 1
                    parts.append(sql[i])
                else:
                    quote = None
            i += 1
            continue

        # Consume MySQL client directives only between statements. Track
        # whether the current buffer has any SQL instead of rescanning it on
        # every character (important for large multi-megabyte dumps).
        if not has_content:
            line_end = sql.find("\n", i)
            if line_end < 0:
                line_end = length
            line = sql[i:line_end]
            directive = re.fullmatch(r"\s*DELIMITER\s+(\S+)\s*\r?", line, re.IGNORECASE)
            if directive:
                delimiter = directive.group(1)
                parts.clear()
                i = min(line_end + 1, length)
                continue
            if line.strip():
                has_content = True

        # Skip whitespace/comments before probing for client-side directives.
        # Comments before DELIMITER directives are common in mysqldump output.
        if not has_content and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = length if end < 0 else end + 1
            continue
        if not has_content and char == "#":
            end = sql.find("\n", i)
            i = length if end < 0 else end + 1
            continue
        if not has_content and char.isspace():
            parts.append(char)
            i += 1
            continue
        if not has_content and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError("Comentário SQL de bloco não foi fechado")
            if sql.startswith("/*!", i):
                has_content = True
            else:
                parts.append(" ")
                i = end + 2
                continue

        # Start of a real SQL statement after whitespace/comments.
        if not has_content and not char.isspace():
            has_content = True

        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError("Comentário SQL de bloco não foi fechado")
            comment = sql[i : end + 2]
            # MySQL executable/version comments (/*!...*/) must reach MySQL;
            # ordinary comments are discarded so they cannot split commands.
            if sql.startswith("/*!", i):
                parts.append(comment)
            else:
                parts.append(" ")
            i = end + 2
            continue

        if char == "#" or (
            sql.startswith("--", i)
            and (i + 2 == length or sql[i + 2].isspace())
        ):
            end = sql.find("\n", i)
            if end < 0:
                i = length
            else:
                parts.append("\n")
                i = end + 1
            continue

        if char in ("'", '"', "`"):
            quote = char
            parts.append(char)
            i += 1
            continue

        if delimiter and sql.startswith(delimiter, i):
            statement = "".join(parts).strip()
            if statement:
                yield statement
            parts.clear()
            has_content = False
            i += len(delimiter)
            continue

        parts.append(char)
        i += 1

    if quote is not None:
        raise ValueError(f"String ou identificador SQL sem fechamento ({quote})")

    final_statement = "".join(parts).strip()
    if final_statement:
        # A dangling DELIMITER directive is never valid SQL, and is not sent.
        if re.match(r"^DELIMITER\b", final_statement, re.IGNORECASE):
            raise ValueError("Diretiva DELIMITER sem terminador ou instrução seguinte")
        yield final_statement


def configured_path(value: str, fallback: Path) -> Path:
    path = Path(value) if value else fallback
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {CONFIG_FILE}")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    started = datetime.now()
    log_dir = configured_path(config.get("det2il", {}).get("diretorio_log", ""), DATA_DIR / "logs")
    backup_dir = configured_path(config.get("amwSystem", {}).get("diretorio_download", ""), DATA_DIR / "downloads")
    backup = backup_dir / f"backup_{started:%Y%m%d}.sql"
    if not backup.is_file():
        raise FileNotFoundError(f"Backup SQL não encontrado: {backup}")

    log_file = log_dir / f"{started:%Y%m%d%H%M%S}_Quallity_BackupSQL.json"
    log_data = {
        "data_inicio": started.strftime("%Y-%m-%d %H:%M:%S"),
        "data_fim": "",
        "cliente": "Quallity",
        "sistema": "BackupSQL",
        "status": "iniciado",
    }
    log_file.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")

    connection = None
    cursor = None
    try:
        mysql_config = config["mysql"]
        connection = mysql.connector.connect(
            host=mysql_config["host"],
            user=mysql_config["user"],
            password=mysql_config["password"],
            database=mysql_config["database"],
            port=int(mysql_config.get("port", 3306)),
            connection_timeout=30,
            autocommit=False,
        )
        sql = backup.read_text(encoding="utf-8-sig")


        # Remove a cláusula DEFINER, inclusive quando aparece em comentários
        # executáveis do MySQL, como /*!50013 DEFINER=... */.
        sql = re.sub(
            r"\bDEFINER\s*=\s*(?:`(?:``|[^`])*`|[^\s@]+)\s*@\s*(?:`(?:``|[^`])*`|[^\s*/;]+)",
            "",
            sql,
            flags=re.IGNORECASE,
        )

        cursor = connection.cursor()
        processed = 0
        for processed, statement in enumerate(split_sql_statements(sql), start=1):
            try:
                # Execute all dump statements, including CREATE PROCEDURE/VIEW,
                # ALTER TABLE, and MySQL executable comments.
                cursor.execute(statement)
            except Exception as exc:
                raise RuntimeError(
                    f"Falha ao executar a instrução SQL nº {processed}: {exc}"
                ) from exc
            # Bound transaction size for large data dumps. DDL can commit
            # implicitly in MySQL, so a failed import may still be partial.
            if processed % 250 == 0:
                connection.commit()
            if processed % 100 == 0:
                print(f"Processadas {processed} instruções SQL")

        connection.commit()
        log_data.update({
            "data_fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Sucesso",
        })
        print(f"Importação concluída: {processed} instruções executadas")
    except Exception as exc:
        if connection is not None:
            connection.rollback()
        error_file = log_dir / f"erro_{started:%Y%m%d%H%M%S}_BackupSQL.txt"
        error_file.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        log_data.update({
            "data_fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Falha",
        })
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
        log_file.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
