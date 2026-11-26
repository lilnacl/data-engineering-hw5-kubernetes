# ДЗ5. Kubernetes

Вдовина Анастасия Романовна. Вариант 7: «Программист, разработчик на Go».

[Отчёт — ИнжДан_lab5_Вдовина А.Р.pdf](<ИнжДан_lab5_Вдовина А.Р.pdf>)

Расчёт пар навыков из ДЗ3 в Kubernetes. Хранилище — RustFS 1.0.1 с EC 2+2,
как в ДЗ2. Четыре работника обрабатывают части CSV, затем reducer объединяет
результаты. Все 13 552 частоты пар и итоговый топ-10 совпали с ДЗ3:
3 123 вакансии, 61 694 вхождения пар.

## Файлы

| Папка | Содержание |
|---|---|
| `hw3/`, `baseline/` | Расчёт и эталон из ДЗ3 |
| `hw5/` | Работник, reducer, чтение и запись S3 |
| `manifests/` | Кластер, RustFS, Jobs, приоритеты и квота |
| `scripts/` | Запуск опытов и проверки |
| `evidence/` | Журналы, статусы Pod/Job, замеры и события |
| `results/` | Все частоты пар и топ-10 |

Скриншоты включены в PDF-отчёт. Отдельные изображения в репозитории не хранятся.

## Запуск

Нужны работающий Docker, Python 3.10+, kind 0.27.0 и kubectl 1.32.2.
Для Docker — минимум 4 CPU, 6 GiB памяти и 10 GiB свободного места.

Полный [it_vacancies_2024.csv](https://drive.google.com/file/d/1rLEu3N2isIBhAkK1EcSJ2482ouOPAots/view)
из ДЗ2/ДЗ3: 847 619 384 байта. SHA-256:

```text
303db922b541e4440906f5a7e8be32fb55e8808f950ffddb8c272f1548212a65
```

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/run.py /absolute/path/it_vacancies_2024.csv
```

Скрипт создаёт отдельный кластер `de-hw5`; kubeconfig и ключи S3 сохраняются
в `work/` и не попадают в Git. Для загрузки используется локальный порт 19000.
После опытов кластер остаётся доступен для просмотра:

```bash
export KUBECONFIG="$PWD/work/kubeconfig"
kubectl get nodes
kubectl get jobs,pods,pvc -n hw5
kubectl get resourcequota -n hw5-quota
kubectl get events -n hw5 --sort-by=.metadata.creationTimestamp
```

Для повторного полного прогона сначала удалить учебный кластер:

```bash
kind delete cluster --name de-hw5
```

Удаляются также его PVC; исходный CSV на компьютере не изменяется.
Все тома находятся на одном SSD — это локальный учебный стенд.

## Проверки

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/verify_evidence.py
```

Проверены OOMKilled при 48 MiB и успешный расчёт при пределе 112 MiB,
выбранном по трём замерам с запасом 25%. Также проверены четыре
параллельных индекса, ожидание CPU, вытеснение low/high и отказ по квоте.
Числа и статусы — в `evidence/`; все частоты — в `results/counts_2.json`.
Пути к манифестам в журналах приведены относительно корня репозитория.

Исходники отчёта с изображениями для Overleaf предоставлены отдельным архивом.
