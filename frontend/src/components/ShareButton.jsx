import { useState } from 'react'

import { shareUrl } from '../router'

export default function ShareButton({ path }) {
  const [state, setState] = useState('idle')

  async function copy() {
    const link = shareUrl(path)
    try {
      await navigator.clipboard.writeText(link)
      setState('copied')
    } catch {
      // 非 HTTPS / 无权限时 clipboard 会拒绝。退回到让用户自己复制。
      window.prompt('复制下面的分享链接：', link)
      setState('manual')
    }
    setTimeout(() => setState('idle'), 2200)
  }

  return (
    <button type="button" className="ghost-button share-button" onClick={copy}>
      {state === 'copied' ? '链接已复制' : state === 'manual' ? '请手动复制' : '复制分享链接'}
    </button>
  )
}
