package com.nukehere.aiassistant;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String PREFS = "ai_assistant_prefs";
    private static final String DEFAULT_URL = "https://ai-assistant-4yn0.onrender.com/v1/message";
    private static final String CLIENT_ID = "primary-user";
    private static final String CONVERSATION_ID = "default";
    private static final Pattern STRUCTURED_LINE = Pattern.compile(
            "^\\s*(?:[-*]\\s*)?(?:\\*\\*)?(Observation|Diagnostic|Diagnosis|Recommended action|Command action|Action|Explanation|Conclusion)(?:\\*\\*)?\\s*:\\s*(.*)$",
            Pattern.CASE_INSENSITIVE
    );

    private SharedPreferences prefs;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private EditText apiUrlInput;
    private EditText tokenInput;
    private TextView historyView;
    private EditText messageInput;
    private Button sendButton;
    private String persona = "ANA";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        setContentView(buildLayout());
        historyView.setText(prefs.getString("history", "Система:\nГотово. Команды: /ana, /alien, запомни: ..., /memory, /functions.\n\n"));
        syncHistory();
    }

    private View buildLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(20, 20, 20, 20);

        apiUrlInput = new EditText(this);
        apiUrlInput.setSingleLine(true);
        apiUrlInput.setText(prefs.getString("api_url", DEFAULT_URL));
        apiUrlInput.setHint("Server URL");
        root.addView(apiUrlInput, matchWrap());

        tokenInput = new EditText(this);
        tokenInput.setSingleLine(true);
        tokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tokenInput.setText(prefs.getString("api_token", ""));
        tokenInput.setHint("APP_API_TOKEN");
        root.addView(tokenInput, matchWrap());

        LinearLayout topButtons = new LinearLayout(this);
        topButtons.setOrientation(LinearLayout.HORIZONTAL);
        topButtons.setGravity(Gravity.CENTER_VERTICAL);

        Button saveButton = new Button(this);
        saveButton.setText("Save");
        saveButton.setOnClickListener(v -> {
            saveSettings();
            syncHistory();
        });
        topButtons.addView(saveButton, weightedButton());

        Button syncButton = new Button(this);
        syncButton.setText("Sync");
        syncButton.setOnClickListener(v -> syncHistory());
        topButtons.addView(syncButton, weightedButton());

        Button anaButton = new Button(this);
        anaButton.setText("ANA");
        anaButton.setOnClickListener(v -> sendText("/ana"));
        topButtons.addView(anaButton, weightedButton());

        Button alienButton = new Button(this);
        alienButton.setText("ALIEN");
        alienButton.setOnClickListener(v -> sendText("/alien"));
        topButtons.addView(alienButton, weightedButton());
        root.addView(topButtons, matchWrap());

        historyView = new TextView(this);
        historyView.setTextSize(15);
        historyView.setTextIsSelectable(true);
        historyView.setPadding(8, 8, 8, 8);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(historyView);
        root.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1f
        ));

        messageInput = new EditText(this);
        messageInput.setMinLines(2);
        messageInput.setMaxLines(5);
        messageInput.setHint("Сообщение");
        root.addView(messageInput, matchWrap());

        sendButton = new Button(this);
        sendButton.setText("Отправить");
        sendButton.setTypeface(Typeface.DEFAULT_BOLD);
        sendButton.setOnClickListener(v -> sendCurrentText());
        root.addView(sendButton, matchWrap());

        return root;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
    }

    private LinearLayout.LayoutParams weightedButton() {
        return new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
    }

    private void saveSettings() {
        saveSettings(true);
    }

    private void saveSettings(boolean showToast) {
        prefs.edit()
                .putString("api_url", apiUrlInput.getText().toString().trim())
                .putString("api_token", tokenInput.getText().toString().trim())
                .apply();
        if (showToast) {
            Toast.makeText(this, "Настройки сохранены", Toast.LENGTH_SHORT).show();
        }
    }

    private void syncHistory() {
        saveSettings(false);
        executor.execute(this::requestHistory);
    }

    private void requestHistory() {
        try {
            String historyUrl = endpointUrl("/v1/history");
            String apiToken = requireToken();
            JSONObject body = new JSONObject();
            body.put("client_id", CLIENT_ID);
            body.put("conversation_id", CONVERSATION_ID);
            body.put("limit", 200);

            JSONObject response = postJson(historyUrl, apiToken, body, 45000);
            persona = response.optString("persona", persona);
            JSONArray messages = response.optJSONArray("messages");
            if (messages == null || messages.length() == 0) {
                return;
            }
            StringBuilder rendered = new StringBuilder();
            for (int i = 0; i < messages.length(); i++) {
                JSONObject item = messages.optJSONObject(i);
                if (item == null) {
                    continue;
                }
                String role = item.optString("role", "");
                String content = item.optString("content", "");
                if (content.isEmpty()) {
                    continue;
                }
                String author = role.equals("user") ? "Вы" : persona;
                rendered.append(renderMessage(author, content)).append("\n");
            }
            rendered.append("Система:\nИстория синхронизирована с сервером.\n\n");
            mainHandler.post(() -> {
                historyView.setText(rendered.toString());
                prefs.edit().putString("history", rendered.toString()).apply();
            });
        } catch (Exception ignored) {
            // Keep local history when offline or when token is not set yet.
        }
    }

    private void sendCurrentText() {
        String text = messageInput.getText().toString().trim();
        if (text.isEmpty()) {
            return;
        }
        messageInput.setText("");
        sendText(text);
    }

    private void sendText(String text) {
        saveSettings(false);
        appendLine("Вы", text);
        setWaiting(true);
        executor.execute(() -> requestAnswer(text));
    }

    private void requestAnswer(String text) {
        try {
            String apiUrl = prefs.getString("api_url", DEFAULT_URL);
            String apiToken = requireToken();

            JSONObject body = new JSONObject();
            body.put("client_id", CLIENT_ID);
            body.put("conversation_id", CONVERSATION_ID);
            body.put("text", text);
            body.put("input_type", "text");

            JSONObject response = postJson(apiUrl, apiToken, body, 90000);
            String responsePersona = response.optString("persona", persona);
            String answer = response.getString("text");
            mainHandler.post(() -> {
                persona = responsePersona;
                appendLine(persona, answer);
                setWaiting(false);
                syncHistory();
            });
        } catch (Exception error) {
            mainHandler.post(() -> {
                appendLine("Ошибка", error.getMessage() == null ? error.toString() : error.getMessage());
                setWaiting(false);
            });
        }
    }

    private String requireToken() {
        String apiToken = prefs.getString("api_token", "");
        if (apiToken == null || apiToken.trim().isEmpty()) {
            throw new IllegalStateException("APP_API_TOKEN не задан в настройках Android-клиента");
        }
        return apiToken.trim();
    }

    private String endpointUrl(String endpoint) {
        String apiUrl = prefs.getString("api_url", DEFAULT_URL);
        if (apiUrl == null) {
            apiUrl = DEFAULT_URL;
        }
        String stripped = apiUrl.trim();
        String[] suffixes = {"/v1/message", "/v1/history", "/v1/snapshot", "/v1/chat/simple"};
        for (String suffix : suffixes) {
            if (stripped.endsWith(suffix)) {
                return stripped.substring(0, stripped.length() - suffix.length()) + endpoint;
            }
        }
        return stripped.replaceAll("/+$", "") + endpoint;
    }

    private JSONObject postJson(String url, String apiToken, JSONObject body, int timeoutMs) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(30000);
        connection.setReadTimeout(timeoutMs);
        connection.setRequestProperty("Authorization", "Bearer " + apiToken);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        connection.setDoOutput(true);

        try (OutputStream output = connection.getOutputStream()) {
            output.write(body.toString().getBytes(StandardCharsets.UTF_8));
        }

        int code = connection.getResponseCode();
        String raw = readAll(code >= 400 ? connection.getErrorStream() : connection.getInputStream());
        if (code >= 400) {
            throw new IllegalStateException("HTTP " + code + ": " + raw);
        }
        return new JSONObject(raw);
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
        }
        return builder.toString().trim();
    }

    private void appendLine(String author, String text) {
        String current = historyView.getText().toString();
        String next = current + renderMessage(author, text);
        historyView.setText(next);
        prefs.edit().putString("history", next).apply();
    }

    private String renderMessage(String author, String text) {
        return author + ":\n" + decorateStructuredText(author, text) + "\n\n";
    }

    private String decorateStructuredText(String author, String text) {
        String[] lines = text.split("\\r?\\n");
        StringBuilder builder = new StringBuilder();
        for (String line : lines) {
            Matcher matcher = STRUCTURED_LINE.matcher(line);
            if (matcher.matches()) {
                builder.append("▌ ").append(labelFor(author, matcher.group(1))).append('\n');
                String rest = cleanMarkdown(matcher.group(2).trim());
                if (!rest.isEmpty()) {
                    builder.append("  ").append(rest).append('\n');
                }
            } else {
                builder.append(cleanMarkdown(line)).append('\n');
            }
        }
        return builder.toString().trim();
    }

    private String labelFor(String author, String label) {
        String key = label.toLowerCase();
        boolean alien = author.equals("ALIEN");
        if (key.equals("observation")) return alien ? "СИГНАЛ" : "НАБЛЮДЕНИЕ";
        if (key.equals("diagnostic") || key.equals("diagnosis")) return alien ? "ДИССОНАНС" : "ДИАГНОСТИКА";
        if (key.equals("recommended action") || key.equals("action")) return alien ? "НОТА ДЕЙСТВИЯ" : "ДЕЙСТВИЕ";
        if (key.equals("command action")) return alien ? "КОМАНДНАЯ НОТА" : "КОМАНДА";
        if (key.equals("explanation")) return alien ? "ГЛУБИНА" : "ОБЪЯСНЕНИЕ";
        if (key.equals("conclusion")) return alien ? "РЕЗОНАНС" : "ВЫВОД";
        return label.toUpperCase();
    }

    private String cleanMarkdown(String text) {
        String cleaned = text;
        cleaned = cleaned.replaceAll("^#{1,6}\\s+", "");
        cleaned = cleaned.replaceAll("^>\\s?", "");
        cleaned = cleaned.replaceAll("^\\s*[-*+]\\s+", "• ");
        cleaned = cleaned.replaceAll("\\*\\*([^*]+)\\*\\*", "$1");
        cleaned = cleaned.replaceAll("__([^_]+)__", "$1");
        cleaned = cleaned.replaceAll("`([^`]+)`", "$1");
        cleaned = cleaned.replace("```", "");
        cleaned = cleaned.replace("**", "").replace("__", "").trim();
        while (cleaned.startsWith("*") || cleaned.startsWith("_")) {
            cleaned = cleaned.substring(1).trim();
        }
        while (cleaned.endsWith("*") || cleaned.endsWith("_")) {
            cleaned = cleaned.substring(0, cleaned.length() - 1).trim();
        }
        return cleaned;
    }

    private void setWaiting(boolean waiting) {
        sendButton.setEnabled(!waiting);
        sendButton.setText(waiting ? "Ждём..." : "Отправить");
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }
}
