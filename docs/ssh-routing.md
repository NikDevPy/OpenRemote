# OpenRemote — SSH/Tailscale routing (v0.1)

## Топология, проверенная 9 ноября 2026

```text
 ChatGPT (отдельно подключаемый custom MCP)
             |
       Secure MCP Tunnel
             |
 terra: OpenRemote Agent (MCP stdio)
             |
    allowlist "rock" → SSH over Tailscale
             |
 rock: OpenRemote Agent (MCP stdio)
             |
    sandbox + локальный allowlist команд
```

В этой проверке `terra -> rock` работает. Клиент на terra вызывает строго известный адрес SSH peer; хост-ключ предварительно присутствует в `~/.ssh/known_hosts`. Запросы MCP не подставляются в командную строку SSH, а идут JSON-RPC по stdio. На rock нет постоянно открытого входящего MCP-порта — каждый вызов создаёт короткую SSH-сессию. Серверы rock и terra работают без sudo.

### Конфигурация для peer

`Config.peers` — закрытый локальный реестр. Пример (значения адаптировать; настоящий Tailnet IP не коммитить):

```json
{
  "device_id": "terra",
  "root": "/home/domenus/.local/share/openremote-agent/sandbox",
  "state": "/home/domenus/.local/state/openremote-agent",
  "role": "operator",
  "commands": {},
  "peers": {
    "rock": {
      "target": "nora-rock-admin",
      "host_name": "100.64.0.1",
      "python": "/home/nora-admin/Projects/OpenRemote-agent/.venv/bin/python",
      "config_path": "/home/nora-admin/Projects/OpenRemote-agent/rock.json"
    }
  }
}
```

Это только иллюстрация структуры. `host_name` должен быть реальным Tailscale-адресом, `target` — заранее проверенный SSH-алиас. Проверка ключа хоста (`StrictHostKeyChecking=yes`) обязательна. Сервер выполняет только `python -m openremote_agent.server --config <remote-config>`. Внешний MCP-запрос не может заменить команду или hostname. Отказ SSH не инициирует обход через публичную сеть.

Удалённый конфиг должен идентифицировать себя как `rock`, содержать отдельные `root` и `state` (mode 0700), `role` и локальный список разрешённых команд. Он не доверяет роли terra автоматически: каждую операцию проверяет собственный Agent. Terra отдельно проверяет роль и белый список peer. На нынешнем MVP роль единая для экземпляра сервера, **не на пользователя/подключение**.

### Функции и подтверждённые ограничения

- `list_devices`: показывает terra online и настроенных peer со статусом `configured`; **не выдаёт configured за online**.
- `system_info(device_id)`, `list_files(path,device_id)`, `read_text`, `write_text`, `execute_command`, `audit_tail`: маршрутизируются локально или через SSH. Неизвестный `device_id` отклоняется.
- SSH отключает форвардинг, agent forwarding, интерактивный запрос пароля; требует проверенный host key. 5 с на соединение, общий таймаут обмена 75 с.
- Удалённый файловый sandbox: path traversal и symlink запрещены; файлы до 64 КиБ; overwrite запрещён.
- `execute_command`: только локальный allowlist, без пользовательских argv или shell; stdout/stderr до 8 КиБ, таймаут одной команды не более 60 с.
- Аудит обеих машин сохраняется в отдельной SQLite без аргументов, содержимого и stdout.
- Реальный smoke: обнаружение terra/rock, информация rock Linux aarch64, `python-version` с кодом 0, create→read→list файла, отказ `../rock.json` и неизвестному устройству, локальный terra продолжает работать.
- Для новых устройств нужно предварительно установить агента, выдать доступ в Tailnet, проверить SSH host key и вручную добавить peer. Автоматического enrollment, push файлов и multi-user OAuth пока нет.

Запуск реального smoke (запись только в выделенный sandbox на rock):

```bash
.venv/bin/python scripts/live_ssh_smoke.py
```

Не запускайте smoke в продакшен-системах без конфигурации, описывающей отдельный тестовый sandbox.
