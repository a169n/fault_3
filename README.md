# Assignment 3: устойчивость облачных микросервисов (AITU, синтетические данные)

Проект для Assignment 3 «Cloud Computing and Microservices Resilience»: синтетическая платформа студенческих сервисов AITU.
Все данные, адреса и пароли учебные и выдуманные.

## Что где лежит

| Часть задания | Файлы |
|---|---|
| **Отчёт по шаблону** | `report/REPORT.md` и `report/REPORT.docx` (поля **[FILL IN]** заполнить самому) |
| Part A: анализ архитектуры, диаграмма | `docs/architecture.png` / `.svg`, таблица в отчёте (раздел 3.1) |
| Part B: расчёт доступности | `scripts/availability.py` → `results/availability.md`, блок-схема `docs/rbd.png` |
| Part C: Kubernetes YAML | `k8s/11-registration-deployment.yaml`, `k8s/12-registration-service.yaml`, `k8s/13-registration-pdb.yaml` |
| Part D: симуляция отказов | `scripts/simulate_failures.py` → `results/simulation_*`; для настоящего кластера `scripts/k8s/run_experiments.py` |
| Part E: RPO/RTO и DR | `docs/dr-plan.md`, `k8s/20-db-backup-cronjob.yaml`, `scripts/dr_drill.sh` → `results/dr_drill.log` |
| Подготовка к защите | `docs/defense_notes_ru.md` |
| Сам сервис | `app/registration_service.py` (Python, только стандартная библиотека), `app/Dockerfile` |

```
app/        сервис регистрации и Dockerfile
k8s/        манифесты: namespace, secret, postgres, configmap, deployment, service, PDB, backup CronJob
k8s/tools/  load-generator (нагрузка для экспериментов на кластере)
scripts/    расчёты, симулятор, DR-учения, сборка DOCX, скрипты для kind
docs/       диаграммы, план DR, шпаргалка к защите
results/    полученные результаты и логи
report/     отчёт
```

## Главные результаты

* **Part B.** End-to-end доступность **99.8417%**, цель 99.9% **не достигается**. Узкое место — единственный API Gateway (99.90%). Если поставить 2 гейтвея, получается **99.9415%**, а с 2 LB и 2 гейтвеями — 99.9915%.
* **Part D (симуляция).**

  | Эксперимент | Обнаружение | Восстановление | Влияние на пользователей |
  |---|---|---|---|
  | 1 реплика упала | 0.92 с | 2.99 с | 9 из 120 запросов с ошибкой |
  | обе реплики упали | 0.95 с | 3.0 с до первой реплики, 13 с до обеих | 60 запросов с ошибкой |
  | БД недоступна 30 с | 9.04 с | через 4.2 с после возврата БД | 503 всё время простоя, 0 рестартов |

* **Part E (DR-учения).** После `DROP TABLE` восстановление из `pg_dump` вернуло 1000 из 1250 строк, а восстановление на момент времени (PITR) по архиву WAL — 1250 из 1250.

## Как запустить на macOS

Нужен Python 3. На macOS он ставится с Xcode Command Line Tools или через `brew install python`.

### 1. Расчёт доступности (Part B)

```bash
python3 scripts/availability.py
```

### 2. Симуляция отказов без Kubernetes (Part D), около 2 минут

```bash
python3 scripts/simulate_failures.py            # все 4 эксперимента
python3 scripts/simulate_failures.py --only 1 2 # только выбранные
```

Результаты появятся в `results/simulation_results.md`. Чтобы параметры probe читались прямо из YAML, нужен PyYAML (`pip3 install pyyaml`). Без него симулятор возьмёт те же значения по умолчанию.
**Запустите сами:** для раздела «Individual work» преподавателю нужен ваш собственный прогон.

### 3. Настоящий Kubernetes на Mac (kind)

```bash
brew install kind kubectl          # плюс Docker Desktop (или colima: brew install colima docker && colima start)
bash scripts/k8s/setup_kind.sh     # кластер из 3 нод, сборка образа, деплой
kubectl -n aitu-sss get pods -o wide
python3 scripts/k8s/run_experiments.py --db-outage 60   # эксперименты 1–3 на кластере
bash scripts/k8s/teardown_kind.sh  # удалить кластер
```

Результаты сохранятся в `results/k8s/<время>/`: таблица, лог нагрузки, события и timeline.
Для скриншотов в отчёт откройте второй терминал с `kubectl -n aitu-sss get pods -w` во время экспериментов.

Если удобнее minikube: `minikube start --nodes 2`, затем `minikube image build -t aitu-demo/registration-service:1.0.0 app/`, `kubectl apply -f k8s/` и дальше тот же `run_experiments.py`.

Проверить вручную, что Service работает:

```bash
kubectl -n aitu-sss run tmp --rm -it --image=curlimages/curl:8.10.1 --restart=Never -- \
  curl -s -XPOST -H 'Content-Type: application/json' \
  -d '{"student_id":"S0001","course_code":"CC-301"}' http://registration-service/api/v1/registrations
```

Демонстрация liveness (зависший под перезапускается):

```bash
POD=$(kubectl -n aitu-sss get pod -l app=registration-service -o name | head -1)
kubectl -n aitu-sss exec $POD -- python -c "import urllib.request as u; u.urlopen(u.Request('http://localhost:8080/chaos/hang', method='POST'))"
kubectl -n aitu-sss get pods -w     # через ~30 с RESTARTS станет 1
```

### 4. DR-учения (Part E), нужен локальный PostgreSQL

```bash
brew install postgresql@16
export PGBIN="$(brew --prefix postgresql@16)/bin"
bash scripts/dr_drill.sh           # лог в results/dr_drill.log
```

### 5. Проверка YAML и сборка отчёта

```bash
brew install kubeconform pandoc && pip3 install python-docx
kubeconform -strict -summary k8s/*.yaml k8s/tools/*.yaml
python3 scripts/build_report_docx.py     # report/REPORT.md -> report/REPORT.docx
```

## Как получены результаты в `results/`

* Kubernetes нельзя было запустить в облачной песочнице, где готовился проект: там не работают вложенные контейнеры. Поэтому Part D прогнан на **симуляторе** `scripts/simulate_failures.py`. Он запускает настоящий код сервиса двумя процессами и эмулирует kubelet: probe с параметрами из YAML, рестарты, CrashLoopBackOff. Также эмулируются балансировка Service по готовым подам и отказ БД. Это честно указано в отчёте как ограничение.
* YAML проверены через `kubeconform` по схемам Kubernetes 1.31: 11 ресурсов, 0 ошибок.
* Работа сервиса с настоящим PostgreSQL 16 проверена отдельно (`results/postgres_backend_check.log`). При падении БД readiness отдаёт 503, а liveness остаётся 200. После возврата БД соединение восстанавливается само.
* DR-учения проведены на настоящем PostgreSQL 16 (`results/dr_drill.log`).

## Что сделать самому перед сдачей

1. Заполнить все **[FILL IN]** в `report/REPORT.md`: ФИО, ID, группа, вариант, даты, коммит. Потом пересобрать DOCX или сразу править `report/REPORT.docx`.
2. Прогнать симулятор и, если получится, kind на своём Mac. Приложить свои логи и скриншоты.
3. Переписать «problem I encountered» и «unique decision» своими словами.
4. Сделать коммит и вписать его хэш в отчёт.
5. Прочитать `docs/defense_notes_ru.md`.
