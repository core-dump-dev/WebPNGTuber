import json
import os
import sys

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

LOCALES_DIR = os.path.join(get_base_dir(), 'locales')
DEFAULT_LANG = 'ru'  # или 'en'

class I18n:
    def __init__(self, lang=DEFAULT_LANG):
        self.lang = lang
        self.translations = {}
        self.load_lang(lang)

    def load_lang(self, lang):
        path = os.path.join(LOCALES_DIR, f'{lang}.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.translations = json.load(f)
                self.lang = lang
        except Exception as e:
            print(f'Failed to load language {lang}: {e}')
            self.translations = {}

    def tr(self, key, **kwargs):
        text = self.translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    def set_lang(self, lang):
        self.load_lang(lang)

# Глобальный экземпляр
i18n = I18n()
tr = i18n.tr