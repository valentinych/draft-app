#!/bin/bash

# Скрипт для коммита изменений, push в master и деплоя на Heroku

set -e  # Остановка при ошибке

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🚀 Начинаем деплой...${NC}"

# Проверяем, есть ли изменения для коммита
if [ -z "$(git status --porcelain)" ]; then
    echo -e "${YELLOW}⚠️  Нет изменений для коммита${NC}"
else
    # Показываем статус
    echo -e "${GREEN}📋 Статус изменений:${NC}"
    git status --short
    
    # Запрашиваем сообщение коммита
    if [ -z "$1" ]; then
        echo -e "${YELLOW}💬 Введите сообщение коммита:${NC}"
        read -r commit_message
    else
        commit_message="$1"
    fi
    
    # Коммитим изменения
    echo -e "${GREEN}💾 Коммитим изменения...${NC}"
    git add .
    git commit -m "$commit_message"
    echo -e "${GREEN}✅ Изменения закоммичены${NC}"
fi

# Определяем текущую ветку
CURRENT_BRANCH=$(git branch --show-current)
if [ -z "$CURRENT_BRANCH" ]; then
    CURRENT_BRANCH="main"
fi

# Пушим в текущую ветку
echo -e "${GREEN}📤 Пушим в ${CURRENT_BRANCH}...${NC}"
git push origin "$CURRENT_BRANCH"
echo -e "${GREEN}✅ Изменения запушены в ${CURRENT_BRANCH}${NC}"

# Пушим в Heroku
echo -e "${GREEN}🌐 Деплоим на Heroku...${NC}"
git push heroku main
echo -e "${GREEN}✅ Деплой на Heroku завершен!${NC}"

echo -e "${GREEN}🎉 Все готово!${NC}"

