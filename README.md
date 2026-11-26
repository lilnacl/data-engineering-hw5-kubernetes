# ДЗ5. Kubernetes: ресурсы и планирование

Вдовина Анастасия Романовна. Вариант №7: «Программист, разработчик на Go».

[Отчёт](<ИнжДан_lab5_Вдовина А.Р.pdf>) · [Итоговый топ-10](results/top10.json) · [Проверки доказательств](evidence/verification.json)

Локальный kind-кластер, RustFS 1.0.1 с EC 2+2 и четырьмя PVC, образ своего
расчёта из ДЗ3, OOMKilled и предел по пику, Indexed Job на четыре части CSV,
ожидание CPU, вытеснение и квота namespace. Все журналы относятся к реальным
запускам на macOS ARM64. Один узел и тома на одном SSD — учебный стенд.

## Результат

Все 13 552 частоты пар, 61 694 вхождения и топ-10 совпали с ДЗ3.
Отобрано 3 123 вакансии из 229 388 записей полного CSV.
Фильтр Go/Golang, нормализация, разбиение на чанки и map-функция сохранены.
[Происхождение кода](hw3/PROVENANCE.md), [побайтная сверка с GitHub](evidence/hw3-provenance.json).

Основные подтверждения:

| Опыт | Свидетельство |
|---|---|
| OOMKilled, exitCode 137 | [Pod status](evidence/skills-oom-pods.json) |
| Три замера и предел с запасом 25% | [Замеры и расчёт](evidence/memory-summary.json) |
| Успех с подобранным пределом | [Job](evidence/skills-tuned-job.json) |
| S3 GET и SHA-256 полного CSV и частей | [Манифест данных](evidence/source-upload.json) |
| Четыре параллельных индекса | [Pod status](evidence/skills-parallel-pods.json), [части](evidence/parallel-shards.json) |
| Полное совпадение частот | [Итог](evidence/parallel-summary.json), [все пары](results/counts_2.json) |
| Pending / Insufficient cpu | [Ожидающий Pod](evidence/cpu-pending.json), [завершившийся Pod](evidence/skills-cpu-wait-pods.json) |
| PreemptionByScheduler | [Жертва](evidence/preemption-victim.json), [события](evidence/preemption-events.json) |
| Отказ по квоте, затем успех | [Отказ](evidence/quota-rejected.log), [принятый Pod](evidence/quota-accepted-pod.json) |

## Воспроизведение

Нужны работающий Docker, Python 3.10+, kind 0.27.0 и kubectl 1.32.2 в PATH.
kind и kubectl доступны для macOS и Linux, ARM64 и AMD64. Образы узла,
Python и RustFS зафиксированы по digest. Для загрузки образов нужен интернет.
Стенд рассчитан на Docker с минимум 4 CPU, 6 GiB памяти и 10 GiB свободного
места. CPU резервируется по requests; фоновой нагрузкой все ядра не занимаются.

Установка kind: [официальный quick start](https://kind.sigs.k8s.io/docs/user/quick-start/).
В работе использована версия [v0.27.0](https://github.com/kubernetes-sigs/kind/releases/tag/v0.27.0).

Исходные данные — тот же полный
[it_vacancies_2024.csv](https://drive.google.com/file/d/1rLEu3N2isIBhAkK1EcSJ2482ouOPAots/view)
из ДЗ2/ДЗ3. Размер **847 619 384** байта. SHA-256:

```text
303db922b541e4440906f5a7e8be32fb55e8808f950ffddb8c272f1548212a65
```

Полный CSV не включён в Git. Программа проверяет его контрольную сумму до
загрузки, затем сверяет S3 GET каждого объекта по размеру и SHA-256.

Запуск из корня репозитория на новом кластере:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/run.py /absolute/path/it_vacancies_2024.csv
```

Скрипт создаёт **только** выделенный кластер `de-hw5` и сохраняет его kubeconfig
в `work/kubeconfig`. Глобальный kubeconfig не меняется. Действующие ключи S3
генерируются случайно, сохраняются в `work/credentials.json` с правами 0600
и Kubernetes Secret. В Git есть только шаблон Secret без рабочих ключей.
Локальный порт 19000 должен быть свободен. После опытов port-forward
закрывается, кластер остаётся доступен для проверки.

Для нового полного прогона удалить предыдущий **учебный** кластер:

```bash
kind delete cluster --name de-hw5
.venv/bin/python scripts/run.py /absolute/path/it_vacancies_2024.csv
```

Удаление кластера удаляет его локальные PVC и загруженные в него данные.
Исходный CSV на компьютере не изменяется.

Просмотр результатов в оставленном кластере:

```bash
export KUBECONFIG="$PWD/work/kubeconfig"
kubectl get nodes
kubectl get jobs,pods,pvc -n hw5
kubectl get resourcequota -n hw5-quota
kubectl get events -n hw5 --sort-by=.metadata.creationTimestamp
kubectl port-forward -n hw5 service/rustfs 19001:9001 --address 127.0.0.1
```

Последняя команда открывает консоль RustFS на `http://127.0.0.1:19001`;
ключи для входа находятся в локальном `work/credentials.json`.

## Как устроены опыты

`scripts/run.py` собирает образ, загружает и проверяет данные, затем запускает
`scripts/experiments.py`. Сгенерированные и фактически выполненные манифесты
лежат в `manifests/runs/`, команды с UTC-временем и кодом возврата — в
`evidence/commands.jsonl`; там же сохранены полные status и журналы.

Память измеряется по Linux `ru_maxrss` и cgroup v2 `memory.peak`.
Выбирается максимум трёх запусков, добавляется 25%, результат округляется
вверх до 16 MiB. При пределе 48 MiB тот же расчёт завершается OOMKilled;
искусственного заполнения памяти нет. Числа конкретного прогона находятся
в `evidence/memory-summary.json`. На другой платформе пик может отличаться;
сценарий проверит результат, а не выдаст неудачный опыт за успешный.

CSV делится по записям штатным парсером, поскольку поля содержат переносы
строк. Четыре индексированных работника публикуют **все** счётчики пар,
а reducer складывает их после проверки индексов 0–3. Ключ результата зависит
от индекса, поэтому повтор работника не добавляет лишний результат.

В опыте CPU служебный Pod резервирует requests и ожидает в sleep. В опыте
вытеснения низкоприоритетный Job циклически выполняет настоящий расчёт;
high Job вытесняет его. RustFS защищён высоким приоритетом. Квота проверяется
в отдельном namespace: второй Pod отклонён API при превышении requests.cpu,
а после освобождения квоты принимается.

## Проверки и отчёт

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/verify_evidence.py
.venv/bin/python scripts/build_report.py
tectonic report/main.tex
```

Проверка доказательств использует сохранённые Pod/Job status, условие
`PreemptionByScheduler`, текст отказа API, интервалы четырёх контейнеров,
SHA-256 и точное равенство всех частот. Отчёт строит таблицы и выдержки из
проверенных файлов. `report/` содержит исходники для Overleaf/XeLaTeX;
набор шрифтов и оформление сохранены из отчётов ДЗ3/ДЗ4.

При повторном прогоне сначала нужно выполнить `build_report.py`, затем
скомпилировать PDF и заменить сдаваемый файл: старый PDF относится к
зафиксированным в репозитории журналам от 26.11.2026.
