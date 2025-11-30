import zipfile, os, json, shutil
import logging
import logging.handlers
from datetime import datetime

# Определение базовой директории
import sys
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Создание папки для логов
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Настройка логирования для utils
def setup_utils_logging():
    logger = logging.getLogger('utils')
    logger.setLevel(logging.DEBUG)
    
    # Форматирование
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Файловый обработчик с ротацией
    log_file = os.path.join(LOGS_DIR, 'utils.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5  # 1MB
    )
    file_handler.setFormatter(formatter)
    
    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# Инициализация логгера
logger = setup_utils_logging()

def export_model_zip(model_json, model_dir):
    # Создаем временную папку для экспорта
    export_temp = os.path.join(os.path.dirname(model_dir), "export_temp")
    os.makedirs(export_temp, exist_ok=True)
    
    # Копируем все файлы модели
    for layer in model_json.get("layers", []):
        filename = layer.get("file")
        if filename:
            src = os.path.join(model_dir, filename)
            dst = os.path.join(export_temp, filename)
            if os.path.exists(src):
                shutil.copy2(src, dst)
    
    # Копируем превью
    preview_src = os.path.join(model_dir, "preview.png")
    preview_dst = os.path.join(export_temp, "preview.png")
    if os.path.exists(preview_src):
        shutil.copy2(preview_src, preview_dst)
    
    # Сохраняем JSON
    json_path = os.path.join(export_temp, "model.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(model_json, f, indent=2, ensure_ascii=False)
    
    # Создаем ZIP
    base = os.path.basename(model_dir.rstrip("/\\"))
    zippath = os.path.join(os.path.dirname(model_dir), base + ".zip")
    
    with zipfile.ZipFile(zippath, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(export_temp):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, export_temp)
                z.write(file_path, arcname=arcname)
    
    # Удаляем временную папку
    shutil.rmtree(export_temp)
    
    logger.info(f"Model exported to: {zippath}")
    return zippath