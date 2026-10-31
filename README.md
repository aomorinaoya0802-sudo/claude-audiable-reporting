# aivis-speech

Claude Code の返答と、作業の進捗を、ローカルの [AivisSpeech](https://aivis-project.com/) で日本語に読み上げる mod です。読んでいる位置を、画面下の帯と本文でハイライトします。音声の合成と再生はすべて自分のPCの中で行い、読み上げる文章を外部のサービスには送りません(「何のために」を補う設定と英語の翻訳は、オンのときだけ、作業の概要を Claude のモデル(Haiku)に渡します。設定画面でオフにできます)。

## インストール

Claude Code のターミナルで、次の一行を入力します(`y` でマーケットプレイスを追加し、スコープを選びます)。

```
/plugin install aivis-speech --marketplace aomorinaoya0802-sudo/claude-audiable-reporting
```

シェルからは、次のとおりです。

```
claude plugin install aivis-speech --marketplace aomorinaoya0802-sudo/claude-audiable-reporting
```

デスクトップアプリでは `/plugin` が使えません。プロンプト欄の + から Plugins → Add marketplace に `aomorinaoya0802-sudo/claude-audiable-reporting` を入れて、Add plugin してください。

入れたあとの準備は、下の「初期設定」にあります(`/aivis setup` から)。

## できること

- **最終回答の読み上げ**: 返答が出そろったら読み始めます。コードブロック・URL・パス・記号は、読み上げに向く形に整えます。次のプロンプトを送ると止まります。
- **作業の進捗の読み上げ**: ツールを実行するたびに、何をしているかを日本語で読みます。
  - 「YouTubeで『限界飯』を検索しています」「Wikipediaの記事を読んでいます」「register.tsx を編集しています」「Gitでコミットしています」など。
  - 「何のために」は、設定がオンのとき、Claude のモデル(Haiku)が短い一文にして補います(1回ごとに少額のトークンを使います)。オフなら定型の言い回しです。英語の文は日本語に訳して読みます。
  - 終わったら「コマンドを2つ実行しました」「YouTubeを1回使いました」のように集計して読みます。エラーも読みます。
  - 待ち時間の表示(Churning… など)は日本語にし、長引くと「引き続き作業中です」と言います。
- **質問の読み上げ**: Claude が質問(AskUserQuestion)をするときは、効果音を鳴らしてから、別の声で「質問があります」と言い、質問と選択肢を読みます。声と効果音は設定画面で変えられます。
- **読んでいる位置のハイライト**: 画面下の字幕帯では、実際の音声の無音区間から作った時刻表に合わせて、読んでいる位置まで色が伸びます。本文では、読んでいる文をコード表示にします。画面の幅や高さは変わりません。
- **設定画面**: 声(話者・スタイル)、音量、速さ、高さ、抑揚、テンポ、ハイライトの色とずれ補正、読む長さ、オン/オフ、読みの辞書を、画面で変えられます。
- **読みの辞書**: 読み間違いを「語=よみ」で直せます。内蔵の辞書には、Claude Code・YouTube・VTuber・GitHub などが入っています。

## 必要なもの

- Windows 10/11
- Python 3.10 以上(`python` コマンドで動くこと)
- AivisSpeech Engine(音声合成。下の初期設定で用意できます)
- Chrome(設定画面を開くときに使います。無ければ、既定のブラウザで開きます)

## 初期設定

mod を入れたら、Claude Code で次の順に実行します。

```
/aivis setup          Python ライブラリ(sounddevice, numpy)・初期設定・設定画面のショートカットを用意します
/aivis doctor         環境を診断します(何も変更しません)
/aivis setup engine   AivisSpeech Engine が無いときだけ。公式配布(1.2.0)から約220MBをダウンロードし、約0.8GBに展開します
/aivis test           テスト音声を鳴らします(初回はエンジンの起動に数十秒かかります)
```

`/aivis setup` は、`~/.claude/aivis-speech/` の下に設定と Python ライブラリを置きます。mod のフォルダには何も書き込みません。すでに AivisSpeech を入れている場合は、`setup engine` は不要です。エンジンは次の順で探します。

1. 環境変数 `AIVIS_ENGINE_EXE`
2. `~/.claude/aivis-speech/settings.json` の `engine_path`
3. `~/.claude/aivis-speech/engine`(`setup engine` が展開する場所)
4. `%LOCALAPPDATA%\Programs\AivisSpeech`

## コマンド

| コマンド | 内容 |
| --- | --- |
| `/aivis on` / `off` | 読み上げのオン/オフ(ステータスラインに 🔊) |
| `/aivis stop` | 読み上げを止める |
| `/aivis test` | テスト音声 |
| `/aivis settings` | 設定画面を開く |
| `/aivis log` | 直近の判断(読んだ・飛ばした理由)を見る |
| `/aivis doctor` | 環境の診断 |
| `/aivis setup` / `setup engine` | 初期設定 / エンジンの用意 |

## 環境を再現する

初期の設定は `defaults/settings.json` にあります。`/aivis setup` が、設定ファイルが無いときだけこれをコピーします。声は AivisSpeech Engine に付属のものです(`doctor` の「声」の一覧で、設定した声があるか確かめられます)。別のPCで同じ声・速さ・色にしたいときは、`~/.claude/aivis-speech/settings.json` と `readings.json` を、そのPCの同じ場所にコピーします。

## 仕組みと注意

- 読み上げは、独立した再生プロセス(`scripts/speak.py`)が行います。Claude Code の mod が再読み込みされても、再生は途切れません。
- 合成は1文字あたり約0.06秒かかるので、最初の文だけ短く切って出だしを速くし、2つ目以降は文を丸ごと読みます。
- 画面は動作の邪魔をしないよう、字幕帯は常に2行ぶんの固定の高さです。要らなければ設定画面でオフにできます。
- 本文のハイライトの色は、Claude Code のテーマ(`/theme`)のコード表示の色です。色を選べるのは字幕帯だけです。
- 設定画面は、ローカルの小さなサーバー(127.0.0.1:18890)を使います。画面を閉じると自動で終了します。
- 読み上げが途中で止まったときは、`/aivis log` の「読み上げ終了」の行に理由が残ります。

## ライセンス

MIT
