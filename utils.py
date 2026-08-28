import zipfile
import os
import json
import shutil
import logging
import logging.handlers
import time
from datetime import datetime
from functools import lru_cache
from PIL import Image
import sys
from locale_loader import tr

# Определение базовой директории
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Фильтр для исключения INFO и DEBUG сообщений


class ErrorOnlyFilter(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.WARNING

# Фильтр для исключения неважных сообщений


class CleanMessageFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if not msg or msg.strip() == "":
            return False
        if "Getting transformed image" in msg:
            return False
        if "Audio processor" in msg and ("started" in msg or "stopped" in msg):
            return False
        if "History saved" in msg:
            return False
        if "Frame render took" in msg:
            return False
        if "cache" in msg.lower():
            return False
        if "Model loaded from slot" in msg:
            return False
        if "Settings saved" in msg or "settings saved" in msg.lower():
            return False
        if "Application started" in msg or "Application closed" in msg:
            return False
        if "Stream connection" in msg or "Index page" in msg or "Web server" in msg:
            return False
        return True

# Централизованная настройка логирования


def setup_logging(name, level=logging.WARNING):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_file = os.path.join(LOGS_DIR, f'{name}.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=1048576,
        backupCount=1,
        encoding='utf-8',
        delay=False
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ErrorOnlyFilter())
    file_handler.addFilter(CleanMessageFilter())
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ErrorOnlyFilter())
    console_handler.addFilter(CleanMessageFilter())
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger

# Очистка старых лог-файлов


def cleanup_old_logs():
    try:
        if not os.path.exists(LOGS_DIR):
            return
        for filename in os.listdir(LOGS_DIR):
            filepath = os.path.join(LOGS_DIR, filename)
            if os.path.isfile(filepath):
                if not (filename.endswith('.log') or filename.endswith('.log.1')):
                    try:
                        os.remove(filepath)
                    except:
                        pass
    except Exception as e:
        print(f"Error cleaning up logs: {e}")


# Инициализируем логгеры для всех модулей при загрузке
logger = setup_logging('utils')
cleanup_old_logs()


@lru_cache(maxsize=32)
def load_image_cached(path, size=None):
    try:
        img = Image.open(path)
        if size:
            img.thumbnail(size, Image.LANCZOS)
        return img
    except Exception as e:
        logger.error(f"Error loading image {path}: {e}")
        return None


def export_model_zip(model_json, model_dir, zip_path=None):
    try:
        export_temp = os.path.join(os.path.dirname(model_dir), "export_temp")
        os.makedirs(export_temp, exist_ok=True)
        for layer in model_json.get("layers", []):
            filename = layer.get("file")
            if filename:
                src = os.path.join(model_dir, filename)
                dst = os.path.join(export_temp, filename)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
        preview_src = os.path.join(model_dir, "preview.png")
        preview_dst = os.path.join(export_temp, "preview.png")
        if os.path.exists(preview_src):
            shutil.copy2(preview_src, preview_dst)
        json_path = os.path.join(export_temp, "model.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(model_json, f, indent=2, ensure_ascii=False)
        if zip_path is None:
            base = os.path.basename(model_dir.rstrip("/\\"))
            zip_path = os.path.join(os.path.dirname(model_dir), base + ".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(export_temp):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, export_temp)
                    z.write(file_path, arcname=arcname)
        shutil.rmtree(export_temp)
        logger.info(tr('exported_zip', path=zip_path))
        return zip_path
    except Exception as e:
        logger.error(tr('zip_export_error', error=e))
        raise


def import_model_zip(zip_path, target_dir=None):
    try:
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"ZIP файл не найден: {zip_path}")
        if target_dir is None:
            import_temp = os.path.join(
                BASE_DIR, "models", f"import_temp_{int(time.time())}")
            os.makedirs(import_temp, exist_ok=True)
        else:
            import_temp = target_dir
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(import_temp)
        json_path = os.path.join(import_temp, "model.json")
        if not os.path.exists(json_path):
            for root, dirs, files in os.walk(import_temp):
                if "model.json" in files:
                    json_path = os.path.join(root, "model.json")
                    break
        if not os.path.exists(json_path):
            raise FileNotFoundError("Файл model.json не найден в архиве")
        with open(json_path, 'r', encoding='utf-8') as f:
            model_data = json.load(f)
        for layer in model_data.get("layers", []):
            filename = layer.get("file")
            if filename:
                file_path = os.path.join(import_temp, filename)
                if not os.path.exists(file_path):
                    found = False
                    for root, dirs, files in os.walk(import_temp):
                        if filename in files:
                            rel_path = os.path.relpath(
                                os.path.join(root, filename), import_temp)
                            layer["file"] = rel_path
                            found = True
                            break
                    if not found:
                        logger.warning(
                            f"Файл изображения не найден: {filename}")
        logger.info(tr('imported_zip', path=zip_path))
        return model_data, import_temp
    except Exception as e:
        logger.error(tr('zip_import_error', error=e))
        raise
