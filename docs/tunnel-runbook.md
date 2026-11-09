# Туннель ChatGPT для OpenRemote — эксплуатация

## Текущее развертывание на terra

На 9 ноября 2026 года существует **старый проверочный туннель** `openremote-probe` (не OpenRemote Agent). На terra он подключён к `openremote_probe.server` с тремя инструментами, а не к `openremote_agent.server` с семью инструментами. Эту границу НЕ скрывать: чат-плагин `OpenRemote test` сейчас доказывает только пробный MCP.

- Бинарник: `~/.local/opt/openremote-tunnel-client-v0.0.20/tunnel-client`
- Профиль: `~/.config/tunnel-client/openremote-probe.yaml`
- Key reference: `control_plane.api_key: env:CONTROL_PLANE_API_KEY`.
- Runtime key: `~/.config/openremote-agent/runtime-key`, mode 0600. Не печатать в чат, shell history, командных аргументах, репозитории или журнале.
- Приватный wrapper: `~/.local/libexec/openremote-tunnel-start`, mode 0700.
- Пользовательская systemd-служба: `~/.config/systemd/user/openremote-tunnel.service` (enabled, user linger=yes).
- Health UI только на `127.0.0.1:18766`, чтобы не конфликтовать с другими службами. Входящий внешний порт не открыт.

### Проверка

```bash
systemctl --user is-active openremote-tunnel.service
systemctl --user is-enabled openremote-tunnel.service
curl -fsS http://127.0.0.1:18766/healthz
curl -fsS http://127.0.0.1:18766/readyz
journalctl --user -u openremote-tunnel.service -n 25 --no-pager
```

Восстановление при проблемах:

```bash
systemctl --user restart openremote-tunnel.service
systemctl --user status openremote-tunnel.service --no-pager
```

Проверка конфигурации (секрет загружается только в окружение команды):

```bash
CONTROL_PLANE_API_KEY="$(cat ~/.config/openremote-agent/runtime-key)" \
  ~/.local/opt/openremote-tunnel-client-v0.0.20/tunnel-client doctor \
  --profile openremote-probe --explain
```

### Зафиксированный результат 9 ноября 2026

1. До запуска клиента обращения к `@OpenRemote test` завершались `MCP SSE probe returned 429 from openai.org`.
2. `doctor` без переменной окружения указывал ошибку отсутствия `CONTROL_PLANE_API_KEY`.
3. После подстановки существующего закрытого ключа `doctor` прошёл; после запуска `/healthz` и `/readyz` отвечают 200.
4. Прямые вызовы ChatGPT MCP `system_info`, `list_files`, `write_probe` прошли. Файл-квитанция создан и проверен по SHA-256 на terra.
5. Перевод под пользовательский systemd подтверждён `active/enabled`, `readyz 200`, повторные ChatGPT MCP вызовы `system_info` и `list_files` успешны.

Это подтверждает связь ChatGPT с **probe**, но ещё не с новым multi-device OpenRemote Agent. Для нового MCP понадобится отдельная регистрация/переподключение в ChatGPT Plugins с актуальным набором инструментов и учётом полномочий пользователя.

### Безопасность

Туннель не делает MCP публичным. Не публиковать `127.0.0.1:8766/mcp` или `~/.probe` напрямую. Глобальная роль operator в текущем агенте — временное MVP-ограничение; **не распространять между пользователями** до реализации per-user OAuth/RBAC, подтверждений критических действий и полноценного аудита. Не менять профиль `openremote-probe` на новый сервер без проверки совместимости уже установленного плагина и сохранения старых tools.

Официальная документация:
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://developers.openai.com/plugins/deploy/connect-chatgpt
