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
    logger.setLevel(logging.INFO)  # Уменьшили уровень логирования
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    log_file = os.path.join(LOGS_DIR, 'utils.log')
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1048576, backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_utils_logging()

def export_model_zip(model_json, model_dir, zip_path=None):
    """Оптимизированный экспорт модели в ZIP архив"""
    try:
        # Создаем временную папку
        export_temp = os.path.join(os.path.dirname(model_dir), "export_temp")
        os.makedirs(export_temp, exist_ok=True)
        
        # Копируем только используемые файлы
        file_set = set()
        for layer in model_json.get("layers", []):
            filename = layer.get("file")
            if filename:
                file_set.add(filename)
        
        # Копируем файлы
        for filename in file_set:
            src = os.path.join(model_dir, filename)
            dst = os.path.join(export_temp, filename)
            if os.path.exists(src):
                shutil.copy2(src, dst)
        
        # Копируем превью если есть
        preview_src = os.path.join(model_dir, "preview.png")
        preview_dst = os.path.join(export_temp, "preview.png")
        if os.path.exists(preview_src):
            shutil.copy2(preview_src, preview_dst)
        
        # Сохраняем JSON с оптимизацией
        json_path = os.path.join(export_temp, "model.json")
        with open(json_path, "w", encoding="utf-8") as f:
            # Уменьшаем размер JSON
            json.dump(model_json, f, separators=(',', ':'), ensure_ascii=False)
        
        # Создаем ZIP
        if zip_path is None:
            base = os.path.basename(model_dir.rstrip("/\\"))
            zip_path = os.path.join(os.path.dirname(model_dir), base + ".zip")
        
        # Используем более высокую степень сжатия
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
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
    """Оптимизированный импорт модели из ZIP архива"""
    try:
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"ZIP файл не найден: {zip_path}")
        
        if target_dir is None:
            import_temp = os.path.join(BASE_DIR, "models", f"import_temp_{int(time.time())}")
            os.makedirs(import_temp, exist_ok=True)
        else:
            import_temp = target_dir
        
        # Распаковываем с оптимизацией
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Сначала извлекаем только model.json
            model_json_member = None
            for member in zip_ref.namelist():
                if member.endswith('model.json') or member == 'model.json':
                    model_json_member = member
                    break
            
            if not model_json_member:
                raise FileNotFoundError("Файл model.json не найден в архиве")
            
            # Извлекаем model.json
            zip_ref.extract(model_json_member, import_temp)
            json_path = os.path.join(import_temp, model_json_member)
            
            # Загружаем модель
            with open(json_path, 'r', encoding='utf-8') as f:
                model_data = json.load(f)
            
            # Извлекаем только необходимые файлы
            file_set = set()
            for layer in model_data.get("layers", []):
                filename = layer.get("file")
                if filename:
                    file_set.add(filename)
            
            # Извлекаем файлы
            for member in zip_ref.namelist():
                filename = os.path.basename(member)
                if filename in file_set or filename == "preview.png":
                    zip_ref.extract(member, import_temp)
        
        logger.info(f"Model imported from ZIP: {zip_path}")
        return model_data, import_temp
        
    except Exception as e:
        logger.error(f"Error importing model from ZIP: {e}")
        raise