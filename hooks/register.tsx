import { atom, read, update } from 'claude-code'
import type { EngineInterface as Engine, Register } from 'claude-code'

import type { Highlight } from '../types'

// 設定は %USERPROFILE%\.claude\aivis-speech\settings.json（設定画面 settings-ui が書く）。
// 既定値と範囲は voice-bridge の Claude 用設定に合わせてある。
type Settings = {
  enabled: boolean
  progress_tts: boolean
  ai_rewrite: boolean
  highlight: boolean
  highlight_color: string
  python_path: string
  engine_path: string
  band: boolean
  inline_highlight: boolean
  sync_offset_ms: number
  max_chars: number
  volume: number
  style: number
  speed: number
  pitch: number
  intonation: number
  tempo: number
  pause: number
  fast_first: number
}

const DEFAULTS: Settings = {
  enabled: true, progress_tts: true, ai_rewrite: false, highlight: true, highlight_color: '#ffd400', python_path: '', engine_path: '', band: true, inline_highlight: true, sync_offset_ms: 100, max_chars: 1000, volume: 1.0,
  style: 888753761, speed: 1.15, pitch: 0.0, intonation: 1.1, tempo: 1.0, pause: 0.6, fast_first: 1,
}
const LIMITS: Record<string, [number, number]> = {
  max_chars: [40, 5000], sync_offset_ms: [-500, 800], volume: [0.1, 2.0], speed: [0.6, 1.8], pitch: [-0.15, 0.15],
  intonation: [0.3, 2.0], tempo: [0.0, 2.0], pause: [0.2, 1.5],
}

const highlight = atom({ plugin: 'aivis-speech', key: 'highlight' } as const, null)

const COMMENTARY_MAX_CHARS = 160 // 作業の合間のひとことは、この長さまで（文末で切る）
const REPEAT_MS = 20000 // 同じ進捗文を続けて読まない時間

// モジュールの変数（再読み込みで初期化される）
let seq = 0
let speakingId = 0
let speakingKind: Highlight['kind'] | '' = ''
let pendingQueue: string[] = [] // 読み上げ中に届いた進捗（最新の3件まで待たせる）
let lastSpokeAt = 0
let inflight = 0
const doneCounts: Partial<Record<Category, number>> = {}
const errorCats: Category[] = []
const doneMcp: Record<string, number> = {}
let keepAlive: { cancel: () => void } | undefined
let lastProgressAt = 0
let lastProgressText = ''
const decisions: string[] = []
let bandEnabled = true
let inlineEnabled = false

function parseSettings(text: string | undefined): Settings {
  const out: Settings = { ...DEFAULTS }
  let raw: Record<string, unknown> = {}
  try {
    raw = text ? JSON.parse(text) : {}
  } catch {
    raw = {}
  }
  for (const [k, [lo, hi]] of Object.entries(LIMITS)) {
    const v = Number(raw[k])
    if (raw[k] !== undefined && Number.isFinite(v)) (out as Record<string, unknown>)[k] = Math.max(lo, Math.min(hi, v))
  }
  for (const k of ['enabled', 'progress_tts', 'ai_rewrite', 'highlight', 'band', 'inline_highlight'] as const) {
    if (raw[k] !== undefined) out[k] = raw[k] !== false && raw[k] !== 0 && raw[k] !== '0'
  }
  if (raw.fast_first !== undefined) out.fast_first = raw.fast_first === 0 || raw.fast_first === false ? 0 : 1
  for (const k of ['python_path', 'engine_path'] as const) {
    if (typeof raw[k] === 'string') out[k] = raw[k] as string
  }
  if (typeof raw.highlight_color === 'string' && /^#[0-9a-fA-F]{6}$/.test(raw.highlight_color)) out.highlight_color = raw.highlight_color
  const style = Number(raw.style)
  if (raw.style !== undefined && Number.isFinite(style)) out.style = Math.trunc(style)
  return out
}

/** voice-bridge の normalize_tts 相当: コード・URL・記号を読み上げ向けに整える。 */
function toSpeech(raw: string, maxChars: number): string {
  let t = raw
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/https?:\/\/\S+/g, 'リンク')
    .replace(/[A-Za-z]:[\\/][^\s、。，,」）)』`]*/g, 'パス')
    .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, 'メールアドレス')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\|.*\|\s*$/gm, '')
    .replace(/[>#*_~|]/g, '')
    .replace(/\s*(?:→|⇒|➡|➜|->|=>)\s*/g, '、')
    .replace(/[\u{1F300}-\u{1FAFF}☀-➿️]/gu, '')
    // 箇条書きや見出しなど、句点のない行は行末で区切る（コロンは読点に）
    .replace(/[：:][ \t]*\n+/g, '、')
    .replace(/([^。！？!?、\s])[ \t]*\n+/g, '$1。')
    .replace(/([。！？])\1+/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  if (t.length > maxChars) {
    const cut = t.slice(0, maxChars)
    const end = Math.max(cut.lastIndexOf('。'), cut.lastIndexOf('！'), cut.lastIndexOf('？'), cut.lastIndexOf('. '))
    t = end > 40 ? cut.slice(0, end + 1) : cut
  }
  return t
}

/** 単語の頭になれる文字（漢字・カタカナ・英数字）と、その直前に来てよい文字（ひらがな・句読点・空白・括弧）。 */
const WORD_START = /[一-龥々ァ-ヴA-Za-z0-9]/
const BEFORE_WORD = /[ぁ-ん、。，,\s「」『』（）()]/

/** 区切る位置を決める。読点が最優先、次に「ひらがなのあと、漢字・カタカナの頭」。単語の途中（処|理）では切らない。 */
function safeCut(s: string, lo: number, hi: number): number {
  const win = s.slice(0, hi + 1)
  const comma = Math.max(win.lastIndexOf('、'), win.lastIndexOf('，'), win.lastIndexOf(','))
  if (comma >= lo) return comma + 1
  for (let i = Math.min(hi, s.length - 1); i >= lo; i--) {
    if (BEFORE_WORD.test(s[i - 1] ?? '') && WORD_START.test(s[i] ?? '')) return i
  }
  for (let i = hi + 1; i < Math.min(s.length - 1, hi + 24); i++) {
    if (BEFORE_WORD.test(s[i - 1] ?? '') && WORD_START.test(s[i] ?? '')) return i
  }
  return s.length
}

/**
 * 区切り方は voice-bridge と同じ考え: 合成は1文字あたり約0.06秒かかるので、最初のチャンクは短く
 * （最初の読点まで）して出だしを速くし、あとは直前のチャンクの約2倍まで伸ばす。1チャンク=ハイライト1単位。
 */
function toChunks(text: string): string[] {
  const out: string[] = []
  let rest = text.trim()
  let isFirst = true
  while (rest) {
    // 文末(。！？)のあとに閉じかっこが続くなら、そこまでを1文とする
    const sentence = rest.match(/^[^。！？!?]+[。！？!?]*[」』）)】]*/)?.[0] ?? rest
    let cut = sentence.length
    if (isFirst && sentence.length > 24) {
      // 出だしを速くするため、最初だけ短く切る。最初の読点が近ければそこで、なければ語の切れ目で
      const comma = Math.max(sentence.indexOf('、'), sentence.indexOf('，'))
      cut = comma >= 6 && comma < 30 ? comma + 1 : safeCut(sentence, 8, 24)
    } else if (!isFirst && sentence.length > 80) {
      // 2つめ以降は、文を丸ごと読む(切ると読みやアクセントが崩れるため)。長すぎる文だけ読点で切る
      cut = safeCut(sentence, 20, 80)
    }
    const piece = rest.slice(0, cut).trim()
    if (piece) out.push(piece)
    rest = rest.slice(cut).trim()
    isFirst = false
  }
  const last = out[out.length - 1]
  if (out.length > 1 && last !== undefined && last.length < 6) {
    out.pop()
    out[out.length - 1] += last
  }
  return out
}

const HAS_JAPANESE = /[ぁ-んァ-ン一-龥]/

function endSentence(t: string): string {
  const s = t.trim().replace(/[.。]+$/, '')
  return s ? `${s}。` : ''
}

function baseName(p: unknown): string {
  const parts = String(p ?? '').split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? ''
}

/** 作業の合間にモデルが書いた一言。長ければ文末で切って、最大 COMMENTARY_MAX_CHARS 文字。 */
function commentaryText(answer: string): string {
  const t = toSpeech(answer, 600)
  if (!t) return ''
  const sentences = t.match(/[^。！？!?]+[。！？!?]?/g) ?? [t]
  let out = ''
  for (const s of sentences) {
    if (out && out.length + s.length > COMMENTARY_MAX_CHARS) break
    out += s
    if (out.length >= COMMENTARY_MAX_CHARS) break
  }
  return out.length <= COMMENTARY_MAX_CHARS + 40 ? out : ''
}

const COMMAND_PHRASES: [RegExp, string][] = [
  [/^git\b/, 'Git を操作しています。'],
  [/^(npm|npx|pnpm|yarn)\b/, 'パッケージのコマンドを実行しています。'],
  [/^(node|bun|deno)\b/, 'Node を実行しています。'],
  [/^(python|python3|py|pip)\b/, 'Python を実行しています。'],
  [/^(curl|wget)\b/, 'ネットワークにアクセスしています。'],
  [/^claude\b/, 'Claude Code のコマンドを実行しています。'],
  [/^(ls|dir|cat|head|tail|find|grep|rg|sed|awk|wc|stat|tree|type)\b/, 'ファイルを調べています。'],
  [/^(mkdir|cp|mv|rm|copy|move|del)\b/, 'ファイルを整理しています。'],
  [/^(cd)\b/, '作業フォルダを移動しています。'],
]


/** AI を使わない英語→日本語。先頭の動詞で言い回しを決め、見つかった名詞（用語集）を並べる。 */
const VERB_RULES: [RegExp, string][] = [
  [/^(read|reading|load|loading|open|opening|view|viewing|cat)\b/i, '読み込んでいます'],
  [/^(check|checking|verify|verifying|validate|validating|inspect|inspecting|review|reviewing|see|seeing|look|looking|probe|probing|confirm|confirming|examine|examining)\b/i, '確認しています'],
  [/^(find|finding|search|searching|grep|locate|locating|scan|scanning|list|listing|show|showing|print|printing|display|displaying|get|getting|fetch|fetching)\b/i, '調べています'],
  [/^(run|running|execute|executing|launch|launching|start|starting)\b/i, '実行しています'],
  [/^(install|installing)\b/i, 'インストールしています'],
  [/^(write|writing|create|creating|add|adding|generate|generating|make|making|save|saving)\b/i, '作成しています'],
  [/^(edit|editing|update|updating|modify|modifying|fix|fixing|patch|patching|change|changing|rewrite|rewriting|replace|replacing|adjust|adjusting|improve|improving|refine|refining|rename|renaming)\b/i, '修正しています'],
  [/^(delete|deleting|remove|removing|clean|cleaning|clear|clearing)\b/i, '削除しています'],
  [/^(stop|stopping|kill|killing|terminate|terminating)\b/i, '停止しています'],
  [/^(restart|restarting|reload|reloading|reset|resetting)\b/i, '再起動しています'],
  [/^(compare|comparing)\b/i, '比較しています'],
  [/^(measure|measuring|time|timing|benchmark|benchmarking)\b/i, '計測しています'],
  [/^(build|building|compile|compiling|bundle|bundling)\b/i, 'ビルドしています'],
  [/^(test|testing|try|trying)\b/i, '試しています'],
  [/^(copy|copying|move|moving)\b/i, '移動しています'],
  [/^(download|downloading)\b/i, 'ダウンロードしています'],
  [/^(analy[sz]e|analy[sz]ing|diagnose|diagnosing|debug|debugging|investigate|investigating|trace|tracing)\b/i, '調査しています'],
  [/^(commit|committing|push|pushing|pull|pulling)\b/i, 'Git を更新しています'],
  [/^(play|playing)\b/i, '再生しています'],
  [/^(wait|waiting)\b/i, '待っています'],
]

const GLOSSARY: [RegExp, string][] = [
  [/type[- ]?check(ing)?|tsc|typescript/i, '型チェック'],
  [/voice[- ]?bridge/i, 'ボイスブリッジ'],
  [/settings? (page|ui|screen)/i, '設定画面'],
  [/settings?|config(uration)?s?|preferences?/i, '設定'],
  [/decision[s]? log|logs?/i, 'ログ'],
  [/mods?\b/i, 'mod'],
  [/plugins?/i, 'プラグイン'],
  [/scripts?/i, 'スクリプト'],
  [/servers?/i, 'サーバー'],
  [/engines?/i, 'エンジン'],
  [/audio|sound/i, '音声'],
  [/voices?/i, '声'],
  [/speech|tts|speak/i, '読み上げ'],
  [/translat(e|ion|ions|ing)/i, '翻訳'],
  [/highlight(s|ing)?|karaoke/i, 'ハイライト'],
  [/hooks?/i, 'フック'],
  [/errors?|failures?/i, 'エラー'],
  [/tests?\b/i, 'テスト'],
  [/python/i, 'Python'],
  [/\bnode\b|npm/i, 'Node'],
  [/\bgit\b/i, 'Git'],
  [/director(y|ies)|folders?/i, 'フォルダ'],
  [/paths?\b/i, 'パス'],
  [/shortcuts?/i, 'ショートカット'],
  [/desktop/i, 'デスクトップ'],
  [/\bui\b|screens?|panel/i, '画面'],
  [/pages?\b/i, 'ページ'],
  [/status/i, '状態'],
  [/process(es)?/i, 'プロセス'],
  [/ports?\b/i, 'ポート'],
  [/disk|storage/i, 'ディスク'],
  [/models?\b/i, 'モデル'],
  [/packages?|librar(y|ies)|dependenc(y|ies)/i, 'パッケージ'],
  [/outputs?/i, '出力'],
  [/devices?/i, 'デバイス'],
  [/wav\b|samples?/i, '音声データ'],
  [/playback/i, '再生'],
  [/latency|delay|timing/i, '遅延'],
  [/speed|rate/i, '速さ'],
  [/volume/i, '音量'],
  [/chunks?/i, '区切り'],
  [/version/i, 'バージョン'],
  [/api\b/i, 'API'],
  [/docs?|documentation|readme/i, 'ドキュメント'],
  [/types?\b/i, '型'],
  [/files?|source|code/i, 'ファイル'],
  [/commands?/i, 'コマンド'],
  [/memory/i, 'メモリ'],
  [/permission/i, '権限'],
]

function translateDescription(desc: string): string {
  const first = desc.trim()
  const verb = VERB_RULES.find(([re]) => re.test(first))?.[1]
  const hits: [number, string][] = []
  for (const [re, ja] of GLOSSARY) {
    const m = re.exec(first)
    if (m && !hits.some(([, j]) => j === ja)) hits.push([m.index, ja])
  }
  const nouns: string[] = []
  for (const [, ja] of hits.sort((a, b) => a[0] - b[0])) {
    if (nouns.length < 3 && !nouns.some((n) => n.includes(ja) || ja.includes(n))) nouns.push(ja)
  }
  if (!verb) return nouns.length > 0 ? `${nouns.join('と')}を確認しています。` : ''
  return nouns.length > 0 ? `${nouns.join('と')}を${verb}。` : `作業内容を${verb}。`
}

/** ツールの種類。完了時の「コマンドを2つ実行しました」などの集計に使う。 */
type Category = 'shell' | 'read' | 'search' | 'edit' | 'web' | 'agent' | 'mcp'
function categoryOf(name: string): Category | '' {
  if (name.startsWith('mcp__')) return 'mcp'
  if (name === 'Bash' || name === 'PowerShell') return 'shell'
  if (name === 'Read') return 'read'
  if (name === 'Grep' || name === 'Glob') return 'search'
  if (name === 'Edit' || name === 'MultiEdit' || name === 'Write' || name === 'NotebookEdit') return 'edit'
  if (name === 'WebSearch' || name === 'WebFetch') return 'web'
  if (name === 'Agent' || name === 'Task') return 'agent'
  return ''
}
const DONE_PHRASES: Record<Category, (n: number) => string> = {
  shell: (n) => `コマンドを${n}つ実行しました`,
  read: (n) => `ファイルを${n}つ読みました`,
  search: (n) => `${n}回検索しました`,
  edit: (n) => `ファイルを${n}つ変更しました`,
  web: (n) => `ウェブを${n}回調べました`,
  agent: (n) => `サブエージェントの作業が${n}つ終わりました`,
  mcp: (n) => `外部ツールを${n}回使いました`,
}
const ERROR_PHRASES: Record<Category, string> = {
  shell: 'コマンドがエラーになりました。',
  read: 'ファイルを読めませんでした。',
  search: '検索でエラーが出ました。',
  edit: 'ファイルの書き込みでエラーが出ました。',
  web: 'ウェブの取得でエラーが出ました。',
  agent: 'サブエージェントでエラーが出ました。',
  mcp: '外部ツールでエラーが出ました。',
}

/** 外部ツール(MCP)のサーバー名 → 日本語の呼び名 */
const SERVER_LABELS: [RegExp, string][] = [
  [/youtube/i, 'YouTube'],
  [/gmail/i, 'Gmail'],
  [/google[_ ]?drive/i, 'Googleドライブ'],
  [/google[_ ]?calendar/i, 'Googleカレンダー'],
  [/google[_ ]?sheets/i, 'Googleスプレッドシート'],
  [/google[_ ]?docs/i, 'Googleドキュメント'],
  [/google[_ ]?slides/i, 'Googleスライド'],
  [/notion/i, 'Notion'],
  [/figma/i, 'Figma'],
  [/canva/i, 'Canva'],
  [/supabase/i, 'Supabase'],
  [/exa/i, 'Exa検索'],
  [/screenpipe/i, 'Screenpipe'],
  [/context7/i, 'ドキュメント検索'],
  [/elevenlabs/i, 'ElevenLabs'],
  [/chrome/i, 'Chromeブラウザ'],
  [/vrc/i, 'VRChat'],
  [/pc[_ ]?agent/i, 'パソコン操作'],
  [/gemini|notebook/i, 'ノートブック'],
  [/web[_ ]?highlights/i, 'Web Highlights'],
  [/mermaid/i, 'Mermaid図'],
  [/three/i, '3Dビューア'],
  [/excalidraw/i, 'Excalidraw'],
  [/llamaparse/i, 'LlamaParse'],
  [/metricool/i, 'Metricool'],
  [/era[_ ]?context/i, '家計データ'],
  [/universal[_ ]?activity/i, '作業ログ'],
  [/consensus/i, '論文検索'],
  [/expedia/i, 'Expedia'],
  [/inkbox/i, 'Inkbox'],
  [/plugin[_-]?authoring/i, 'mod作成ツール'],
]

/** ツール名の単語 → 日本語(対象) */
const OBJECT_WORDS: Record<string, string> = {
  video: '動画', videos: '動画', channel: 'チャンネル', channels: 'チャンネル', comment: 'コメント', comments: 'コメント',
  playlist: 'プレイリスト', page: 'ページ', pages: 'ページ', doc: 'ドキュメント', docs: 'ドキュメント', document: 'ドキュメント',
  file: 'ファイル', files: 'ファイル', thread: 'スレッド', threads: 'スレッド', message: 'メッセージ', messages: 'メッセージ',
  mail: 'メール', email: 'メール', event: '予定', events: '予定', calendar: 'カレンダー', design: 'デザイン', image: '画像',
  thumbnail: 'サムネイル', frame: '画面', frames: '画面', transcript: '文字起こし', table: 'テーブル', tables: 'テーブル',
  project: 'プロジェクト', projects: 'プロジェクト', issue: '課題', memory: 'メモリ', record: '記録', content: '内容',
  screen: '画面', desktop: 'デスクトップ', window: 'ウィンドウ', windows: 'ウィンドウ', tab: 'タブ', tabs: 'タブ', text: 'テキスト',
  notebook: 'ノートブック', source: 'ソース', sheet: 'シート', slide: 'スライド', slides: 'スライド', spreadsheet: 'スプレッドシート',
  speaker: '話者', speakers: '話者', meeting: '会議', summary: '要約', status: '状態', health: '状態', user: 'ユーザー',
  account: 'アカウント', library: 'ライブラリ', branch: 'ブランチ', function: '関数', functions: '関数', database: 'データベース',
  highlight: 'ハイライト', highlights: 'ハイライト', tag: 'タグ', tags: 'タグ', folder: 'フォルダ', folders: 'フォルダ',
  location: '位置情報', photo: '写真', photos: '写真', history: '履歴', activity: '作業内容', screenshot: 'スクリーンショット',
  elements: '画面の要素', element: '画面の要素', keyword: 'キーワード', news: 'ニュース', web: 'ウェブ', trace: '記録',
}

const MCP_VERBS: [RegExp, string, string][] = [
  // [先頭の単語, 対象つきの言い方(〜を…しています), 対象なしの言い方]
  [/^(search|find|query|grep|lookup)$/, '検索', '検索'],
  [/^(list)$/, '一覧を取得', '一覧を取得'],
  [/^(get|read|fetch|view|show|describe|inspect|check|download|export|retrieve)$/, '取得', '取得'],
  [/^(create|add|new|write|insert|append|upload|save|generate|make)$/, '作成', '作成'],
  [/^(send|post|reply|forward|publish|share)$/, '送信', '送信'],
  [/^(update|edit|set|modify|patch|replace|apply|label|move|rename|merge)$/, '更新', '更新'],
  [/^(delete|remove|trash|clear|cancel|disconnect)$/, '削除', '削除'],
  [/^(run|exec|execute|start|call|navigate|click|type|press|open|play|control)$/, '操作', '操作'],
]

function serverLabel(server: string): string {
  const hit = SERVER_LABELS.find(([re]) => re.test(server))
  return hit ? hit[1] : server.replace(/^claude_ai_/, '').replace(/[_-]+/g, ' ')
}

/** 入力の中の「検索語」らしいもの。 */
function queryOf(input: Record<string, unknown>): string {
  for (const k of ['query', 'q', 'search', 'keywords', 'text', 'prompt', 'term']) {
    const v = input[k]
    if (typeof v === 'string' && v.trim()) return shorten(v.trim().replace(/\s+/g, ' '), 24)
  }
  return ''
}

/** 外部ツールを「何を・どのサービスで・どうしているか」の日本語にする。 */
function describeMcp(name: string, input: Record<string, unknown>): { label: string; text: string } {
  const [, server = '', ...rest] = name.split('__')
  const label = serverLabel(server)
  const tokens = rest.join('_').toLowerCase().split(/[_-]+/).filter(Boolean)
  const serverWords = server.toLowerCase().split(/[_-]+/)
  const words = tokens.filter((t) => !serverWords.includes(t))
  const verbHit = MCP_VERBS.find(([re]) => words.some((w) => re.test(w)))
  const objects = [...new Set(words.map((w) => OBJECT_WORDS[w]).filter((x): x is string => !!x))].slice(0, 2)
  const query = queryOf(input)
  const verb = verbHit ? verbHit[1] : ''
  let text: string
  if (query && verb === '検索') text = `${label}で「${query}」を検索しています。`
  else if (verb && objects.length > 0) text = `${label}の${objects.join('と')}を${verb}しています。`
  else if (verb) text = `${label}で${verb}しています。`
  else if (objects.length > 0) text = `${label}の${objects.join('と')}を使っています。`
  else text = `${label}を使っています。`
  return { label, text }
}

/** シェルコマンドから、何をしているかを推定する(説明文が無いとき用)。 */
function describeCommand(command: string): string {
  const parts = command.split('&&').map((p) => p.trim()).filter((p) => !p.startsWith('cd '))
  const cmd = (parts[0] ?? command).replace(/^\(+/, '').trim()
  const word = (re: RegExp) => re.test(cmd)
  const file = cmd.match(/([\w.-]+\.(?:py|js|ts|tsx|ps1|sh|json|md|html|txt|log))\b/)?.[1] ?? ''
  if (word(/^git\s+(status|diff|log|show)/)) return 'Gitで変更の状況を確認しています。'
  if (word(/^git\s+(add|commit)/)) return 'Gitでコミットしています。'
  if (word(/^git\s+(push|pull|fetch)/)) return 'Gitでリモートとやり取りしています。'
  if (word(/^git\b/)) return 'Gitを操作しています。'
  if (word(/^(npm|npx|pnpm|yarn)\s+(install|i|add)\b/)) return 'パッケージをインストールしています。'
  if (word(/^(npm|npx|pnpm|yarn)\s+(run\s+)?(test|jest|vitest)/)) return 'テストを実行しています。'
  if (word(/^(npm|npx|pnpm|yarn)\s+(run\s+)?(build|tsc)/)) return 'ビルドしています。'
  if (word(/tsc\b/)) return '型チェックをしています。'
  if (word(/^(npm|npx|pnpm|yarn)\b/)) return 'Nodeのコマンドを実行しています。'
  if (word(/^(python|python3|py)\b/)) return file ? `Pythonで ${file} を実行しています。` : 'Pythonを実行しています。'
  if (word(/^pip\b/)) return 'Pythonのパッケージを操作しています。'
  if (word(/^node\b/)) return file ? `Nodeで ${file} を実行しています。` : 'Nodeを実行しています。'
  if (word(/^(powershell|pwsh)\b/)) return file ? `PowerShellで ${file} を実行しています。` : 'PowerShellを実行しています。'
  if (word(/^claude\s+plugin\s+validate/)) return 'modを検証しています。'
  if (word(/^claude\b/)) return 'Claude Codeのコマンドを実行しています。'
  if (word(/^(curl|wget)\b/)) return 'ネットワークからデータを取得しています。'
  if (word(/^(ls|dir|tree)\b/)) return 'フォルダの中身を確認しています。'
  if (word(/^(cat|head|tail|type|more|less)\b/)) return file ? `${file} の中身を確認しています。` : 'ファイルの中身を確認しています。'
  if (word(/^(grep|rg|findstr|sed|awk)\b/)) return file ? `${file} の中を検索しています。` : 'ファイルの中を検索しています。'
  if (word(/^find\b/)) return 'ファイルを探しています。'
  if (word(/^(mkdir|md)\b/)) return 'フォルダを作っています。'
  if (word(/^(cp|copy|mv|move)\b/)) return 'ファイルを移動またはコピーしています。'
  if (word(/^(rm|del|rmdir)\b/)) return 'ファイルを削除しています。'
  if (word(/^(tasklist|ps|taskkill|kill)\b/)) return 'プロセスを確認しています。'
  if (word(/^(sleep|timeout)\b/)) return '少し待っています。'
  return 'コマンドを実行しています。'
}

/** サイト名の読み(WebFetch 用)。 */
function describeSite(url: string): string {
  const host = (url.match(/^https?:\/\/([^/]+)/)?.[1] ?? '').replace(/^www\./, '').toLowerCase()
  if (!host) return 'ウェブページを読んでいます。'
  if (host.endsWith('wikipedia.org')) return 'Wikipediaの記事を読んでいます。'
  if (host.includes('youtube.com') || host === 'youtu.be') return 'YouTubeのページを読んでいます。'
  if (host.includes('github.com')) return 'GitHubのページを読んでいます。'
  if (host === 'x.com' || host.includes('twitter.com')) return 'Xの投稿を読んでいます。'
  if (host.includes('qiita.com')) return 'Qiitaの記事を読んでいます。'
  if (host.includes('zenn.dev')) return 'Zennの記事を読んでいます。'
  if (host.includes('anthropic.com') || host.includes('claude.com')) return 'Anthropicの資料を読んでいます。'
  return `${host} のページを読んでいます。`
}

/** ツール呼び出しを、何をしているか分かる日本語にする。読まないツールは空文字。 */
async function describeTool($: Engine, name: string, input: Record<string, unknown>, aiRewrite: boolean): Promise<string> {
  if (name.startsWith('mcp__')) return describeMcp(name, input).text
  const desc = typeof input.description === 'string' ? input.description.trim() : ''
  // description が日本語ならそのまま、英語なら AI(設定でオン)または用語集で日本語にする
  const fromDescription = async (): Promise<string> => {
    if (!desc) return ''
    if (HAS_JAPANESE.test(desc)) return endSentence(desc)
    if (!aiRewrite) return translateDescription(desc)
    const r = await $.model.complete({
      model: 'haiku',
      prompt: `次の作業内容を、音声で読み上げる自然な日本語の一文(25文字前後。「〜しています。」で終わる)にしてください。説明・引用符・前置きは付けず、一文だけ答えてください。\n作業内容: ${desc}`,
      maxTokens: 60,
      timeoutMs: 6000,
    })
    return r.isAnswered ? endSentence(r.text.split('\n')[0] ?? '') : translateDescription(desc)
  }
  switch (name) {
    case 'Read':
      return baseName(input.file_path) ? `${baseName(input.file_path)} を読んでいます。` : 'ファイルを読んでいます。'
    case 'Write':
      return baseName(input.file_path) ? `${baseName(input.file_path)} を書き込んでいます。` : 'ファイルを書き込んでいます。'
    case 'Edit':
    case 'MultiEdit':
    case 'NotebookEdit': {
      const f = baseName(input.file_path ?? input.notebook_path)
      return f ? `${f} を編集しています。` : 'ファイルを編集しています。'
    }
    case 'Grep': {
      const pat = typeof input.pattern === 'string' ? input.pattern : ''
      const where = baseName(input.path)
      const target = where ? `${where} の中で` : 'コード内で'
      return /^[\w\s\-ぁ-んァ-ン一-龥]{1,16}$/.test(pat) ? `${target}「${pat}」を検索しています。` : `${target}パターンを検索しています。`
    }
    case 'Glob': {
      const pat = typeof input.pattern === 'string' ? input.pattern : ''
      return /^[\w*./\-]{1,20}$/.test(pat) ? `「${pat}」に合うファイルを探しています。` : 'ファイルを探しています。'
    }
    case 'Bash':
    case 'PowerShell': {
      const d = await fromDescription()
      if (d) return d
      return describeCommand(typeof input.command === 'string' ? input.command.trim() : '')
    }
    case 'WebSearch': {
      const q = typeof input.query === 'string' ? input.query.trim() : ''
      return q ? `ウェブで「${shorten(q, 24)}」を検索しています。` : 'ウェブで調べています。'
    }
    case 'WebFetch':
      return describeSite(typeof input.url === 'string' ? input.url : '')
    case 'Agent':
    case 'Task':
      return (await fromDescription()) || 'サブエージェントに作業を任せています。'
    case 'Skill':
      return typeof input.skill === 'string' && input.skill ? `${input.skill} のスキルを使っています。` : 'スキルを使っています。'
    case 'TodoWrite':
    case 'ToolSearch':
    case 'AskUserQuestion':
    case 'ExitPlanMode':
    case 'EnterPlanMode':
      return ''
    default:
      return '作業を進めています。'
  }
}

/** 日本語の説明を書いてもらうよう、モデルに伝える（進捗の読み上げがオンのときだけ）。 */
const COMPOSE_NOTE = [
  '音声読み上げの mod(aivis-speech)が有効です。',
  'ツールを呼ぶ前に、何のために何をするのかを、読み上げに向く短い自然な日本語の一文で書いてください。',
  '(例: 限界飯の動画を探すために、YouTubeで検索します。/ 設定が反映されているか確かめるために、ログを読みます。)',
  'Bash や Agent の description も、同じように目的が分かる日本語で書いてください。',
  'ファイル名やコマンドそのものを羅列せず、長いパスは書かないでください。',
].join('')


/** 本文の1行から、読み上げ用の見た目に近い平文を作る（強調記号・リンク・行頭記号を外す）。 */
function plainLine(line: string): string {
  return line
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '')
    .replace(/^\s*#{1,6}\s+/, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/(\*\*|__|`|~~)/g, '')
    .replace(/^\s*>\s?/, '')
}


/** チャンクの中で、再生の経過(0〜1)に対応する文字位置。句読点は間があるので重く数える。 */
function positionAt(text: string, fraction: number): number {
  const weights = Array.from(text).map((ch) =>
    ch === '。' || ch === '！' || ch === '？' ? 6 : ch === '、' || ch === '，' ? 4 : ch === ' ' ? 0.3 : /[A-Za-z0-9]/.test(ch) ? 0.7 : 1,
  )
  const total = weights.reduce((a, b) => a + b, 0)
  const target = Math.min(1, Math.max(0, fraction)) * total
  let acc = 0
  for (let i = 0; i < weights.length; i++) {
    acc += weights[i] ?? 1
    if (acc >= target) return i + 1
  }
  return weights.length
}

let lastInlineLog = ''
async function logInline($: Engine, hl: Highlight, line: string): Promise<void> {
  const tag = `${hl.id}:${hl.index}:${line}`
  if (tag === lastInlineLog) return
  lastInlineLog = tag
  await note($, `inline ${hl.index + 1}/${hl.chunks.length} ${line}`)
}

/** 背景色の明るさに合わせた、読みやすい文字色。 */
function textOn(bg: string): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(bg.slice(i, i + 2), 16))
  return (r ?? 0) * 0.299 + (g ?? 0) * 0.587 + (b ?? 0) * 0.114 > 150 ? '#000000' : '#ffffff'
}

function shorten(t: string, max: number): string {
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}

function speakArgv(root: string, args: string[], python: string): string[] {
  return [python, `${root}/scripts/speak.py`, ...args]
}

function voiceArgs(s: Settings): string[] {
  return [
    '--style', String(s.style),
    '--speed', String(s.speed),
    '--intonation', String(s.intonation),
    '--pitch', String(s.pitch),
    '--tempo', String(s.tempo),
    '--pause', String(s.pause),
    '--volume', String(s.volume),
    '--fast-first', String(s.fast_first),
  ]
}

async function loadSettings($: Engine): Promise<{ settings: Settings; path: string; python: string }> {
  const home = (await $.env.get('USERPROFILE')) ?? ''
  const path = `${home}/.claude/aivis-speech/settings.json`
  const raw = await $.fs.read(path).catch(() => undefined)
  const settings = parseSettings(typeof raw === 'string' ? raw : undefined)
  bandEnabled = settings.enabled && settings.band
  inlineEnabled = settings.enabled && settings.highlight && settings.inline_highlight
  return { settings, path, python: settings.python_path || 'python' }
}

/** 直近の判断（読んだ・飛ばした理由）を .run/decisions.log に残す。再読み込みをまたいで残す。「なんで読まなかった？」の調査用。 */
let hasLoadedLog = false
async function note($: Engine, line: string): Promise<void> {
  const path = `${$.plugin.root}/.run/decisions.log`
  if (!hasLoadedLog) {
    hasLoadedLog = true
    const old = await $.fs.read(path).catch(() => undefined)
    if (typeof old === 'string') decisions.push(...old.split('\n').filter(Boolean).slice(-40), '--- 再読み込み ---')
  }
  const at = new Date((await $.clock.now()) + 9 * 3600000).toISOString().slice(11, 19)
  decisions.push(`${at} ${line}`)
  if (decisions.length > 80) decisions.splice(0, decisions.length - 80)
  await $.fs.write(path, decisions.join('\n')).catch(() => {})
}

/**
 * 文ごとに合成・再生し、再生中の文を highlight に出す。新しい読み上げは古いものを置き換える。
 * 再生は切り離したワーカーが行う（mod の再読み込みで途切れない）。進捗ファイルを読んでハイライトする。
 */
async function speak($: Engine, text: string, kind: Highlight['kind'], s: Settings, python: string): Promise<void> {
  const chunks = toChunks(text)
  if (chunks.length === 0) return
  const id = ++seq
  speakingId = id
  speakingKind = kind
  if (kind !== 'progress') pendingQueue = []
  lastSpokeAt = await $.clock.now()
  await note($, `speak ${kind} ${text.length}文字: ${text.slice(0, 40)}`)
  const file = `${$.plugin.root}/.run/utterance-${id % 4}.txt`
  const progressFile = `${$.plugin.root}/.run/progress-${id % 4}.txt`
  await $.fs.write(file, chunks.join('\n'))
  await $.fs.write(progressFile, '')
  let errors = ''
  try {
    const launched = await $.process.run(
      speakArgv($.plugin.root, ['--text-file', file, '--progress-file', progressFile, ...voiceArgs(s)], python),
      { timeoutMs: 20000 },
    )
    if (launched.exitCode !== 0) errors += launched.stderr
    const startedAt = await $.clock.now()
    let isEnded = launched.exitCode !== 0
    let consumed = 0
    let lastIndex = -1
    let chunkMs = 1000
    let playedAt = startedAt
    let times: number[] = []
    let lastPos = -1
    let lastChangeAt = startedAt
    let lastLength = 0
    let exitReason = ''
    while (!isEnded && speakingId === id) {
      await $.clock.sleep(80)
      const raw = await $.fs.read(progressFile).catch(() => '')
      const now = await $.clock.now()
      const all = String(raw).split(String.fromCharCode(10))
      if (String(raw).length !== lastLength) {
        lastLength = String(raw).length
        lastChangeAt = now
      }
      for (let k = consumed; k < all.length - 1; k++) {
        const line = all[k] ?? ''
        if (line.startsWith('CHUNK ')) {
          const parts = line.split(' ')
          lastIndex = Number(parts[1])
          chunkMs = Math.max(200, Number(parts[2]) || 1000)
          playedAt = Number(parts[3]) || now
          times = []
          lastPos = -1
          await note($, `chunk ${lastIndex + 1}/${chunks.length}`)
        } else if (line.startsWith('T ')) {
          const parts = line.split(' ')
          if (Number(parts[1]) === lastIndex) times = (parts[2] ?? '').split(',').map(Number)
        } else if (line.startsWith('ERROR')) {
          errors += line + ' '
        } else if (line.startsWith('END')) {
          isEnded = true
        }
      }
      consumed = Math.max(0, all.length - 1)
      if (s.highlight && lastIndex >= 0 && speakingId === id) {
        // 音が出始めた時刻からの経過。実際の無音区間から作った時刻表があれば、それで文字位置を決める
        const elapsed = now - playedAt - s.sync_offset_ms
        const chunk = chunks[lastIndex] ?? ''
        let pos = 0
        if (times.length > 1) {
          for (let k = 1; k < times.length; k++) if ((times[k] ?? 0) <= elapsed) pos = k
        } else {
          pos = positionAt(chunk, elapsed / chunkMs)
        }
        if (pos !== lastPos) {
          lastPos = pos
          const index = lastIndex
          await update($, highlight, () => ({ id, chunks, index, pos, kind, color: s.highlight_color }))
        }
      }
      // 進捗ファイルがずっと増えないなら、再生が止まったとみなして打ち切る
      if (now - lastChangeAt > chunkMs + 40000) {
        exitReason = '進捗が止まった'
        break
      }
      if (now - startedAt > 15 * 60000) {
        exitReason = '15分で打ち切り'
        break
      }
    }
    await note($, `読み上げ終了 id=${id} ${isEnded ? 'END' : exitReason || '中断'} chunk=${lastIndex + 1}/${chunks.length}`)
  } catch (err) {
    errors += String(err)
  } finally {
    if (speakingId === id) {
      speakingId = 0
      speakingKind = ''
    }
    await update($, highlight, (cur) => (cur !== null && cur.id === id ? null : cur))
  }
  if (errors.trim()) {
    await note($, `error: ${errors.trim().slice(0, 120)}`)
    if (seq === id) $.ui.toast(`aivis-speech: ${errors.trim().slice(0, 160)}`)
  }
  // 読み上げ中に届いた進捗があれば、続けて読む
  const queued = pendingQueue.shift()
  if (speakingId === 0 && queued) await speak($, queued, 'progress', s, python)
}

/** 進捗の文を読む。進捗を読んでいる最中なら最新3件まで待たせ、返答を読んでいる最中は読まない。 */
async function narrate($: Engine, text: string, settings: Settings, python: string, repeat = false): Promise<void> {
  if (!text) return
  const now = await $.clock.now()
  if (!repeat && text === lastProgressText && now - lastProgressAt < REPEAT_MS) {
    await note($, `skip(同じ文): ${text}`)
    return
  }
  if (speakingId !== 0 && speakingKind !== 'progress') {
    await note($, `skip(${speakingKind}を読み上げ中): ${text}`)
    return
  }
  if (speakingId !== 0) {
    pendingQueue = [...pendingQueue, text].slice(-3)
    await note($, `待機: ${text}`)
    return
  }
  lastProgressAt = now
  lastProgressText = text
  await speak($, text, 'progress', settings, python)
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    $.command.register({ name: 'aivis', description: 'Claude の返答の読み上げ: on / off / stop / test / settings / log / doctor / setup' })
    const { settings } = await loadSettings($)
    $.ui.status(settings.enabled ? '🔊' : undefined)
    return next(e)
  })

  // 返答が出そろったら読み上げる（ターンは待たせない）。進捗より優先して置き換える。
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    try {
      await note($, `turn.complete reason=${e.reason} agent=${e.agentId ?? '-'} answer=${e.answer.length}文字`)
      if (e.agentId === undefined) keepAlive?.cancel()
      if (e.agentId !== undefined || e.reason === 'aborted' || e.reason === 'error') return done
      const { settings, python } = await loadSettings($)
      if (!settings.enabled) {
        await note($, 'answer: 読み上げオフ')
        return done
      }
      const text = toSpeech(e.answer, settings.max_chars)
      if (!text) {
        await note($, 'answer: 読む文字が空')
        return done
      }
      void speak($, text, 'answer', settings, python).catch((err: unknown) => note($, `answer error: ${String(err)}`))
    } catch (err) {
      await note($, `turn.complete error: ${String(err)}`).catch(() => {})
    }
    return done
  })

  // モデルの1回の応答。ツールを呼ぶ応答の前置き（作業の合間のひとこと）を進捗として読む。
  on('turn.step', async function* ($, e, next) {
    const result = yield* next(e)
    if (e.agentId === undefined && result.toolUses.length > 0 && result.answer.trim()) {
      void (async () => {
        const { settings, python } = await loadSettings($)
        if (!settings.enabled || !settings.progress_tts) return
        await narrate($, commentaryText(result.answer), settings, python)
      })().catch(() => {})
    }
    return result
  })

  // ツールを実行するたびに、何をしているかを日本語で読む。終わったら「コマンドを2つ実行しました」のように集計して読む。
  on('tool.call', async ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    const name = e.tool as string
    const input = e as unknown as Record<string, unknown>
    inflight += 1
    void (async () => {
      const { settings, python } = await loadSettings($)
      if (!settings.enabled || !settings.progress_tts) return
      await narrate($, await describeTool($, name, input, settings.ai_rewrite), settings, python)
    })().catch(() => {})
    const ran = await next(e)
    inflight = Math.max(0, inflight - 1)
    const cat = categoryOf(name)
    if (cat) {
      if (ran.deny === undefined && ran.isError === true) errorCats.push(cat)
      else doneCounts[cat] = (doneCounts[cat] ?? 0) + 1
      if (cat === 'mcp') {
        const label = describeMcp(name, input).label
        doneMcp[label] = (doneMcp[label] ?? 0) + 1
      }
    }
    if (inflight === 0) {
      const parts = (Object.keys(doneCounts) as Category[]).filter((c) => c !== 'mcp').map((c) => DONE_PHRASES[c](doneCounts[c] ?? 0))
      for (const [label, n] of Object.entries(doneMcp)) parts.push(`${label}を${n}回使いました`)
      for (const label of Object.keys(doneMcp)) delete doneMcp[label]
      const errs = errorCats.splice(0).map((c) => ERROR_PHRASES[c])
      for (const c of Object.keys(doneCounts) as Category[]) delete doneCounts[c]
      const text = [...errs, parts.length > 0 ? `${parts.join('、')}。` : ''].filter(Boolean).join('')
      void (async () => {
        const { settings, python } = await loadSettings($)
        if (!settings.enabled || !settings.progress_tts) return
        await narrate($, text, settings, python, true)
      })().catch(() => {})
    }
    return ran
  }).catch(($, e, next) => next(e))

  // ターンが始まったら「考えています」。長引く間は、たまに「引き続き作業中です」と言う（Churning… の代わり）。
  on('turn.start', async ($, e, next) => {
    const started = await next(e)
    if (e.agentId === undefined) {
      keepAlive?.cancel()
      const t0 = await $.clock.now()
      keepAlive = $.clock.every(30000, async () => {
        const { settings, python } = await loadSettings($)
        if (!settings.enabled || !settings.progress_tts) return
        const now = await $.clock.now()
        if (speakingId !== 0 || now - lastSpokeAt < 30000) return
        const minutes = Math.floor((now - t0) / 60000)
        await narrate($, minutes >= 1 ? `引き続き作業中です。${minutes}分たちました。` : '引き続き作業中です。', settings, python, true)
      })
      void (async () => {
        const { settings, python } = await loadSettings($)
        if (settings.enabled && settings.progress_tts) await narrate($, '考えています。', settings, python)
      })().catch(() => {})
    }
    return started
  }).catch(($, e, next) => next(e))

  // 日本語の短い説明を書いてもらうよう、システムプロンプトに一節を足す。
  on('prompt.compose', async ($, e, next) => {
    const result = await next(e)
    const { settings } = await loadSettings($)
    if (!settings.enabled || !settings.progress_tts) return result
    return { ...result, sections: [...result.sections, { id: 'aivis-speech', text: COMPOSE_NOTE, scope: 'session' as const }] }
  }).catch(($, e, next) => next(e))

  // 次の入力が始まったら読み上げを止める。
  on('prompt.submit', async ($, e, next) => {
    const { python } = await loadSettings($)
    pendingQueue = []
    speakingId = 0
    speakingKind = ''
    void $.process.run(speakArgv($.plugin.root, ['--stop'], python), { timeoutMs: 10000 }).catch(() => {})
    return next(e)
  }).catch(($, e, next) => next(e))

  // 読んでいる文を、返答の本文そのものの上で目立たせる。文字を足さず、その文を `...` (インラインコード)で囲むだけなので、行の幅も高さも変わらない。
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    const hl = await read($, highlight)
    if (!inlineEnabled || hl === null || hl.kind === 'test') return next(e)
    const current = hl.chunks[hl.index]
    if (!current) return next(e)
    const key = current.slice(0, Math.min(8, current.length))
    const text = e.props.text
    const lines = text.split(String.fromCharCode(10))
    let fence = false
    let offset = 0
    let start = -1
    let lineEnd = 0
    for (const line of lines) {
      if (line.trimStart().startsWith('```')) fence = !fence
      else if (!fence && !line.trimStart().startsWith('|') && start < 0) {
        const at = line.indexOf(key)
        if (at >= 0) {
          start = offset + at
          lineEnd = offset + line.length
        }
      }
      if (start >= 0) break
      offset += line.length + 1
    }
    if (start < 0) return next(e)
    // 文末(。！？)で終わるチャンクは、本文の文末まで。途中で切ったチャンクは、読む長さぶんだけ。
    let end = Math.min(lineEnd, start + current.length)
    if (/[。！？!?]$/.test(current)) {
      for (let i = start; i < Math.min(lineEnd, start + current.length + 12); i++) {
        if ('。！？'.includes(text[i] ?? '')) {
          end = i + 1
          break
        }
      }
    }
    while (start < end && (text[start] === ' ' || text[start] === '　')) start++
    while (end > start && (text[end - 1] === ' ' || text[end - 1] === '　')) end--
    const segment = text.slice(start, end)
    // 強調・コード・リンクなど、すでに記号が入っている文は、崩さないよう何もしない
    if (!segment || ['`', '*', '_', '[', ']', '|', '<', '>', '~'].some((c) => segment.includes(c))) {
      return next(e)
    }
    await logInline($, hl, `本文を囲んだ: ${segment.slice(0, 20)}`)
    const marked = text.slice(0, start) + '`' + segment + '`' + text.slice(end)
    return next({ ...e, props: { ...e.props, text: marked } })
  })

  // 画面下(プロンプトの真上)の字幕帯。常に2行ぶんの高さで、読んでいる位置まで色が伸びていく。
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const hl = await read($, highlight)
    if (!bandEnabled) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    if (hl === null) {
      return (
        <Box flexDirection="column" height={2} overflow="hidden">
          <Text dimColor>🔊 読み上げ待機中</Text>
          <Text dimColor> </Text>
        </Box>
      )
    }
    const text = hl.chunks[hl.index] ?? ''
    const pos = Math.min(hl.pos, text.length)
    const start = Math.max(0, Math.min(pos - 30, text.length - 90))
    const shown = text.slice(start, start + 90)
    const done = shown.slice(0, Math.max(0, pos - start))
    const rest = shown.slice(done.length)
    const upcoming = shorten(hl.chunks[hl.index + 1] ?? '', 90)
    return (
      <Box flexDirection="column" height={2} overflow="hidden">
        <Text>
          {'🔊 '}
          <Text bold color={textOn(hl.color)} backgroundColor={hl.color}>{done}</Text>
          {rest}
        </Text>
        <Text dimColor>{upcoming ? `   ${upcoming}` : ' '}</Text>
      </Box>
    )
  })

  // 待ち時間の表示（Churning… など）を日本語にする。
  on('ui.render', { component: 'Spinner' }, ($, e, next) => {
    if (e.props.message !== null) return next(e)
    const ja: Record<string, string> = {
      thinking: '考えています',
      requesting: 'リクエストを送っています',
      'tool-use': 'ツールを実行しています',
      'tool-input': 'ツールの入力を組み立てています',
      responding: '回答を書いています',
    }
    const message = ja[e.props.mode]
    return message ? next({ ...e, props: { ...e.props, message } }) : next(e)
  })

  on('command.run', { command: 'aivis' }, async ($, e, next) => {
    const arg = e.args.trim().toLowerCase()
    const { settings: s, path, python } = await loadSettings($)

    if (arg === 'on' || arg === 'off') {
      s.enabled = arg === 'on'
      await $.fs.write(path, JSON.stringify(s, null, 2))
      $.ui.status(s.enabled ? '🔊' : undefined)
      if (!s.enabled) void $.process.run(speakArgv($.plugin.root, ['--stop'], python), { timeoutMs: 10000 }).catch(() => {})
      return { text: `読み上げ: ${s.enabled ? 'ON' : 'OFF'}` }
    }
    if (arg === 'stop') {
      pendingQueue = []
      speakingId = 0
      speakingKind = ''
      await $.process.run(speakArgv($.plugin.root, ['--stop'], python), { timeoutMs: 10000 })
      return { text: '読み上げを停止しました' }
    }
    if (arg === 'settings') {
      void $.process
        .run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', `${$.plugin.root}/settings-ui/open.ps1`], { timeoutMs: 30000 })
        .catch(() => {})
      return { text: '設定画面を開きます' }
    }
    if (arg === 'test') {
      void speak($, 'AivisSpeech で読み上げています。読んでいる文が、ここで光っていれば成功です。', 'test', s, python).catch(() => {})
      return { text: 'テスト音声を再生します（初回はエンジン起動で数十秒かかることがあります）' }
    }
    if (arg === 'doctor' || arg === 'setup' || arg === 'setup engine') {
      const script = `${$.plugin.root}/scripts/setup.py`
      const args = arg === 'doctor' ? ['doctor'] : arg === 'setup' ? ['install'] : ['engine', '--yes', '--detach']
      const r = await $.process
        .run([python, script, ...args], { timeoutMs: 300000 })
        .catch((err: unknown) => ({ exitCode: 1, stdout: '', stderr: String(err) }))
      const out = `${r.stdout}${r.stderr ? String.fromCharCode(10) + r.stderr : ''}`.trim()
      return { text: out || '(出力がありませんでした)' }
    }
    if (arg === 'log') {
      return { text: decisions.length ? decisions.slice(-15).join('\n') : '（まだ記録がありません）' }
    }
    return { text: `読み上げ: ${s.enabled ? 'ON' : 'OFF'}（/aivis on | off | stop | test | settings | log | doctor | setup | setup engine）` }
  })
}
