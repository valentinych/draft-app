# Список трансферов

*Список составлен на основе анализа составов из AWS S3 (https://val-draft-storage.s3.us-east-1.amazonaws.com/lineups/)*

## ТРАНСФЕРЫ ПОСЛЕ GW3

### Андрей
1. **Sandro Tonali** (MID, ID: 491) → **Dango Ouattara** (MID, ID: 83)
2. **Evann Guessand** (MID, ID: 677) → **Harvey Elliott** (MID, ID: 389)

### Женя
1. **Fábio Soares Silva** (FWD, ID: 655) → **Randal Kolo Muani** (FWD, ID: 726)

### Ксана
1. **Jhon Arias** (MID, ID: 663) → **Kiernan Dewsbury-Hall** (MID, ID: 242)

### Макс
1. **Georginio Rutter** (MID, ID: 158) → **Xavi Simons** (MID, ID: 717)
2. **Aaron Wan-Bissaka** (DEF, ID: 610) → **Cristian Romero** (DEF, ID: 569)

### Руслан
1. **Jamie Bynoe-Gittens** (MID, ID: 239) → **Callum Hudson-Odoi** (MID, ID: 516)
2. **Jorrel Hato** (DEF, ID: 672) → **Kieran Trippier** (DEF, ID: 478)

### Саша
1. **Igor Jesus Maciel da Cruz** (FWD, ID: 526) → **Nick Woltemade** (FWD, ID: 714)
2. **Nayef Aguerd** (DEF, ID: 607) → **Chris Richards** (DEF, ID: 261)

### Сергей
1. **Leandro Trossard** (MID, ID: 20) → **Jaidon Anthony** (MID, ID: 200)
2. **Ian Maatsen** (DEF, ID: 39) → **Bafodé Diakité** (DEF, ID: 685)

### Тёма
1. **Nicolas Jackson** (FWD, ID: 251) → **Eliezer Mayenda Dossou** (FWD, ID: 561)
2. **Ederson Santana de Moraes** (GK, ID: 399) → **Gianluigi Donnarumma** (GK, ID: 736)
3. **Kevin Schade** (MID, ID: 120) → **Marcus Tavernier** (MID, ID: 84)

**ИТОГО GW3: 13 трансферов**

**Примечание:** Информация об игроках получена из FPL API (https://fantasy.premierleague.com/api/bootstrap-static/). Трансферы для Сергея и Тёмы были восстановлены на основе предоставленной информации.

---

## ТРАНСФЕРЫ ПОСЛЕ GW10

### Андрей
1. **Ola Aina** (DEF, ID: 507) → **Nico O'Reilly** (DEF, ID: 411)

### Женя
1. **Youri Tielemans** (MID, ID: 48) → **Josh Cullen** (MID, ID: 205)

### Ксана
1. **Dan Ndoye** (MID, ID: 669) → **Granit Xhaka** (MID, ID: 668)

### Макс
1. **Benjamin White** (DEF, ID: 11) → **Matty Cash** (DEF, ID: 36)

### Руслан
1. **Chris Wood** (FWD, ID: 525) → **Junior Kroupi** (FWD, ID: 100)

### Саша
1. **Daniel James** (MID, ID: 353) → **Leandro Trossard** (MID, ID: 20)

### Сергей
1. **Dejan Kulusevski** (MID, ID: 583) → **Palhinha** (MID, ID: 673)

### Тёма
1. **Armando Broja** (FWD, ID: 680) → **Lukas Nmecha** (FWD, ID: 365)

**ИТОГО GW10: 8 трансферов**

---

**ОБЩИЙ ИТОГ: 21 трансфер (13 после GW3 + 8 после GW10)**

---

## Источники данных

1. **State файл** (`draft_state_epl.json`) - содержит записанные трансферы в `transfer.history` (используется как основной источник)
2. **FPL API** (https://fantasy.premierleague.com/api/bootstrap-static/) - используется для получения информации об игроках по ID
3. **S3 Lineups** (https://val-draft-storage.s3.us-east-1.amazonaws.com/lineups/) - использовались для проверки и поиска недостающих трансферов


