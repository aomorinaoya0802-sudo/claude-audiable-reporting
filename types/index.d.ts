export type Highlight = {
  /** 読み上げごとの連番（古い読み上げが新しい表示を消さないための目印） */
  id: number
  /** 文ごとに区切った読み上げテキスト */
  chunks: string[]
  /** いま再生中の文の位置 */
  index: number
  /** いま読んでいる文の中の位置（文字数）。ここまで色を付ける */
  pos: number
  kind: 'answer' | 'progress' | 'test' | 'question' | 'think'
  /** ハイライトの背景色（#rrggbb） */
  color: string
}

declare module 'claude-code' {
  interface PluginState {
    'aivis-speech': { highlight: Highlight | null }
  }
}
