package cn.meetingassistant.device;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.IBinder;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

public final class RecordingService extends Service {
    static final String ACTION_START = "cn.meetingassistant.device.START";
    static final String ACTION_STOP = "cn.meetingassistant.device.STOP";
    static final String ACTION_EVENT = "cn.meetingassistant.device.EVENT";
    static volatile boolean active = false;
    static volatile String currentFolder = "";
    private volatile boolean keepRecording;
    private Thread worker;
    private AudioRecord recorder;
    private static final int CHANNELS = 1;
    private static final String CHANNEL_ID = "meeting_recording";

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        if (ACTION_STOP.equals(intent.getAction())) {
            keepRecording = false;
            event("stopping", "正在保存录音与剩余转写…");
            return START_NOT_STICKY;
        }
        if (!ACTION_START.equals(intent.getAction()) || active) return START_NOT_STICKY;
        ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(
                new NotificationChannel(CHANNEL_ID, "会议录音", NotificationManager.IMPORTANCE_LOW));
        Notification notification = notification();
        if (Build.VERSION.SDK_INT >= 29) startForeground(71, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        else startForeground(71, notification);
        active = true;
        keepRecording = true;
        String title = intent.getStringExtra("title");
        int deviceId = intent.getIntExtra("deviceId", -1);
        String endpoint = intent.getStringExtra("endpoint");
        String key = intent.getStringExtra("key");
        boolean cloud = intent.getBooleanExtra("cloud", false);
        worker = new Thread(() -> record(title, deviceId, endpoint, key, cloud), "meeting-recorder");
        worker.start();
        return START_NOT_STICKY;
    }

    private Notification notification() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("见声 · 正在录制会议")
                .setContentText("音频仅来自选定的输入设备")
                .setContentIntent(pending).setOngoing(true).build();
    }

    private void record(String title, int deviceId, String endpoint, String key, boolean cloud) {
        ThreadPoolExecutor asr = new ThreadPoolExecutor(1, 1, 0, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(8));
        File folder = null;
        int sampleRate = 16000;
        try {
            int minimum = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT);
            if (minimum <= 0) {
                sampleRate = 48000;
                minimum = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT);
            }
            if (minimum <= 0) throw new IOException("设备不支持 PCM 麦克风录制");
            int bufferSize = Math.max(minimum * 2, sampleRate / 2);
            AudioRecord.Builder builder = new AudioRecord.Builder()
                    .setAudioSource(MediaRecorder.AudioSource.MIC)
                    .setAudioFormat(new AudioFormat.Builder().setSampleRate(sampleRate)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_IN_MONO).build())
                    .setBufferSizeInBytes(bufferSize);
            recorder = builder.build();
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new IOException("无法打开麦克风，可能已被会议应用占用");
            if (deviceId >= 0) {
                AudioManager manager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
                boolean found = false;
                for (AudioDeviceInfo device : manager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                    if (device.getId() == deviceId) { found = recorder.setPreferredDevice(device); break; }
                }
                if (!found) throw new IOException("所选音频输入已断开，请重新选择");
            }
            File root = getExternalFilesDir("Meetings");
            if (root == null) root = new File(getFilesDir(), "Meetings");
            if (!root.exists() && !root.mkdirs()) throw new IOException("无法创建会议目录");
            String stamp = new SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(new Date());
            String safe = title == null ? "会议" : title.trim().replaceAll("[^\\p{L}\\p{N}_-]", "-");
            if (safe.isEmpty()) safe = "会议";
            if (safe.length() > 36) safe = safe.substring(0, 36);
            folder = new File(root, stamp + "_" + safe);
            if (!folder.mkdir()) throw new IOException("无法创建会议文件夹");
            currentFolder = folder.getAbsolutePath();
            File audioFile = new File(folder, "audio.wav");
            File transcriptFile = new File(folder, "transcript.txt");
            if (!transcriptFile.createNewFile()) throw new IOException("无法创建转写文件");
            writeText(new File(folder, "source.txt"), "输入设备录音；Android 通用 API 不保证录到会议应用的远端音频。\n");
            final File asrFolder = folder;
            final int rate = sampleRate;
            byte[] buffer = new byte[bufferSize];
            ByteArrayOutputStream chunk = new ByteArrayOutputStream(rate * 2 * 16);
            long chunkBytes = 0;
            int chunkIndex = 0;
            long nextHeaderAt = rate * 2L * 2;
            long silentBytes = 0;
            boolean silenceReported = false;
            try (RandomAccessFile wav = new RandomAccessFile(audioFile, "rw")) {
                wav.write(new byte[44]);
                recorder.startRecording();
                if (recorder.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) throw new IOException("录音启动失败");
                AudioDeviceInfo routed = recorder.getRoutedDevice();
                if (deviceId >= 0 && routed != null && routed.getId() != deviceId)
                    event("warning", "设备没有路由到所选输入，已停止以避免录错来源");
                if (deviceId >= 0 && routed != null && routed.getId() != deviceId)
                    throw new IOException("输入设备路由与选择不一致");
                event("recording", "正在录制 · " + rate / 1000 + " kHz · 单声道");
                while (keepRecording) {
                    int count = recorder.read(buffer, 0, buffer.length, AudioRecord.READ_BLOCKING);
                    if (count < 0) throw new IOException("音频读取失败：" + count);
                    if (count == 0) continue;
                    wav.write(buffer, 0, count);
                    long recordedBytes = wav.length() - 44;
                    if (recordedBytes >= nextHeaderAt) {
                        writeWavHeader(wav, recordedBytes, rate);
                        wav.seek(wav.length());
                        nextHeaderAt = recordedBytes + rate * 2L * 2;
                    }
                    boolean allZero = true;
                    for (int i = 0; i < count; i++) {
                        if (buffer[i] != 0) { allZero = false; break; }
                    }
                    silentBytes = allZero ? silentBytes + count : 0;
                    if (!silenceReported && silentBytes >= rate * 2L * 5) {
                        event("warning", "连续 5 秒全静音，请检查会议应用是否独占麦克风或音频输入路由");
                        silenceReported = true;
                    }
                    if (cloud) {
                        chunk.write(buffer, 0, count);
                        chunkBytes += count;
                        if (chunkBytes >= rate * 2L * 15) {
                            submitAsr(asr, asrFolder, endpoint, key, chunk.toByteArray(), rate, ++chunkIndex);
                            chunk.reset(); chunkBytes = 0;
                        }
                    }
                }
                if (cloud && chunkBytes >= rate * 2L) submitAsr(asr, asrFolder, endpoint, key,
                        chunk.toByteArray(), rate, ++chunkIndex);
            } finally {
                try (RandomAccessFile wav = new RandomAccessFile(audioFile, "rw")) {
                    if (wav.length() >= 44) writeWavHeader(wav, Math.max(0, wav.length() - 44), rate);
                }
            }
            if (recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) recorder.stop();
            recorder.release(); recorder = null;
            event("stopping", "录音已保存，正在完成转写…");
            asr.shutdown();
            while (!asr.awaitTermination(2, TimeUnit.SECONDS)) { /* foreground service remains visible */ }
            event("complete", "录音已保存，可在历史会议中导出 · " + folder.getName());
        } catch (Exception error) {
            asr.shutdownNow();
            event("error", error.getMessage() == null ? "录音失败" : error.getMessage());
        } finally {
            if (recorder != null) {
                try { if (recorder.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) recorder.stop(); }
                catch (Exception ignored) {}
                recorder.release(); recorder = null;
            }
            active = false;
            keepRecording = false;
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
        }
    }

    private void submitAsr(ThreadPoolExecutor queue, File folder, String endpoint, String key,
                           byte[] pcm, int rate, int index) {
        try {
            queue.execute(() -> {
                try {
                    byte[] wav = toWav(pcm, rate);
                    String text = QwenClient.transcribe(endpoint, key, wav);
                    synchronized (this) {
                        appendText(new File(folder, "transcript.txt"), "[" + (index * 15 - 15) + "s] " + text + "\n");
                    }
                    event("transcript", text);
                } catch (Exception e) {
                    try { appendText(new File(folder, "transcript.errors.txt"),
                            "第 " + index + " 段：" + e.getMessage() + "\n"); }
                    catch (IOException ignored) {}
                    event("warning", "第 " + index + " 段转写失败；录音已保留");
                }
            });
        } catch (RuntimeException e) {
            try { appendText(new File(folder, "transcript.errors.txt"),
                    "第 " + index + " 段：转写队列已满\n"); } catch (IOException ignored) {}
            event("warning", "转写跟不上录音，部分文字段未提交；录音完整保留");
        }
    }

    static byte[] toWav(byte[] pcm, int sampleRate) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(pcm.length + 44);
        byte[] header = new byte[44];
        writeHeader(header, pcm.length, sampleRate);
        bytes.write(header); bytes.write(pcm);
        return bytes.toByteArray();
    }

    private static void writeWavHeader(RandomAccessFile wav, long dataBytes, int sampleRate) throws IOException {
        if (dataBytes > 0xffffffffL - 36) throw new IOException("录音超过标准 WAV 的 4 GiB 限制");
        byte[] header = new byte[44]; writeHeader(header, dataBytes, sampleRate);
        wav.seek(0); wav.write(header);
    }

    private static void writeHeader(byte[] h, long size, int rate) {
        put(h, 0, "RIFF"); le(h, 4, size + 36, 4); put(h, 8, "WAVEfmt ");
        le(h, 16, 16, 4); le(h, 20, 1, 2); le(h, 22, CHANNELS, 2);
        le(h, 24, rate, 4); le(h, 28, rate * CHANNELS * 2L, 4);
        le(h, 32, CHANNELS * 2, 2); le(h, 34, 16, 2); put(h, 36, "data"); le(h, 40, size, 4);
    }

    private static void put(byte[] out, int offset, String value) {
        byte[] data = value.getBytes(StandardCharsets.US_ASCII);
        System.arraycopy(data, 0, out, offset, data.length);
    }
    private static void le(byte[] out, int offset, long value, int count) {
        for (int i = 0; i < count; i++) out[offset + i] = (byte) (value >> (8 * i));
    }
    private static void writeText(File file, String text) throws IOException {
        try (FileOutputStream stream = new FileOutputStream(file, false)) {
            stream.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }
    private static void appendText(File file, String text) throws IOException {
        try (FileOutputStream stream = new FileOutputStream(file, true)) {
            stream.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }
    private void event(String state, String message) {
        Intent event = new Intent(ACTION_EVENT).setPackage(getPackageName());
        event.putExtra("state", state).putExtra("message", message);
        event.putExtra("folder", currentFolder);
        sendBroadcast(event);
    }

    @Override public void onDestroy() {
        keepRecording = false;
        super.onDestroy();
    }
}
