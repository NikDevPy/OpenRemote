# OpenRemote Agent 0.3.1 — локальный агент с рабочими областями и терминалом

Новая реализация в ветке `feature/secure-agent-mvp`. Старый `openremote_probe` не изменён. Это **отдельный локальный агент**, а не уже работающий универсальный плагин на нескольких компьютерах.

## Возможности

- 11 MCP-инструментов: `system_info`, `list_devices`, `list_workspaces`, `list_files`, `read_text`, `write_text`, `replace_text`, `preview_command`, `execute_command`, `run_argv`, `audit_tail`. Файловые рабочие области и динамический терминал описаны в [owner control](owner-control.md); фиксированные команды и разовое подтверждение — в [командных политиках](command-policies.md).
- Read: каталоги/текст до 64 КиБ в `sandbox` или разрешённых владельцем `workspaces`, без обхода symlink/`..`.
- Write: создание нового файла или контролируемая замена по SHA-256 в области с `access=write`, требует operator. Замена помечена потенциально разрушительной.
- Command: `execute_command` использует локальный allowlist; отдельный **высокорисковый** `run_argv` разрешает динамический argv только при `role=admin` и `ad_hoc_enabled=true`. Он работает с правами учётной записи ОС; `workspace` определяет рабочий каталог, но **не ограничивает доступ процесса к другим файлам ОС**. Обе операции не используют `shell=True`, ограничивают время и вывод.
- Аудит в SQLite с разрешениями 0600, без параметров, путей, содержимого файлов, stdout или секретов. Роли viewer/operator/admin.
- HTTP только `127.0.0.1:8766`; локальный bearer в переменной `OPENREMOTE_TOKEN`. На публичном доступе нужен OAuth и полноценная модель пользователей. Текущая роль глобальна для конкретного экземпляра агента, **не индивидуальная проверка пользователя**.
- Входящие подключения извне и автоматический запуск не включены. Устройство `terra` не теряет имеющийся канал Remote Desktop Commander.

## Запуск

В проекте с установленной `.venv`:

```bash
cp examples/terra.json ~/.config/openremote-agent.json
chmod 600 ~/.config/openremote-agent.json
.venv/bin/python -m openremote_agent.server --config ~/.config/openremote-agent.json --doctor
.venv/bin/python -m openremote_agent.server --config ~/.config/openremote-agent.json --transport stdio
```

Замените абсолютные пути sandbox/state, device_id и allowlist под устройство. Команды должны использовать абсолютный исполняемый путь. По умолчанию в примере роль viewer: это намеренное ограничение. Для тестов `write_text` и `execute_command` администратор явно меняет её на operator; для `audit_tail` нужна admin. Установка MCP должна быть выполнена отдельно на доверенной стороне (например, через одобренный частный tunnel). **НЕ публикуйте loopback HTTP в Интернет.**

Локальный HTTP:

```bash
# Передать OPENREMOTE_TOKEN безопасно через менеджер секретов; не хранить в Git
.venv/bin/python -m openremote_agent.server --config ~/.config/openremote-agent.json --transport http
```

Запуск без токена >=32 символов отклоняется. Это защита локальной отладки, не OAuth.

## Тесты

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/local_smoke.py
```

Тесты покрывают MCP stdio end-to-end, командный allowlist, права, запрет path traversal/symlink, отсутствие перезаписи, SQLite persistence. Старый проверочный MCP сохранён.

## Не реализовано (намеренно)

- Автоматическая регистрация/поиск удалённых устройств, брокер, управление SSH-ключами и полноценное определение их online/offline состояния. **Ручной SSH/Tailscale маршрут terra → rock уже реализован и проверен**, см. [SSH routing](ssh-routing.md).
- Разделение пользователей и независимая аутентификация каждого клиента; токен не подходит для публикации.
- Произвольный терминал/PTY; принудительное подтверждение изменения на стороне устройства.
- Инкрементальные файловые операции, двоичные файлы, передачи больших файлов, git API, конфиг на Windows/macOS.
- End-to-end **нового multi-device Agent** непосредственно через ChatGPT Plus ещё не выполнен. Старый OpenRemote Probe восстановлен после запуска tunnel-client с переменной `CONTROL_PLANE_API_KEY`; его инструменты теперь работают из ChatGPT, см. [runbook](tunnel-runbook.md).

Не выдавать рабочий локальный MCP за работоспособный облачный интеграционный маршрут.
