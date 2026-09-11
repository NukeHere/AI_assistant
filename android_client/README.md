# Android client MVP

Минимальный Android-клиент для AI Assistant.

Что умеет:

- отправляет сообщения на тот же серверный endpoint `/v1/message`;
- хранит `APP_API_TOKEN` локально в настройках приложения;
- сохраняет историю на телефоне через `SharedPreferences`;
- использует общий `client_id = primary-user`, чтобы память совпадала с desktop-клиентом;
- поддерживает быстрые кнопки `ANA` и `ALIEN`;
- позволяет выделять и копировать текст истории.

## Как собрать

1. Открой папку `android_client` в Android Studio.
2. Дождись Gradle Sync.
3. Подключи телефон с USB debugging или запусти эмулятор.
4. Нажми Run.
5. В приложении вставь URL:

```text
https://ai-assistant-4yn0.onrender.com/v1/message
```

6. Вставь локально `APP_API_TOKEN`. Не добавляй токен в GitHub.

Если Android Studio предложит обновить Android Gradle Plugin или SDK, можно согласиться.
