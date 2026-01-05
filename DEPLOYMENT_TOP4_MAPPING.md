# Порядок команд для деплоя маппинга Top-4 игроков

## Шаг 1: Маппинг всех игроков

Выполнить маппинг всех игроков из четырех лиг (EPL, La Liga, Serie A, Bundesliga):

```bash
heroku run --app val-draft-app "python3 scripts/map_all_top4_players.py"
```

Этот скрипт:
- Загружает ВСЕХ игроков из API Football для всех 4 лиг
- Загружает ВСЕХ игроков из Top-4 draft системы
- Выполняет маппинг для ВСЕХ игроков (не только задрафтованных)
- Проверяет результаты маппинга
- Сохраняет обновленный маппинг

**Время выполнения:** 5-10 минут (зависит от количества игроков)

## Шаг 2: Проверка результатов маппинга

Результаты выводятся в консоль. Проверьте:
- Покрытие API Football игроков (должно быть > 80%)
- Покрытие Top-4 draft игроков (должно быть > 90%)
- Количество дубликатов (должно быть минимальным)

## Шаг 3: Обновление очков для всех завершенных туров

После маппинга обновите очки для всех завершенных туров:

```bash
heroku run --app val-draft-app "python3 scripts/refresh_top4_scores_all_finished.py"
```

Этот скрипт:
- Определяет все завершенные туры/матчи
- Обновляет статистику всех игроков
- Очищает кеш лайнапов для всех завершенных раундов

**Время выполнения:** 10-20 минут (зависит от количества игроков и туров)

## Шаг 4: Проверка переменных окружения

Проверьте, что переменная окружения установлена:

```bash
heroku config:get TOP4_USE_API_FOOTBALL --app val-draft-app
```

Должно быть: `true`

## Шаг 5: Установка переменной окружения (если не установлена)

Если переменная не установлена или установлена в `false`:

```bash
heroku config:set TOP4_USE_API_FOOTBALL=true --app val-draft-app
```

## Шаг 6: Проверка API ключа

Убедитесь, что API ключ установлен:

```bash
heroku config:get API_FOOTBALL_KEY --app val-draft-app
```

Должен быть установлен API ключ (проверьте в настройках проекта).

Если не установлен, установите его:

```bash
heroku config:set API_FOOTBALL_KEY=<ваш_api_ключ> --app val-draft-app
```

## Шаг 7: Перезапуск приложения

Перезапустите приложение для применения изменений:

```bash
heroku restart --app val-draft-app
```

## Шаг 8: Проверка работы

Откройте страницы и проверьте, что очки отображаются:

1. **Лайнапы:** https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/lineups?round=6
2. **Результаты:** https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/results

## Полный порядок команд (копировать и выполнять по порядку):

```bash
# 1. Маппинг всех игроков
heroku run --app val-draft-app "python3 scripts/map_all_top4_players.py"

# 2. Обновление очков
heroku run --app val-draft-app "python3 scripts/refresh_top4_scores_all_finished.py"

# 3. Проверка переменных окружения
heroku config:get TOP4_USE_API_FOOTBALL --app val-draft-app
heroku config:get API_FOOTBALL_KEY --app val-draft-app

# 4. Установка переменных (если нужно)
heroku config:set TOP4_USE_API_FOOTBALL=true --app val-draft-app
heroku config:set API_FOOTBALL_KEY=<ваш_api_ключ> --app val-draft-app

# 5. Перезапуск
heroku restart --app val-draft-app
```

## Проверка логов

Если что-то не работает, проверьте логи:

```bash
heroku logs --tail --app val-draft-app
```

Ищите ошибки, связанные с:
- `[API_FOOTBALL]` - ошибки API Football
- `[MAP]` - ошибки маппинга
- `[TOP4]` - ошибки Top-4 системы

## Устранение проблем

### Проблема: Все еще нули на странице лайнапов

1. Проверьте логи на наличие ошибок
2. Убедитесь, что маппинг выполнен успешно
3. Убедитесь, что переменная `TOP4_USE_API_FOOTBALL=true`
4. Попробуйте очистить кеш вручную (удалить файлы в `data/cache/lineups/`)

### Проблема: Маппинг не находит игроков

1. Проверьте, что API Football возвращает данные
2. Проверьте формат данных в `data/cache/top4_players.json`
3. Проверьте логи на наличие ошибок при загрузке игроков

### Проблема: Ошибки API Football

1. Проверьте, что API ключ правильный
2. Проверьте лимиты API (возможно, превышен лимит запросов)
3. Подождите несколько минут и попробуйте снова

