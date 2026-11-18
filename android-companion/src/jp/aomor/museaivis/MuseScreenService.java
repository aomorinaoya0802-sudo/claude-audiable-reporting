package jp.aomor.museaivis;

import android.accessibilityservice.AccessibilityService;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Locale;

public class MuseScreenService extends AccessibilityService {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable scan = this::readNewAssistantText;
    private String last = "";
    private long lastWhen = 0;

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || event.getPackageName() == null
                || !"com.facebook.aura".contentEquals(event.getPackageName())) return;
        if (!RemoteVoice.prefs(this).getBoolean("screen", false) || !RemoteVoice.configured(this)) return;
        handler.removeCallbacks(scan);
        handler.postDelayed(scan, 1700);
    }

    private void readNewAssistantText() {
        if (!RemoteVoice.prefs(this).getBoolean("screen", false)) return;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null || root.getPackageName() == null
                || !"com.facebook.aura".contentEquals(root.getPackageName())) return;
        Deque<AccessibilityNodeInfo> stack = new ArrayDeque<>();
        stack.add(root);
        String candidate = "";
        int scanned = 0;
        while (!stack.isEmpty() && scanned++ < 400) {
            AccessibilityNodeInfo node = stack.removeFirst();
            CharSequence desc = node.getContentDescription();
            String role = desc == null ? "" : desc.toString().toLowerCase(Locale.ROOT);
            String res = node.getViewIdResourceName();
            if (res != null) role += " " + res.toLowerCase(Locale.ROOT);
            CharSequence content = node.getText();
            // A response must be explicitly identified as an AI response by the app's accessibility metadata.
            boolean aiLabel = role.contains("assistant_message")
                    || role.contains("muse_response")
                    || role.contains("muse reply")
                    || role.contains("museの返信")
                    || role.contains("ai response");
            if (aiLabel && !node.isEditable() && content != null) {
                String s = content.toString().trim();
                if (s.length() > 2) candidate = s;
            }
            int count = node.getChildCount();
            for (int i = 0; i < count; i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) stack.add(child);
            }
        }
        candidate = candidate.replaceAll("\\s+", " ");
        if (candidate.length() > 900) candidate = candidate.substring(0, 900);
        long now = System.currentTimeMillis();
        if (candidate.isEmpty() || candidate.equals(last) || now - lastWhen < 3000) return;
        last = candidate;
        lastWhen = now;
        RemoteVoice.speak(this, candidate, null);
    }

    @Override public void onInterrupt() { handler.removeCallbacks(scan); }
    @Override public void onDestroy() { handler.removeCallbacks(scan); super.onDestroy(); }
}
