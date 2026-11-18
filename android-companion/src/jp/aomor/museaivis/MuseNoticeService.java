package jp.aomor.museaivis;

import android.app.Notification;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

public class MuseNoticeService extends NotificationListenerService {
    private String last = "";
    private long lastWhen = 0;

    @Override public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null || !"com.facebook.aura".equals(sbn.getPackageName())) return;
        if (!RemoteVoice.prefs(this).getBoolean("notices", true) || !RemoteVoice.configured(this)) return;
        Notification note = sbn.getNotification();
        if (note == null || (note.flags & Notification.FLAG_GROUP_SUMMARY) != 0) return;
        Bundle extra = note.extras;
        if (extra == null) return;
        CharSequence large = extra.getCharSequence(Notification.EXTRA_BIG_TEXT);
        CharSequence normal = extra.getCharSequence(Notification.EXTRA_TEXT);
        CharSequence title = extra.getCharSequence(Notification.EXTRA_TITLE);
        String body = clean(large != null ? large : normal);
        if (body.length() < 2) return;
        String heading = clean(title);
        if (!heading.isEmpty() && body.equals(heading)) heading = "";
        String signature = sbn.getKey() + "|" + body;
        long now = System.currentTimeMillis();
        if (signature.equals(last) && now - lastWhen < 60000) return;
        last = signature;
        lastWhen = now;
        String utterance = "Museからのお知らせです。" + body;
        RemoteVoice.speak(this, utterance, null);
    }

    private static String clean(CharSequence source) {
        if (source == null) return "";
        return source.toString().trim().replaceAll("\\s+", " ");
    }
}
