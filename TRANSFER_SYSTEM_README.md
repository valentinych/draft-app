# 🔄 Transfer System - Система трансферов

Унифицированная система трансферов для всех драфтов (UCL, EPL, TOP4) в Draft App.

## 🎯 Основная функциональность

### ✨ Возможности системы:
- **Трансфер игроков** между командами с сохранением позиционных ограничений
- **GW-базированное отслеживание** - игроки засчитываются только за те GW когда были в команде
- **Transfer Out пул** - отданные игроки доступны для пика другими командами
- **Полная история трансферов** с детальной информацией
- **Автоматическая валидация** трансферов по позициям и ограничениям

## 🏗️ Архитектура

### Основные компоненты:

1. **`TransferSystem`** (`transfer_system.py`) - основной класс для работы с трансферами
2. **Transfer Routes** (`transfer_routes.py`) - Flask маршруты для API
3. **Scoring Helpers** (`scoring_helpers.py`) - интеграция с системой подсчета очков
4. **UI Components** - модальные окна и JavaScript для интерфейса

### Структура данных:

```json
{
  "rosters": {
    "manager_name": [
      {
        "playerId": 123,
        "fullName": "Player Name",
        "clubName": "Club",
        "position": "MID",
        "price": 7.5,
        "status": "active|transfer_out|transfer_in",
        "gws_active": [1, 2, 3, 4, 5],
        "transferred_in_gw": 1,
        "transferred_out_gw": null
      }
    ]
  },
  "transfers": {
    "history": [
      {
        "gw": 3,
        "manager": "Руслан",
        "out_player": {...},
        "in_player": {...},
        "ts": "2025-01-19T12:00:00",
        "draft_type": "UCL"
      }
    ],
    "available_players": [
      {
        "playerId": 456,
        "status": "transfer_out",
        "transferred_out_gw": 3,
        "...": "полная информация об игроке"
      }
    ]
  }
}
```

## 🚀 Использование

### Создание Transfer System:

```python
from draft_app.transfer_system import create_transfer_system

# Создать систему для конкретного драфта
ucl_transfers = create_transfer_system("UCL")
epl_transfers = create_transfer_system("EPL") 
top4_transfers = create_transfer_system("TOP4")
```

### Выполнение трансфера:

```python
state = transfer_system.load_state()

# Выполнить трансфер
updated_state = transfer_system.execute_transfer(
    state=state,
    manager="Руслан",
    out_player_id=123,
    in_player={
        "playerId": 456,
        "fullName": "New Player",
        "clubName": "New Club",
        "position": "MID",
        "price": 8.0
    },
    current_gw=3
)

transfer_system.save_state(updated_state)
```

### Подбор transfer-out игрока:

```python
# Получить доступных игроков
available = transfer_system.get_available_transfer_players(state)

# Подобрать игрока
updated_state = transfer_system.pick_transfer_player(
    state=state,
    manager="Андрей", 
    player_id=789,
    current_gw=4
)
```

## 🎮 API Endpoints

### Трансферы:
- `POST /transfers/<draft_type>/execute` - Выполнить трансфер
- `POST /transfers/<draft_type>/pick-transfer-player` - Подобрать transfer-out игрока
- `GET /transfers/<draft_type>/available-players` - Получить доступных игроков
- `POST /transfers/<draft_type>/validate` - Валидировать трансфер

### Управление:
- `GET /transfers/<draft_type>/history` - История трансферов
- `POST /transfers/<draft_type>/normalize` - Нормализовать игроков (Admin)

## 🖥️ Интерфейс

### Модальные окна:
- **Transfer Modal** - выполнение трансфера с валидацией
- **Transfer Players Modal** - просмотр и выбор доступных игроков

### Интеграция в страницы:

```html
<!-- Подключить модальные окна -->
{% include 'transfer_modal.html' %}

<!-- Установить тип драфта -->
<script>
window.DRAFT_TYPE = '{{ draft_type|upper }}';
</script>

<!-- Кнопка трансфера на карточке игрока -->
<button class="button is-warning" 
        onclick="openTransferModal(this.closest('.player-card'))">
  Трансфер
</button>
```

## 📊 Интеграция с подсчетом очков

### Проверка активности игрока:

```python
from draft_app.scoring_helpers import should_player_score_for_gw

# Проверить, должен ли игрок получать очки за GW
should_score = should_player_score_for_gw("UCL", player_id=123, gw=5)

# Получить менеджера игрока для конкретного GW  
manager = get_player_manager_for_gw("UCL", player_id=123, gw=5)
```

### Фильтрация состава по GW:

```python
from draft_app.scoring_helpers import filter_roster_for_gw

# Получить только активных игроков для конкретного GW
active_roster = filter_roster_for_gw("EPL", full_roster, gw=3)
```

## 🎯 Ключевые особенности

### 1. GW-базированное отслеживание:
- Игроки засчитываются только за GW когда были в команде
- Поле `gws_active` содержит список активных GW для каждого игрока
- Автоматическое обновление при трансферах

### 2. Transfer Out пул:
- Отданные игроки попадают в общий пул
- Доступны для пика любой командой
- Сохраняют историю активности

### 3. Валидация трансферов:
- Проверка позиционных ограничений
- Проверка наличия игрока в составе
- Соответствие лимитам драфта

### 4. Полная история:
- Детальная история всех трансферов
- Фильтрация по менеджерам
- Отслеживание времени операций

## 🛠️ Настройка и развертывание

### 1. Добавить в `__init__.py`:
```python
from .transfer_routes import bp as transfer_bp
app.register_blueprint(transfer_bp)
```

### 2. Обновить навигацию:
```html
<a href="{{ url_for('transfers.transfer_history', draft_type='ucl') }}">
  Трансферы
</a>
```

### 3. Нормализовать существующих игроков:
```
POST /transfers/<draft_type>/normalize
```

## 📈 Расширение функциональности

### Добавление новых правил валидации:
```python
def validate_transfer(self, state, manager, out_player_id, in_player):
    # Базовая валидация
    is_valid, error_msg = super().validate_transfer(...)
    
    # Дополнительные правила
    if custom_rule_check():
        return False, "Custom rule violation"
    
    return is_valid, error_msg
```

### Интеграция с внешними API:
```python
def execute_transfer(self, ...):
    # Выполнить трансфер
    updated_state = super().execute_transfer(...)
    
    # Отправить уведомление
    notify_external_service(transfer_data)
    
    return updated_state
```

## 🔧 Техническое обслуживание

### Проверка целостности данных:
- Регулярная валидация `gws_active` массивов
- Проверка соответствия transfer history и current rosters
- Мониторинг доступных transfer players

### Производительность:
- Кэширование частых запросов к transfer history
- Оптимизация запросов к available players
- Периодическая очистка старых transfer записей

## 🆘 Устранение неполадок

### Частые проблемы:
1. **Игрок не найден в составе** - проверить нормализацию данных
2. **Неверные GW активности** - перенормализовать игроков
3. **Дублирование в transfer pool** - очистить available_players

### Логи и отладка:
- Включить детальное логирование в `transfer_system.py`
- Проверить состояние через `/transfers/<draft_type>/history`
- Использовать admin функции для исправления данных
