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
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String PREFS = "ai_assistant_prefs";
    private static final String DEFAULT_URL = "https://ai-assistant-4yn0.onrender.com/v1/message";
    private static final String CLIENT_ID = "primary-user";
    private static final String CONVERSATION_ID = "default";
    private static final String DEVICE_ID = "android-" + android.os.Build.MODEL.replaceAll("[^a-zA-Z0-9_.-]+", "_");
    private static final int HISTORY_KEEP = 400;
    private static final long AUTO_SYNC_MS = 10000L;
    private static final Pattern STRUCTURED_LINE = Pattern.compile(
            "^\\s*(?:[-*]\\s*)?(?:\\*\\*)?(Observation|Diagnostic|Diagnosis|Recommended action|Command action|Action|Explanation|Conclusion)(?:\\*\\*)?\\s*:\\s*(.*)$",
            Pattern.CASE_INSENSITIVE
    );

    private SharedPreferences prefs;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private EditText apiUrlInput;
    private EditText tokenInput;
    private ScrollView scrollView;
    private TextView historyView;
    private EditText messageInput;
    private Button sendButton;
    private String persona = "ANA";
    private final List<JSONObject> events = new ArrayList<>();
    private boolean syncRunning = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        events.addAll(loadEvents());
        buildUi();
        renderHistory();
        syncHistory(false);
        scheduleAutoSync();
    }

    private void scheduleAutoSync() {
        mainHandler.postDelayed(() -> {
            syncHistory(false);
            scheduleAutoSync();
        }, AUTO_SYNC_MS);
    }
    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(12, 12, 12, 12);
        setContentView(root);

        apiUrlInput = new EditText(this);
        apiUrlInput.setSingleLine(true);
        apiUrlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
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
        saveButton.setOnClickListener(v -> { saveSettings(); syncHistory(true); });
        topButtons.addView(saveButton, weightedButton());

        Button syncButton = new Button(this);
        syncButton.setText("Sync");
        syncButton.setOnClickListener(v -> syncHistory(true));
        topButtons.addView(syncButton, weightedButton());

        Button downButton = new Button(this);
        downButton.setText("↓");
        downButton.setOnClickListener(v -> scrollToBottom());
        topButtons.addView(downButton, weightedButton());

        Button anaButton = new Button(this);
        anaButton.setText("ANA");
        anaButton.setOnClickListener(v -> sendText("/ana"));
        topButtons.addView(anaButton, weightedButton());

        Button timersButton = new Button(this);
        timersButton.setText("⏰");
        timersButton.setOnClickListener(v -> sendText("/timers"));
        topButtons.addView(timersButton, weightedButton());
        Button alienButton = new Button(this);
        alienButton.setText("ALIEN");
        alienButton.setOnClickListener(v -> sendText("/alien"));
        topButtons.addView(alienButton, weightedButton());
        root.addView(topButtons, matchWrap());

        historyView = new TextView(this);
        historyView.setTextSize(15);
        historyView.setTextIsSelectable(true);
        historyView.setPadding(8, 8, 8, 8);

        scrollView = new ScrollView(this);
        scrollView.addView(historyView);
        root.addView(scrollView, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

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
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams weightedButton() {
        return new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
    }

    private void saveSettings() {
        prefs.edit()
                .putString("api_url", apiUrlInput.getText().toString().trim())
                .putString("api_token", tokenInput.getText().toString().trim())
                .apply();
    }

    private List<JSONObject> loadEvents() {
        List<JSONObject> loaded = new ArrayList<>();
        String raw = prefs.getString("events", "[]");
        try {
            JSONArray array = new JSONArray(raw);
            for (int i = 0; i < array.length(); i++) {
                JSONObject item = array.optJSONObject(i);
                if (item != null) loaded.add(normalizeEvent(item));
            }
        } catch (Exception ignored) {
            String legacy = prefs.getString("history", "");
            if (!legacy.isEmpty()) {
                loaded.add(makeEvent("system", "Система", "Старая локальная история сохранена как текст. Новые сообщения синхронизируются по событиям."));
            }
        }
        return loaded;
    }

    private void saveEvents() {
        JSONArray array = new JSONArray();
        int start = Math.max(0, events.size() - HISTORY_KEEP);
        for (int i = start; i < events.size(); i++) array.put(events.get(i));
        prefs.edit().putString("events", array.toString()).apply();
    }

    private void syncHistory(boolean showToast) {
        if (syncRunning) return;
        String token = tokenInput.getText().toString().trim();
        if (token.isEmpty()) return;
        syncRunning = true;
        executor.execute(() -> requestSync(showToast, token));
    }

    private void requestSync(boolean showToast, String apiToken) {
        try {
            JSONObject body = new JSONObject();
            body.put("client_id", CLIENT_ID);
            body.put("conversation_id", CONVERSATION_ID);
            body.put("device_id", DEVICE_ID);
            body.put("limit", HISTORY_KEEP);
            JSONArray messages = new JSONArray();
            for (JSONObject event : events) {
                if (!"system".equals(event.optString("role"))) messages.put(toServerEvent(event));
            }
            body.put("messages", messages);
            JSONObject response = postJson(endpointUrl("/v1/sync"), apiToken, body, 45000);
            mainHandler.post(() -> {
                syncRunning = false;
                mergeServerMessages(response);
                if (showToast) Toast.makeText(this, "Синхронизация завершена", Toast.LENGTH_SHORT).show();
            });
        } catch (Exception error) {
            mainHandler.post(() -> {
                syncRunning = false;
                if (showToast) Toast.makeText(this, "Sync: " + error.getMessage(), Toast.LENGTH_LONG).show();
            });
        }
    }

    private void sendCurrentText() {
        String text = messageInput.getText().toString().trim();
        if (text.isEmpty()) return;
        messageInput.setText("");
        sendText(text);
    }

    private void sendText(String text) {
        saveSettings();
        JSONObject userEvent = makeEvent("user", "Вы", text);
        events.add(userEvent);
        saveEvents();
        renderHistory();
        setWaiting(true);
        executor.execute(() -> requestAnswer(text));
    }

    private void requestAnswer(String text) {
        try {
            String apiToken = requireToken();
            JSONObject body = new JSONObject();
            body.put("client_id", CLIENT_ID);
            body.put("conversation_id", CONVERSATION_ID);
            body.put("text", text);
            body.put("input_type", "text");
            JSONObject response = postJson(apiUrlInput.getText().toString().trim(), apiToken, body, 120000);
            String newPersona = response.optString("persona", persona);
            String answer = response.optString("text", "");
            mainHandler.post(() -> {
                persona = newPersona;
                events.add(makeEvent("assistant", persona, answer));
                List<JSONObject> compacted = mergeEvents(events, Collections.emptyList());
                events.clear();
                events.addAll(compacted);
                saveEvents();
                renderHistory();
                setWaiting(false);
                syncHistory(false);
        scheduleAutoSync();
            });
        } catch (Exception error) {
            mainHandler.post(() -> {
                events.add(makeEvent("system", "Ошибка", error.getMessage() == null ? error.toString() : error.getMessage()));
                saveEvents();
                renderHistory();
                setWaiting(false);
            });
        }
    }

    private JSONObject postJson(String urlText, String apiToken, JSONObject body, int timeoutMs) throws Exception {
        URL url = new URL(urlText);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(timeoutMs);
        connection.setReadTimeout(timeoutMs);
        connection.setDoOutput(true);
        connection.setRequestProperty("Authorization", "Bearer " + apiToken);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
        try (OutputStream output = connection.getOutputStream()) { output.write(bytes); }
        int code = connection.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? connection.getInputStream() : connection.getErrorStream();
        String response = readAll(stream);
        if (code < 200 || code >= 300) throw new RuntimeException("HTTP " + code + ": " + response);
        return new JSONObject(response);
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) builder.append(line);
        }
        return builder.toString();
    }

    private String endpointUrl(String endpoint) {
        String stripped = apiUrlInput.getText().toString().trim();
        String[] suffixes = {"/v1/message", "/v1/history", "/v1/snapshot", "/v1/sync", "/v1/chat/simple"};
        for (String suffix : suffixes) {
            if (stripped.endsWith(suffix)) return stripped.substring(0, stripped.length() - suffix.length()) + endpoint;
        }
        return stripped + endpoint;
    }

    private String requireToken() {
        String token = tokenInput.getText().toString().trim();
        if (token.isEmpty()) throw new IllegalStateException("APP_API_TOKEN пустой");
        return token;
    }

    private void mergeServerMessages(JSONObject response) {
        persona = response.optString("persona", persona);
        JSONArray messages = response.optJSONArray("messages");
        if (messages == null) return;
        List<JSONObject> remote = new ArrayList<>();
        for (int i = 0; i < messages.length(); i++) {
            JSONObject item = messages.optJSONObject(i);
            if (item == null) continue;
            String role = item.optString("role", "");
            if (!role.equals("user") && !role.equals("assistant") && !role.equals("system")) continue;
            String author = role.equals("user") ? "Вы" : (role.equals("assistant") ? persona : "Система");
            JSONObject event = new JSONObject();
            try {
                event.put("message_id", item.optString("message_id", ""));
                event.put("conversation_id", item.optString("conversation_id", CONVERSATION_ID));
                event.put("role", role);
                event.put("author", author);
                event.put("text", item.optString("content", ""));
                event.put("created_at", item.optString("created_at", nowUtc()));
                event.put("client_created_at", item.optString("client_created_at", item.optString("created_at", nowUtc())));
                event.put("device_id", item.optString("device_id", "server"));
                remote.add(normalizeEvent(event));
            } catch (Exception ignored) {}
        }
        List<JSONObject> merged = mergeEvents(events, remote);
        events.clear();
        events.addAll(merged);
        saveEvents();
        renderHistory();
    }

    private List<JSONObject> mergeEvents(List<JSONObject> local, List<JSONObject> remote) {
        List<JSONObject> merged = new ArrayList<>();
        HashSet<String> seenIds = new HashSet<>();
        HashSet<String> seenHashes = new HashSet<>();
        for (JSONObject source : local) addMerged(merged, seenIds, seenHashes, source);
        for (JSONObject source : remote) addMerged(merged, seenIds, seenHashes, source);
        Collections.sort(merged, (a, b) -> {
            int byDate = a.optString("created_at", "").compareTo(b.optString("created_at", ""));
            if (byDate != 0) return byDate;
            return a.optString("message_id", "").compareTo(b.optString("message_id", ""));
        });
        if (merged.size() > HISTORY_KEEP) return new ArrayList<>(merged.subList(merged.size() - HISTORY_KEEP, merged.size()));
        return merged;
    }

    private void addMerged(List<JSONObject> merged, HashSet<String> seenIds, HashSet<String> seenHashes, JSONObject raw) {
        try {
            JSONObject item = normalizeEvent(raw);
            String messageId = item.optString("message_id", "");
            String hash = item.optString("conversation_id") + "|" + item.optString("role") + "|" + sha256(item.optString("text"));
            if (seenIds.contains(messageId) || seenHashes.contains(hash)) return;
            seenIds.add(messageId);
            seenHashes.add(hash);
            merged.add(item);
        } catch (Exception ignored) {}
    }

    private JSONObject makeEvent(String role, String author, String text) {
        JSONObject event = new JSONObject();
        try {
            String createdAt = nowUtc();
            event.put("message_id", DEVICE_ID + "-" + UUID.randomUUID().toString().replace("-", ""));
            event.put("conversation_id", CONVERSATION_ID);
            event.put("role", role);
            event.put("author", author);
            event.put("text", text);
            event.put("created_at", createdAt);
            event.put("client_created_at", createdAt);
            event.put("device_id", DEVICE_ID);
        } catch (Exception ignored) {}
        return event;
    }

    private JSONObject normalizeEvent(JSONObject raw) throws Exception {
        JSONObject event = new JSONObject();
        String role = raw.optString("role", roleForAuthor(raw.optString("author", "Система")));
        String text = raw.optString("text", raw.optString("content", ""));
        String createdAt = raw.optString("created_at", raw.optString("client_created_at", nowUtc()));
        String messageId = raw.optString("message_id", raw.optString("message_uid", ""));
        if (messageId.isEmpty()) messageId = "android-local-" + sha256(CONVERSATION_ID + role + text + createdAt).substring(0, 32);
        event.put("message_id", messageId);
        event.put("conversation_id", raw.optString("conversation_id", CONVERSATION_ID));
        event.put("role", role);
        event.put("author", raw.optString("author", authorForRole(role)));
        event.put("text", text);
        event.put("created_at", createdAt);
        event.put("client_created_at", raw.optString("client_created_at", createdAt));
        event.put("device_id", raw.optString("device_id", DEVICE_ID));
        return event;
    }

    private JSONObject toServerEvent(JSONObject event) throws Exception {
        JSONObject out = new JSONObject();
        out.put("message_id", event.optString("message_id"));
        out.put("conversation_id", event.optString("conversation_id", CONVERSATION_ID));
        out.put("role", event.optString("role"));
        out.put("content", event.optString("text"));
        out.put("created_at", event.optString("created_at"));
        out.put("client_created_at", event.optString("client_created_at"));
        out.put("device_id", event.optString("device_id", DEVICE_ID));
        return out;
    }

    private String roleForAuthor(String author) {
        if (author.equals("Вы")) return "user";
        if (author.equals("ANA") || author.equals("ALIEN")) return "assistant";
        return "system";
    }

    private String authorForRole(String role) {
        if (role.equals("user")) return "Вы";
        if (role.equals("assistant")) return persona;
        return "Система";
    }

    private void renderHistory() {
        StringBuilder builder = new StringBuilder();
        if (events.isEmpty()) builder.append("Система:\nГотово. Команды: /ana, /alien, запомни: ..., /memory, /timers, /functions.\n\n");
        for (JSONObject event : events) {
            String author = event.optString("author", authorForRole(event.optString("role", "system")));
            String text = event.optString("text", "");
            builder.append(author).append(":\n").append(decorateStructuredText(author, text)).append("\n\n");
        }
        historyView.setText(builder.toString());
        scrollToBottom();
    }

    private String decorateStructuredText(String author, String text) {
        String[] lines = text.split("\\r?\\n");
        StringBuilder builder = new StringBuilder();
        for (String line : lines) {
            Matcher matcher = STRUCTURED_LINE.matcher(line);
            if (matcher.matches()) {
                builder.append("▌ ").append(labelFor(author, matcher.group(1))).append('\n');
                String rest = cleanMarkdown(matcher.group(2).trim());
                if (!rest.isEmpty()) builder.append("  ").append(rest).append('\n');
            } else {
                builder.append(cleanMarkdown(line)).append('\n');
            }
        }
        return builder.toString().trim();
    }

    private String labelFor(String author, String label) {
        String key = label.toLowerCase(Locale.ROOT);
        boolean alien = author.equals("ALIEN");
        if (key.equals("observation")) return alien ? "СИГНАЛ" : "НАБЛЮДЕНИЕ";
        if (key.equals("diagnostic") || key.equals("diagnosis")) return alien ? "ДИССОНАНС" : "ДИАГНОСТИКА";
        if (key.equals("recommended action") || key.equals("action")) return alien ? "НОТА ДЕЙСТВИЯ" : "ДЕЙСТВИЕ";
        if (key.equals("command action")) return alien ? "КОМАНДНАЯ НОТА" : "КОМАНДА";
        if (key.equals("explanation")) return alien ? "ГЛУБИНА" : "ОБЪЯСНЕНИЕ";
        if (key.equals("conclusion")) return alien ? "РЕЗОНАНС" : "ВЫВОД";
        return label.toUpperCase(Locale.ROOT);
    }

    private String cleanMarkdown(String text) {
        String cleaned = text;
        cleaned = cleaned.replaceAll("^#{1,6}\\s+", "");
        cleaned = cleaned.replaceAll("^>\\s?", "");
        cleaned = cleaned.replaceAll("^\\s*[-*+]\\s+", "• ");
        cleaned = cleaned.replaceAll("\\*\\*([^*]+)\\*\\*", "$1");
        cleaned = cleaned.replaceAll("__([^_]+)__", "$1");
        cleaned = cleaned.replaceAll("`([^`]+)`", "$1");
        cleaned = cleaned.replace("```", "").replace("**", "").replace("__", "").trim();
        while (cleaned.startsWith("*") || cleaned.startsWith("_")) cleaned = cleaned.substring(1).trim();
        while (cleaned.endsWith("*") || cleaned.endsWith("_")) cleaned = cleaned.substring(0, cleaned.length() - 1).trim();
        return cleaned;
    }

    private void scrollToBottom() {
        if (scrollView == null) return;
        scrollView.postDelayed(() -> scrollView.fullScroll(View.FOCUS_DOWN), 80);
    }

    private void setWaiting(boolean waiting) {
        sendButton.setEnabled(!waiting);
        sendButton.setText(waiting ? "Ждём..." : "Отправить");
    }

    private String nowUtc() {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.ROOT);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    private String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte b : hash) hex.append(String.format(Locale.ROOT, "%02x", b));
            return hex.toString();
        } catch (Exception error) {
            return String.valueOf(value.hashCode());
        }
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }
}
