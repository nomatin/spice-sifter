# Spice Sifter Wiki

Подробная документация Spice Sifter 0.6. Краткое описание и быстрый старт — в [README](../README.md).

## Использование

- [Руководство пользователя](User-Guide.md) — запуск, входной JSON, поддерживаемые анализы, DbLib, AC, графики, температуры, Parameter Sweep и ошибки.
- [Скрытый экземпляр Altium](HIDDEN_ALTIUM.md) — отдельный экземпляр, требования, безопасность, диагностика и восстановление.
- [Автоматические измерения](MEASUREMENTS.md) — форматы и алгоритмы измерений.
- [Контракт JSON-отчёта 1.0](REPORT_CONTRACT.md) — `results[]`, `coverage`, статусы и инварианты.
- [Интеграция с пакетными агентами](AGENT_INTEGRATION.md) — `doctor --json`, `recover`, preflight и остановка серии.

## Разработка и проверка

- [Архитектура и политика тестирования](DEVELOPMENT.md)
- [Проверки измерений](MEASUREMENT_VERIFICATION.md)
- [Базовые проверки (0.1)](VERIFICATION.md)
- [Проверки AC (0.2)](AC_VERIFICATION.md)
- [Температурные проверки (0.3)](TEMPERATURE_VERIFICATION.md)
- [Проверки Parameter Sweep (0.4)](SWEEP_VERIFICATION.md)

[История изменений](../CHANGELOG.md) · [Примеры тестов](../examples/)
