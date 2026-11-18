package jp.aomor.museaivis;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

public class MainActivity extends Activity {
    private TextView status;
    private EditText token;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ScrollView scroller = new ScrollView(this);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(28, 26, 28, 28);
        scroller.addView(page);
        setContentView(scroller);

        TextView header = text("Muse × AivisSpeech", 23);
        header.setTextColor(Color.rgb(22, 58, 91));
        page.addView(header);
        page.addView(text("AndroidのMuseから届く通知と、Muse画面で識別できる新しいAI返答を、既存のAivisSpeechの声で読み上げます。", 15));
        page.addView(text("送受信はご自身のTailscaleの暗号化トンネル内のPCに限定します。PCが動作中で、スマホにもTailscaleを接続する必要があります。", 13));

        page.addView(text("音声合成PC（固定）", 15));
        page.addView(text(RemoteVoice.HOST, 13));

        page.addView(text("接続用トークン（PC側の muse-mobile-token）", 15));
        token = new EditText(this);
        token.setHint("PCで生成されたトークンを一度貼り付けてください");
        token.setSingleLine(true);
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        token.setText(RemoteVoice.prefs(this).getString("token", ""));
        page.addView(token);

        Button save = button("接続設定を保存");
        save.setOnClickListener(v -> {
            RemoteVoice.prefs(this).edit().putString("token", token.getText().toString().trim()).apply();
            report("端末内に保存しました。");
        });
        page.addView(save);

        Button check = button("PC接続確認");
        check.setOnClickListener(v -> RemoteVoice.check(this, (ok,msg) -> report(msg)));
        page.addView(check);

        Button test = button("AivisSpeechでテスト再生");
        test.setOnClickListener(v -> {
            report("音声合成をリクエストしています。");
            RemoteVoice.speak(this, "MuseとAivisSpeechの音声接続テストです。", (ok,msg) -> report(msg));
        });
        page.addView(test);

        Switch notice = new Switch(this);
        notice.setText("Museの新しい通知を自動読み上げ");
        notice.setTextSize(15);
        notice.setChecked(RemoteVoice.prefs(this).getBoolean("notices", true));
        notice.setOnCheckedChangeListener((v,checked) ->
            RemoteVoice.prefs(this).edit().putBoolean("notices", checked).apply());
        page.addView(notice);
        Button notificationPermission = button("通知アクセスの設定を開く");
        notificationPermission.setOnClickListener(v ->
            startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        page.addView(notificationPermission);
        page.addView(text("Museの通知内容が表示される場合だけ、通知本文を読みます。端末の集中モードや省電力設定によって再生が抑制される場合があります。", 12));

        Switch screen = new Switch(this);
        screen.setText("Muse画面の新しいAI返答を自動読み上げ（試験機能）");
        screen.setTextSize(15);
        screen.setChecked(RemoteVoice.prefs(this).getBoolean("screen", false));
        screen.setOnCheckedChangeListener((v,checked) ->
            RemoteVoice.prefs(this).edit().putBoolean("screen", checked).apply());
        page.addView(screen);
        Button accessibilityPermission = button("アクセシビリティの設定を開く");
        accessibilityPermission.setOnClickListener(v ->
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        page.addView(accessibilityPermission);
        page.addView(text("画面内のMuse返答は、アプリが支援技術へ提供するAI返信ラベルがある場合に限って検出します。未検証のため初期設定はOFFです。", 12));

        page.addView(text("Museのメッセージを共有機能から本アプリへ送る方法も使えます。選択した文章のみ読み上げます。", 13));
        status = text("通知アクセスの有効化は端末側で必要です。", 13);
        status.setTextColor(Color.rgb(40, 85, 130));
        page.addView(status);
        handleShare(getIntent());
    }

    private TextView text(String value, int size) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setPadding(0, 12, 0, 12);
        return view;
    }

    private Button button(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setAllCaps(false);
        return button;
    }

    private void report(String value) { status.setText(value); }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleShare(intent);
    }

    private void handleShare(Intent intent) {
        if (intent != null && Intent.ACTION_SEND.equals(intent.getAction())
                && "text/plain".equals(intent.getType())) {
            CharSequence shared = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            if (shared != null && shared.length() > 0) {
                report("共有された文章を読み上げます。");
                RemoteVoice.speak(this, shared.toString(), (ok,msg) -> report(msg));
            }
        }
    }
}
