from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.getenv("APP_CONFIG", "/config/config.json"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))


def configured_path(value: str, fallback: Path) -> Path:
    path = Path(value) if value else fallback
    if not path.is_absolute():
        path = BASE_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_log(log_dir: Path, started: datetime, status: str) -> None:
    payload = {
        "data_inicio": started.strftime("%Y-%m-%d %H:%M:%S"),
        "data_fim": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status != "iniciado" else "",
        "cliente": "Quallity",
        "sistema": "Crawling",
        "status": status,
    }
    filename = f"{started:%Y%m%d%H%M%S}_Quallity_Crawling.json"
    (log_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {CONFIG_FILE}")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    system = config["amwSystem"]
    username = system["username"]
    password = system["password"]
    if not username or not password:
        raise ValueError("Preencha amwSystem.username e amwSystem.password no config.json")

    downloads = configured_path(system.get("diretorio_download", ""), DATA_DIR / "downloads")
    logs = configured_path(config.get("det2il", {}).get("diretorio_log", ""), DATA_DIR / "logs")
    started = datetime.now()
    write_log(logs, started, "iniciado")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    options.add_experimental_option("prefs", {
        "download.default_directory": str(downloads),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    remote_url = os.getenv("SELENIUM_REMOTE_URL", "http://browser:4444/wd/hub")
    driver = None
    try:
        if remote_url:
            driver = webdriver.Remote(command_executor=remote_url, options=options)
        else:
            driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
        driver.set_page_load_timeout(90)
        try:
            driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(downloads), "eventsEnabled": True,
            })
        except Exception:
            pass  # O diretório também é montado no contêiner do navegador.

        driver.get("https://amwsys.com.br/sistema57/admin_home.php")
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.ID, "formlogin"))).send_keys(username)
        driver.find_element(By.NAME, "formsenha").send_keys(password)
        buttons = driver.find_elements(By.XPATH, "//*[@id='login-box']/div/div/form/fieldset/div[2]/button")
        if not buttons:
            raise RuntimeError("Botão de login não encontrado")
        buttons[0].click()

        try:
            menu = WebDriverWait(driver, 5).until(
                EC.visibility_of_element_located((By.XPATH, "//*[@id='menu-toggler']/span"))
            )
            menu.click()
        except TimeoutException:
            print("Menu lateral não precisou ser expandido.")

        wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='secao_999']/span"))).click()
        wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='menu_sistema']/li[8]/ul/li[1]/a"))).click()
        wait.until(EC.element_to_be_clickable((By.ID, "bnt_gerar_bkp"))).click()

        # Remove o backup anterior antes de solicitar outro, evitando confundi-lo com o download atual.
        for old_file in downloads.glob("backup*.sql"):
            old_file.unlink(missing_ok=True)
        download_link = wait.until(EC.element_to_be_clickable((By.ID, "download_element")))
        download_link.click()

        expected = downloads / f"backup_{datetime.now():%Y%m%d}.sql"
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            if expected.is_file() and not (downloads / (expected.name + ".crdownload")).exists():
                print(f"Download concluído: {expected.name}")
                write_log(logs, started, "Sucesso")
                return
            time.sleep(2)
        raise TimeoutError(f"O arquivo de backup não foi baixado no tempo esperado: {expected.name}")
    except Exception:
        write_log(logs, started, "Falha")
        raise
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
