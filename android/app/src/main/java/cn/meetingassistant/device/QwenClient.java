package cn.meetingassistant.device;

import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class QwenClient {
    private QwenClient() {}

    static boolean validEndpoint(String endpoint) {
        try {
            URL url = new URL(endpoint);
            return "https".equals(url.getProtocol()) && url.getHost() != null
                    && !url.getHost().isEmpty() && url.getPath().endsWith("/chat/completions")
                    && url.getUserInfo() == null;
        } catch (Exception ignored) { return false; }
    }

    static String transcribe(String endpoint, String key, byte[] wav) throws Exception {
        String uri = "data:audio/wav;base64," + Base64.encodeToString(wav, Base64.NO_WRAP);
        if (uri.length() >= 10_000_000) throw new IOException("音频块超过线上接口限制");
        JSONObject audio = new JSONObject().put("data", uri);
        JSONObject part = new JSONObject().put("type", "input_audio").put("input_audio", audio);
        JSONObject message = new JSONObject().put("role", "user")
                .put("content", new JSONArray().put(part));
        JSONObject body = new JSONObject().put("model", "qwen3-asr-flash")
                .put("messages", new JSONArray().put(message)).put("stream", false)
                .put("asr_options", new JSONObject().put("language", "zh").put("enable_itn", false));
        return request(endpoint, key, body);
    }

    static String summarize(String endpoint, String key, String title, String transcript) throws Exception {
        if (transcript.trim().isEmpty()) throw new IOException("转写文本为空");
        if (transcript.length() > 14000) throw new IOException("当前设备版纪要只支持 14000 字以内，请导出后用桌面版分段生成");
        JSONObject system = new JSONObject().put("role", "system").put("content",
                "你是严谨的中文会议纪要助手。仅依据转写文本提取结论、决定、行动项、待确认问题。不要补造事实；没有明确负责人或期限时写未提及。麦克风录音不代表已收录远端声音。");
        JSONObject user = new JSONObject().put("role", "user").put("content",
                "会议《" + title + "》的转写：\n" + transcript);
        JSONObject body = new JSONObject().put("model", "qwen-plus")
                .put("messages", new JSONArray().put(system).put(user)).put("stream", false);
        return request(endpoint, key, body);
    }

    private static String request(String endpoint, String key, JSONObject body) throws Exception {
        if (!validEndpoint(endpoint) || key == null || key.trim().isEmpty()) throw new IOException("需要 HTTPS 接口与 API Key");
        HttpURLConnection connection = (HttpURLConnection) new URL(endpoint).openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(180000);
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        connection.setRequestProperty("Authorization", "Bearer " + key);
        try {
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) throw new IOException("线上模型返回 HTTP " + status);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            try (InputStream in = connection.getInputStream()) {
                byte[] buffer = new byte[8192]; int count;
                while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            }
            JSONObject response = new JSONObject(out.toString("UTF-8"));
            String answer = response.getJSONArray("choices").getJSONObject(0)
                    .getJSONObject("message").getString("content").trim();
            if (answer.isEmpty()) throw new IOException("线上模型返回空文本");
            return answer;
        } finally { connection.disconnect(); }
    }
}
