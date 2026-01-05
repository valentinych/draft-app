#!/bin/bash
# Последовательность команд для деплоя маппинга Top-4 игроков
# Выполняйте команды по порядку

echo "=========================================="
echo "ШАГ 1: Маппинг всех игроков"
echo "=========================================="
heroku run --app val-draft-app "python3 scripts/map_all_top4_players.py"

echo ""
echo "=========================================="
echo "ШАГ 2: Обновление очков для всех завершенных туров"
echo "=========================================="
heroku run --app val-draft-app "python3 scripts/refresh_top4_scores_all_finished.py"

echo ""
echo "=========================================="
echo "ШАГ 3: Проверка переменных окружения"
echo "=========================================="
heroku config:get TOP4_USE_API_FOOTBALL --app val-draft-app
heroku config:get API_FOOTBALL_KEY --app val-draft-app

echo ""
echo "=========================================="
echo "ШАГ 4: Установка переменных (если нужно)"
echo "=========================================="
echo "Если TOP4_USE_API_FOOTBALL не установлен или не равен true, выполните:"
echo "heroku config:set TOP4_USE_API_FOOTBALL=true --app val-draft-app"
echo ""
echo "Если API_FOOTBALL_KEY не установлен, выполните:"
echo "heroku config:set API_FOOTBALL_KEY=<ваш_api_ключ> --app val-draft-app"

echo ""
echo "=========================================="
echo "ШАГ 5: Перезапуск приложения"
echo "=========================================="
heroku restart --app val-draft-app

echo ""
echo "=========================================="
echo "ГОТОВО!"
echo "=========================================="
echo "Проверьте работу на страницах:"
echo "1. Лайнапы: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/lineups?round=6"
echo "2. Результаты: https://val-draft-app-b4a5eee9bd9a.herokuapp.com/top4/results"

