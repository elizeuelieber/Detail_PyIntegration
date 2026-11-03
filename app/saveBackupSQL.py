from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import mysql.connector

CONFIG_FILE = Path(os.getenv("APP_CONFIG", "/config/config.json"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))


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
        lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
        sql = "\n".join(lines)
        sql = sql.replace("`produtos` text,", "`produtos` longtext,")
        sql = sql.replace("'0000-00-00 00:00:00'", "NULL").replace("'0000-00-00'", "NULL")
        sql = sql.replace(";;", ";")
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        statements = [item.strip() for item in sql.split(";") if item.strip()]
        allowed = ("USE", "DROP", "SET", "CREATE TABLE", "LOCK", "UNLOCK", "INSERT", "DELETE")
        cursor = connection.cursor()
        total = len(statements)
        for index, statement in enumerate(statements, start=1):
            if statement.upper().startswith(allowed):
                cursor.execute(statement)
                # Preserva consistência em caso de falha durante a importação.
                if index % 250 == 0:
                    connection.commit()
                if index % 100 == 0:
                    print(f"Processadas {index}/{total} instruções SQL")
        connection.commit()
        cursor.close()
        log_data.update({
            "data_fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Sucesso",
        })
        print(f"Importação concluída: {total} instruções verificadas")
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
        if connection is not None and connection.is_connected():
            connection.close()
        log_file.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
