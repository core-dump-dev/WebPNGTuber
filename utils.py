import zipfile, os, json, shutil
import logging
import logging.handlers
import time
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

def export_model_zip(model_json, model_dir, zip_path=None):
    """Экспорт модели в ZIP архив"""
    try:
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
        
        # Копируем превью если есть
        preview_src = os.path.join(model_dir, "preview.png")
        preview_dst = os.path.join(export_temp, "preview.png")
        if os.path.exists(preview_src):
            shutil.copy2(preview_src, preview_dst)
        
        # Сохраняем JSON
        json_path = os.path.join(export_temp, "model.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(model_json, f, indent=2, ensure_ascii=False)
        
        # Если zip_path не указан, создаем рядом с моделью
        if zip_path is None:
            base = os.path.basename(model_dir.rstrip("/\\"))
            zip_path = os.path.join(os.path.dirname(model_dir), base + ".zip")
        
        # Создаем ZIP
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(export_temp):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, export_temp)
                    z.write(file_path, arcname=arcname)
        
        # Удаляем временную папку
        shutil.rmtree(export_temp)
        
        logger.info(f"Model exported to: {zip_path}")
        return zip_path
        
    except Exception as e:
        logger.error(f"Error exporting model to ZIP: {e}")
        raise

def import_model_zip(zip_path, target_dir=None):
    """Импорт модели из ZIP архива"""
    try:
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"ZIP файл не найден: {zip_path}")
        
        if target_dir is None:
            # Создаем временную папку для импорта
            import_temp = os.path.join(BASE_DIR, "models", f"import_temp_{int(time.time())}")
            os.makedirs(import_temp, exist_ok=True)
        else:
            import_temp = target_dir
        
        # Распаковываем ZIP
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(import_temp)
        
        # Ищем файл model.json
        json_path = os.path.join(import_temp, "model.json")
        if not os.path.exists(json_path):
            # Ищем во вложенных папках
            for root, dirs, files in os.walk(import_temp):
                if "model.json" in files:
                    json_path = os.path.join(root, "model.json")
                    break
        
        if not os.path.exists(json_path):
            raise FileNotFoundError("Файл model.json не найден в архиве")
        
        # Загружаем модель
        with open(json_path, 'r', encoding='utf-8') as f:
            model_data = json.load(f)
        
        # Проверяем наличие всех файлов изображений
        for layer in model_data.get("layers", []):
            filename = layer.get("file")
            if filename:
                file_path = os.path.join(import_temp, filename)
                if not os.path.exists(file_path):
                    # Ищем в подпапках
                    found = False
                    for root, dirs, files in os.walk(import_temp):
                        if filename in files:
                            # Обновляем путь к файлу в модели
                            rel_path = os.path.relpath(os.path.join(root, filename), import_temp)
                            layer["file"] = rel_path
                            found = True
                            break
                    if not found:
                        logger.warning(f"Файл изображения не найден: {filename}")
        
        logger.info(f"Model imported from ZIP: {zip_path}")
        return model_data, import_temp
        
    except Exception as e:
        logger.error(f"Error importing model from ZIP: {e}")
        raise