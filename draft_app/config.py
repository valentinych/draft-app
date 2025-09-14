import os
import tempfile
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Secret key — ваш постоянный
SECRET_KEY = 'b2484a04b35fa7a0b01293cdd8a75e3cd9742a093577dd731d7ecece56a40c24'

# Пользователи драфтов
# Порядок участников UCL в драфте (snake-очередь строится из этого списка)
UCL_USERS = ["Ксана", "Андрей", "Саша", "Руслан", "Женя", "Макс", "Серёга Б"]
EPL_USERS = ["Ксана", "Саша", "Руслан", "Тёма", "Сергей", "Андрей", "Женя", "Макс"]
TOP4_USERS = ["Ксана", "Саша", "Макс", "Андрей", "Сергей", "Тёма", "Женя", "Руслан"]

# Позиции и лимиты
UCL_POSITION_MAP = {1: 'Goalkeeper', 2: 'Defender', 3: 'Midfielder', 4: 'Forward'}
FPL_POSITION_MAP = {1: 'Goalkeeper', 2: 'Defender', 3: 'Midfielder', 4: 'Forward'}
POSITION_ORDER   = ['Goalkeeper', 'Defender', 'Midfielder', 'Forward']

UCL_POSITION_LIMITS = {'Goalkeeper': 3, 'Defender': 8, 'Midfielder': 9, 'Forward': 5}  # 25
EPL_POSITION_LIMITS = {'Goalkeeper': 3, 'Defender': 7, 'Midfielder': 8, 'Forward': 4}  # 22 (GK=3)
TOP4_POSITION_LIMITS = {'GK': 2, 'DEF': 6, 'MID': 6, 'FWD': 4}  # 18

# Файлы состояния/данных
UCL_STATE_FILE   = os.path.join(BASE_DIR, 'draft_state_ucl.json')
EPL_STATE_FILE   = os.path.join(BASE_DIR, 'draft_state_epl.json')
TOP4_STATE_FILE  = os.path.join(BASE_DIR, 'draft_state_top4.json')
UCL_PLAYERS_FILE = os.path.join(BASE_DIR, 'players_80_en_1.json')
UCL_POINTS_FILE  = os.path.join(BASE_DIR, 'players_70_en_3.json')
# players_fpl_bootstrap.json кешируется вне репозитория
EPL_PLAYERS_FILE = os.path.join(tempfile.gettempdir(), 'players_fpl_bootstrap.json')

# Кэш-дериктории
UCL_CACHE_DIR = os.path.join(BASE_DIR, 'popupstats')
os.makedirs(UCL_CACHE_DIR, exist_ok=True)

# Файлы аутентификации
AUTH_FILE = os.path.join(BASE_DIR, 'auth.json')

# Часовой пояс для EPL дедлайнов
WARSZAWA_TZ = ZoneInfo("Europe/Warsaw")
