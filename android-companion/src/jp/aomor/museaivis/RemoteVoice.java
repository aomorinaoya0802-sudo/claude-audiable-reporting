package jp.aomor.museaivis;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class RemoteVoice {
    static final String HOST = "http://100.114.247.101:18891";
    static final String PREFS = "muse_aivis";
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static MediaPlayer player;
    private static File playingFile;

    interface Result { void done(boolean ok, String message); }

    static SharedPreferences prefs(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean configured(Context ctx) {
        return !prefs(ctx).getString("token", "").trim().isEmpty();
    }

    static void speak(Context ctx, String value, Result callback) {
        final Context app = ctx.getApplicationContext();
        final String text = value == null ? "" : value.trim();
        if (text.isEmpty() || !configured(app)) {
            complete(callback, false, "PC接続用トークンの設定が必要です");
            return;
        }
        WORKER.execute(() -> {
            HttpURLConnection connection = null;
            File temp = null;
            try {
                String token = prefs(app).getString("token", "").trim();
                connection = (HttpURLConnection) new URL(HOST + "/synthesize").openConnection();
                connection.setRequestMethod("POST");
                connection.setConnectTimeout(9000);
                connection.setReadTimeout(100000);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setDoOutput(true);
                byte[] payload = new JSONObject().put("text", text.substring(0, Math.min(900, text.length())))
                        .toString().getBytes(StandardCharsets.UTF_8);
                try (OutputStream stream = connection.getOutputStream()) { stream.write(payload); }
                int status = connection.getResponseCode();
                if (status != 200) throw new IllegalStateException("音声サーバー HTTP " + status);
                temp = File.createTempFile("muse-aivis-", ".wav", app.getCacheDir());
                try (InputStream input = connection.getInputStream();
                     FileOutputStream output = new FileOutputStream(temp)) {
                    byte[] bytes = new byte[16384];
                    int length;
                    int total = 0;
                    while ((length = input.read(bytes)) != -1) {
                        total += length;
                        if (total > 20000000) throw new IllegalStateException("音声ファイルが大きすぎます");
                        output.write(bytes, 0, length);
                    }
                }
                File wav = temp;
                MAIN.post(() -> {
                    try {
                        stopPlayback();
                        final MediaPlayer mp = new MediaPlayer();
                        player = mp;
                        playingFile = wav;
                        mp.setAudioAttributes(new AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_MEDIA)
                                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
                        mp.setDataSource(wav.getAbsolutePath());
                        mp.setOnCompletionListener(v -> stopPlayback());
                        mp.setOnErrorListener((v, what, extra) -> { stopPlayback(); return true; });
                        mp.prepare();
                        mp.start();
                        complete(callback, true, "AivisSpeechで再生を開始しました");
                    } catch (Exception e) {
                        stopPlayback();
                        complete(callback, false, "再生失敗: " + e.getClass().getSimpleName());
                    }
                });
                temp = null;
            } catch (Exception e) {
                complete(callback, false, "送信失敗: " + e.getMessage());
            } finally {
                if (connection != null) connection.disconnect();
                if (temp != null) temp.delete();
            }
        });
    }

    static void check(Context ctx, Result callback) {
        final Context app = ctx.getApplicationContext();
        WORKER.execute(() -> {
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(HOST + "/health").openConnection();
                conn.setRequestProperty("Authorization",
                        "Bearer " + prefs(app).getString("token", "").trim());
                conn.setConnectTimeout(9000);
                conn.setReadTimeout(9000);
                int status = conn.getResponseCode();
                if (status == 200) {
                    ByteArrayOutputStream out = new ByteArrayOutputStream();
                    try (InputStream input = conn.getInputStream()) {
                        byte[] buf = new byte[4096]; int n;
                        while ((n = input.read(buf)) > 0 && out.size() < 65536) out.write(buf, 0, n);
                    }
                    String result = out.toString("UTF-8");
                    JSONObject json = new JSONObject(result);
                    complete(callback, true, "接続済み。Engine起動: " + json.optBoolean("engine_running"));
                } else {
                    complete(callback, false, "PCへの接続: HTTP " + status);
                }
            } catch (Exception e) {
                complete(callback, false, "接続不可: " + e.getClass().getSimpleName());
            } finally {
                if (conn != null) conn.disconnect();
            }
        });
    }

    private static void complete(Result callback, boolean ok, String message) {
        if (callback != null) MAIN.post(() -> callback.done(ok, message));
    }

    static void stopPlayback() {
        if (player != null) {
            try { player.stop(); } catch (Exception ignored) {}
            try { player.release(); } catch (Exception ignored) {}
            player = null;
        }
        if (playingFile != null) {
            playingFile.delete();
            playingFile = null;
        }
    }
}
