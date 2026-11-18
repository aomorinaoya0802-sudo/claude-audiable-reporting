
# Meta Muse Android × 既存AivisSpeech 音声読み上げ

## 目的と現在の実装

Meta MuseのAndroidアプリ（`com.facebook.aura`）の発話を、既存Windows AivisSpeech Engineの話者・話速・抑揚・読み辞書で合成し、Androidのスピーカーから再生する非公式の補助機能です。Museの公式音声モード自体は差し替えていません。APIキーは不要です。

- Museから新しい通知が届く場合: Androidの`NotificationListenerService`を経由して、**通知本文に含まれる文字列**を自動読み上げします。
- Museを開いている場合: `AccessibilityService`を利用して、「AI返答」と明記されたノードの文字列だけを読み上げます。アプリ内部のアクセシビリティ構造は未検証です。初期設定はOFFです。
- Muse画面にある文章を本アプリへ共有した場合: その文章だけを読み上げられます。
- Windowsは音声合成のみ、実際に音を出すのはスマホです。

## PC側の設定

音声合成の正本: `C:\Users\aomor\repos\claude-audiable-reporting`。

既存設定: `C:\Users\aomor\.claude\aivis-speech\settings.json`。

1. PCでTailscaleを有効にしてください。今回確認したPCのTailnet IPは`100.114.247.101`です。
2. リポジトリで`python scripts/muse_bridge.py`を起動します。サーバーは`100.114.247.101:18891`に限定して待ち受けます。公衆インターネットへの公開はしません。
3. 初回起動時にトークンが`C:\Users\aomor\.claude\aivis-speech\muse-mobile-token`へ生成されます。スマホの入力欄へ一度貼り付けます。トークンをGit、Notion、メールや公開Webページへ載せないでください。
4. エンジンが停止中でも、音声リクエスト時に既存の`speak.py`がEngineを起動します。
5. 認証されたGET `/health`とPOST `/synthesize`だけを提供します。発話本文はこのPCで処理し、APIとして外部に転送しません。

## Android側の設定

Android用のAPKは`android-companion/build/MuseAivis.apk`です。コード正本は`android-companion/src/`です。

1. Androidに**Tailscale**を入れ、PCと同じアカウント/Tailnetに接続します。端末でVPN接続を有効にしてください。
2. APKをインストールします。Androidのアプリ外インストール許可が必要な場合があります。
3. 本アプリを開き、上記トークンを貼り付けて「接続設定を保存」を押します。
4. 「PC接続確認」→「AivisSpeechでテスト再生」を実行します。
5. 「通知アクセスの設定を開く」から、このアプリの通知アクセスを明示的に許可します。Museから新着通知が届くと、その通知本文を読み上げます。
6. アプリ内の自動読み上げは、設定画面から試験用スイッチを有効にし、「アクセシビリティの設定を開く」から許可します。Museの返信ラベルが取得できるときだけ動作します。
7. Muse内のメッセージを手動で読みたい場合は、共有機能で「Muse音声補助 AivisSpeech」へテキストを送ってください。

## 動作と安全上の制約

- **Android実機へはまだインストールしていません**。今回の接続でAndroid端末がADBに表示されなかったため、通知・アプリ内返信の検証は残っています。
- Androidの通知本文に全文が含まれない場合、その全文を取得することはできません。通知そのものが届かない時は音声も出ません。
- アクセシビリティによる画面検出は、Museの内部UIに依存します。未確認のUI情報を推測して全画面読み上げしません。
- スマホにTailscaleがなくPCとつながらない場合、Windowsが停止している場合は再生できません。
- 通信はTailscaleのWireGuard暗号化トンネル上のプライベートアドレスへ固定しています。トークン認証も要求します。公衆向けHTTP公開やTailscale Funnelは使用しません。
- Android側の通知・アクセシビリティ権限は、ユーザーが端末の設定で明示的に有効化する必要があります。通知権限は他アプリの通知もOS上で許可対象になりますが、実装はMuseのパッケージ名`com.facebook.aura`だけを処理します。
- ユーザーが手動で設定・権限を解除できます。APKはGoogle Playから配布する公式アプリではなく、Android SDKでビルドしたテスト用アプリです。

## 検証

`python -m unittest discover -s tests -p test_muse_bridge.py -v`  
`powershell -NoProfile -File android-companion/build.ps1`  
AndroidビルドではAPK v2/v3署名とパッケージ情報を確認します。

## 関連資料

- Meta Muse Android: https://play.google.com/store/apps/details?id=com.facebook.aura
- Android通知取得: https://developer.android.com/reference/android/service/notification/NotificationListenerService
- Androidアクセシビリティ: https://developer.android.com/reference/android/accessibilityservice/AccessibilityService
- Tailscale: https://tailscale.com
- 記録担当AI: ChatGPT / 2026-11-19
