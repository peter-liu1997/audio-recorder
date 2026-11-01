package cn.meetingassistant.device;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsetsController;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;
import java.io.File;
import java.io.FileInputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

public final class MainActivity extends Activity {
    private static final int NAVY = Color.rgb(17, 43, 62);
    private static final int TEAL = Color.rgb(0, 140, 135);
    private static final int MUTED = Color.rgb(100, 115, 125);
    private static final int PAGE = Color.rgb(246, 244, 240);
    private EditText titleInput, endpointInput, keyInput;
    private Switch cloudSwitch;
    private Spinner deviceSpinner;
    private TextView statusText, transcriptText, selectedText, timerText;
    private Button mainButton, exportAudioButton, exportTextButton, summaryButton;
    private LinearLayout historyList;
    private final List<InputChoice> choices = new ArrayList<>();
    private File selectedFolder, pendingExport;
    private Intent pendingStart;
    private long startedAt;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            String state = intent.getStringExtra("state");
            String message = intent.getStringExtra("message");
            if (state == null) return;
            statusText.setText(message);
            if ("transcript".equals(state)) transcriptText.append("\n" + message);
            if ("recording".equals(state)) startedAt = System.currentTimeMillis();
            if ("complete".equals(state) || "error".equals(state)) {
                mainButton.setText("开始录制");
                refreshHistory();
                if ("error".equals(state)) toast(message);
            }
        }
    };
    private static final class InputChoice {
        final String name; final int id;
        InputChoice(String name, int id) { this.name = name; this.id = id; }
        @Override public String toString() { return name; }
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(PAGE);
        getWindow().setNavigationBarColor(PAGE);
        buildUi();
        if (Build.VERSION.SDK_INT >= 30) {
            int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            getWindow().getInsetsController().setSystemBarsAppearance(light, light);
        } else {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                    | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        }
        refreshDevices();
        refreshHistory();
        handler.post(new Runnable() {
            @Override public void run() {
                if (RecordingService.active && startedAt > 0) {
                    long elapsed = (System.currentTimeMillis() - startedAt) / 1000;
                    timerText.setText(String.format(java.util.Locale.US, "%02d:%02d:%02d",
                            elapsed / 3600, (elapsed / 60) % 60, elapsed % 60));
                }
                handler.postDelayed(this, 1000);
            }
        });
    }

    private void buildUi() {
        boolean wide = getResources().getConfiguration().screenWidthDp >= 700;
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(PAGE);
        LinearLayout outer = vertical(); outer.setPadding(dp(24), dp(22), dp(24), dp(30));
        scroll.addView(outer); setContentView(scroll);

        LinearLayout hero = vertical();
        hero.setPadding(dp(24), dp(wide ? 14 : 20), dp(24), dp(wide ? 15 : 22));
        hero.setBackground(round(NAVY, 24)); outer.addView(hero, matchWrap());
        TextView eyebrow = text("见声  /  DEVICE EDITION", 12, 0xff87d8cf, true);
        hero.addView(eyebrow);
        TextView heading = text(wide ? "让每场会议留下清晰记录" : "让每场会议\n留下清晰记录",
                wide ? 24 : 30, Color.WHITE, true);
        heading.setLineSpacing(dp(2), 1); heading.setPadding(0, dp(wide ? 4 : 10), 0, dp(wide ? 3 : 7)); hero.addView(heading);
        TextView desc = text("会议平板原生录音 · 中文实时转写 · 可导出文件", 14, 0xffc2d4dc, false);
        hero.addView(desc);

        LinearLayout columns = new LinearLayout(this);
        columns.setOrientation(wide ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        outer.addView(columns, matchWrapWithTop(18));
        LinearLayout left = vertical(); LinearLayout right = vertical();
        if (wide) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1);
            lp.rightMargin = dp(18); columns.addView(left, lp);
            columns.addView(right, new LinearLayout.LayoutParams(0, -2, 1));
        } else {
            columns.addView(left, matchWrap()); columns.addView(right, matchWrap());
        }

        LinearLayout setup = card(left, "01 录制设置");
        label(setup, "会议名称");
        titleInput = input("例如：产品方案评审", false);
        setup.addView(titleInput, matchWrapWithTop(6));
        label(setup, "音频输入");
        deviceSpinner = new Spinner(this); deviceSpinner.setBackground(round(0xfff2f5f5, 12));
        setup.addView(deviceSpinner, new LinearLayout.LayoutParams(-1, dp(50)));
        Button reload = secondary("刷新设备");
        reload.setOnClickListener(v -> refreshDevices()); setup.addView(reload, matchWrapWithTop(10));
        TextView sourceNote = text("仅录制所选麦克风或 USB 音频输入。同机会议应用占用麦克风时，本应用可能收到静音。远端声音需要设备厂商接口、可路由的物理音频输入，或 Windows OPS 方案。", 13, MUTED, false);
        sourceNote.setLineSpacing(dp(3), 1); sourceNote.setPadding(0, dp(13), 0, 0); setup.addView(sourceNote);

        LinearLayout cloud = card(left, "02 可选 AI 转写");
        cloudSwitch = new Switch(this); cloudSwitch.setText("启用线上 Qwen 实时转写");
        cloudSwitch.setTextColor(NAVY); cloud.addView(cloudSwitch, matchWrap());
        label(cloud, "百炼工作空间 HTTPS 地址");
        endpointInput = input("https://…/chat/completions", false);
        endpointInput.setSingleLine(true); endpointInput.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        endpointInput.setText(getPreferences(0).getString("endpoint", ""));
        cloud.addView(endpointInput, matchWrapWithTop(6));
        label(cloud, "API Key（本次运行使用，不保存）");
        keyInput = input("填写后才会上传 15 秒音频块", true);
        cloud.addView(keyInput, matchWrapWithTop(6));
        TextView privacy = text("关闭时只在设备本地保存 WAV。开启后，音频块会发送至填写的线上接口。", 12, MUTED, false);
        privacy.setPadding(0, dp(12), 0, 0); cloud.addView(privacy);

        LinearLayout live = card(right, "03 正在进行");
        timerText = text("00:00:00", 38, NAVY, true); live.addView(timerText);
        statusText = text("准备就绪", 14, MUTED, false);
        statusText.setPadding(0, dp(6), 0, dp(16)); live.addView(statusText);
        mainButton = new Button(this); mainButton.setAllCaps(false); mainButton.setText("开始录制");
        mainButton.setTextColor(Color.WHITE); mainButton.setTextSize(17);
        mainButton.setBackground(round(TEAL, 16));
        mainButton.setMinHeight(dp(58)); mainButton.setOnClickListener(v -> toggleRecording());
        live.addView(mainButton, matchWrap());
        transcriptText = text("转写片段会显示在这里。", 14, NAVY, false);
        transcriptText.setPadding(dp(16), dp(16), dp(16), dp(16));
        transcriptText.setBackground(round(0xfff1f5f3, 14));
        live.addView(transcriptText, matchWrapWithTop(16));

        LinearLayout saved = card(right, "04 历史会议");
        selectedText = text("选择一场会议以导出或生成纪要", 13, MUTED, false);
        saved.addView(selectedText);
        historyList = vertical(); saved.addView(historyList, matchWrapWithTop(12));
        exportAudioButton = secondary("导出录音 WAV");
        exportTextButton = secondary("导出转写 TXT");
        summaryButton = secondary("生成中文纪要");
        exportAudioButton.setOnClickListener(v -> exportFile("audio.wav", "audio/wav"));
        exportTextButton.setOnClickListener(v -> exportFile("transcript.txt", "text/plain"));
        summaryButton.setOnClickListener(v -> summarize());
        saved.addView(exportAudioButton, matchWrapWithTop(10));
        saved.addView(exportTextButton, matchWrapWithTop(8));
        saved.addView(summaryButton, matchWrapWithTop(8));
        updateSelection();
    }

    private void refreshDevices() {
        choices.clear(); choices.add(new InputChoice("设备默认麦克风", -1));
        AudioManager manager = (AudioManager) getSystemService(AUDIO_SERVICE);
        for (AudioDeviceInfo info : manager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
            String name = info.getProductName() == null ? "音频输入" : info.getProductName().toString();
            choices.add(new InputChoice(name + " · ID " + info.getId(), info.getId()));
        }
        ArrayAdapter<InputChoice> adapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_dropdown_item, choices);
        deviceSpinner.setAdapter(adapter);
    }

    private void toggleRecording() {
        if (RecordingService.active) {
            startService(new Intent(this, RecordingService.class).setAction(RecordingService.ACTION_STOP));
            mainButton.setEnabled(false);
            handler.postDelayed(() -> mainButton.setEnabled(true), 1800);
            return;
        }
        String title = titleInput.getText().toString().trim();
        if (title.isEmpty()) { titleInput.setError("请填写会议名称"); return; }
        String endpoint = endpointInput.getText().toString().trim();
        String key = keyInput.getText().toString().trim();
        boolean cloud = cloudSwitch.isChecked();
        if (cloud && (!QwenClient.validEndpoint(endpoint) || key.isEmpty())) {
            toast("线上转写需要有效的 HTTPS 接口地址和 API Key"); return;
        }
        getPreferences(0).edit().putString("endpoint", endpoint).apply();
        InputChoice choice = (InputChoice) deviceSpinner.getSelectedItem();
        pendingStart = new Intent(this, RecordingService.class).setAction(RecordingService.ACTION_START)
                .putExtra("title", title).putExtra("deviceId", choice == null ? -1 : choice.id)
                .putExtra("cloud", cloud).putExtra("endpoint", endpoint).putExtra("key", key);
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 41); return;
        }
        beginRecording();
    }

    private void beginRecording() {
        if (pendingStart == null) return;
        try {
            startForegroundService(pendingStart);
            pendingStart = null;
            startedAt = System.currentTimeMillis();
            mainButton.setText("结束并保存");
            statusText.setText("正在请求音频输入…");
            transcriptText.setText(cloudSwitch.isChecked() ? "等待首段转写…" : "仅录音模式；可在历史会议导出 WAV。");
        } catch (Exception e) { toast("无法开始录音：" + e.getMessage()); }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(requestCode, permissions, grants);
        if (requestCode == 41) {
            if (grants.length > 0 && grants[0] == PackageManager.PERMISSION_GRANTED) beginRecording();
            else { pendingStart = null; toast("需要麦克风权限才能录制"); }
        }
    }

    private File root() {
        File dir = getExternalFilesDir("Meetings");
        return dir == null ? new File(getFilesDir(), "Meetings") : dir;
    }

    private void refreshHistory() {
        historyList.removeAllViews();
        File[] folders = root().listFiles(File::isDirectory);
        if (folders == null || folders.length == 0) {
            historyList.addView(text("还没有会议记录", 14, MUTED, false)); return;
        }
        Arrays.sort(folders, Comparator.comparing(File::getName).reversed());
        for (int i = 0; i < Math.min(folders.length, 20); i++) {
            File folder = folders[i];
            TextView row = text(folder.getName().replace('_', ' '), 14, NAVY, false);
            row.setPadding(dp(14), dp(12), dp(14), dp(12));
            row.setBackground(round(0xfff1f5f3, 12));
            row.setOnClickListener(v -> { selectedFolder = folder; updateSelection(); });
            historyList.addView(row, matchWrapWithTop(7));
        }
        if (selectedFolder == null || !selectedFolder.exists()) selectedFolder = folders[0];
        updateSelection();
    }

    private void updateSelection() {
        boolean selected = selectedFolder != null && selectedFolder.isDirectory();
        if (selectedText != null) selectedText.setText(selected ? "已选：" + selectedFolder.getName() : "选择一场会议以导出或生成纪要");
        if (exportAudioButton != null) exportAudioButton.setEnabled(selected);
        if (exportTextButton != null) exportTextButton.setEnabled(selected);
        if (summaryButton != null) summaryButton.setEnabled(selected);
    }

    private void exportFile(String name, String mime) {
        if (selectedFolder == null) return;
        pendingExport = new File(selectedFolder, name);
        if (!pendingExport.isFile()) { toast("文件尚未生成"); return; }
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT).setType(mime)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .putExtra(Intent.EXTRA_TITLE, selectedFolder.getName() + "-" + name);
        startActivityForResult(intent, 51);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != 51 || resultCode != RESULT_OK || data == null || data.getData() == null || pendingExport == null) return;
        try (FileInputStream in = new FileInputStream(pendingExport);
             OutputStream out = getContentResolver().openOutputStream(data.getData())) {
            if (out == null) throw new java.io.IOException("无法打开导出位置");
            byte[] buffer = new byte[65536]; int count;
            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            toast("导出完成");
        } catch (Exception e) { toast("导出失败：" + e.getMessage()); }
        pendingExport = null;
    }

    private void summarize() {
        if (selectedFolder == null) return;
        String endpoint = endpointInput.getText().toString().trim();
        String key = keyInput.getText().toString().trim();
        if (!QwenClient.validEndpoint(endpoint) || key.isEmpty()) {
            toast("生成纪要需要填写线上模型接口和 API Key"); return;
        }
        File folder = selectedFolder;
        summaryButton.setEnabled(false); summaryButton.setText("正在生成纪要…");
        new Thread(() -> {
            try {
                File transcript = new File(folder, "transcript.txt");
                String source = new String(Files.readAllBytes(transcript.toPath()), StandardCharsets.UTF_8);
                String result = QwenClient.summarize(endpoint, key, folder.getName(), source);
                File target = new File(folder, "summary.md");
                int version = 2;
                while (target.exists()) target = new File(folder, "summary-v" + version++ + ".md");
                Files.write(target.toPath(), result.getBytes(StandardCharsets.UTF_8));
                runOnUiThread(() -> toast("纪要已保存"));
            } catch (Exception e) { runOnUiThread(() -> toast("纪要失败：" + e.getMessage())); }
            finally { runOnUiThread(() -> { summaryButton.setEnabled(true); summaryButton.setText("生成中文纪要"); }); }
        }, "meeting-summary").start();
    }

    @Override protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(RecordingService.ACTION_EVENT);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(receiver, filter);
        if (mainButton != null) mainButton.setText(RecordingService.active ? "结束并保存" : "开始录制");
        refreshHistory();
    }
    @Override protected void onStop() { unregisterReceiver(receiver); super.onStop(); }

    private LinearLayout vertical() { LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); return box; }
    private LinearLayout card(LinearLayout parent, String heading) {
        LinearLayout box = vertical(); box.setPadding(dp(20), dp(18), dp(20), dp(20));
        box.setBackground(round(Color.WHITE, 20));
        parent.addView(box, matchWrapWithTop(14));
        TextView title = text(heading, 17, NAVY, true); title.setPadding(0, 0, 0, dp(16));
        box.addView(title); return box;
    }
    private void label(LinearLayout parent, String value) {
        TextView view = text(value, 13, MUTED, true); view.setPadding(0, dp(16), 0, dp(3)); parent.addView(view);
    }
    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD); return view;
    }
    private EditText input(String hint, boolean secret) {
        EditText view = new EditText(this); view.setSingleLine(true); view.setTextSize(14);
        view.setHint(hint); view.setPadding(dp(14), 0, dp(14), 0); view.setTextColor(NAVY);
        if (secret) view.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        view.setBackground(round(0xfff2f5f5, 12)); view.setMinHeight(dp(50)); return view;
    }
    private Button secondary(String value) {
        Button button = new Button(this); button.setAllCaps(false); button.setText(value);
        button.setTextSize(14); button.setTextColor(TEAL); button.setBackground(round(0xffe9f5f2, 12));
        button.setMinHeight(dp(46)); return button;
    }
    private GradientDrawable round(int color, int radius) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(radius)); return shape;
    }
    private LinearLayout.LayoutParams matchWrap() { return new LinearLayout.LayoutParams(-1, -2); }
    private LinearLayout.LayoutParams matchWrapWithTop(int margin) {
        LinearLayout.LayoutParams params = matchWrap(); params.topMargin = dp(margin); return params;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void toast(String message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
}
