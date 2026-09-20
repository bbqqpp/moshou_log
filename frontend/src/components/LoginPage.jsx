import { useState } from 'react'

import { login } from '../api'

export default function LoginPage({ notice = '', onLoggedIn }) {
  const [guildName, setGuildName] = useState('')
  // 会话失效时 App 会把原因通过 notice 传进来
  const [loginError, setLoginError] = useState(notice)
  const [loginLoading, setLoginLoading] = useState(false)

  async function handleLogin(event) {
    event.preventDefault()
    setLoginError('')
    setLoginLoading(true)
    try {
      const result = await login(guildName)
      setGuildName('')
      onLoggedIn(result)
    } catch (loginFailed) {
      setLoginError(loginFailed.message || '登录失败')
    } finally {
      setLoginLoading(false)
    }
  }

  return (
    <div className="page login-page">
      <header className="hero">
        <p className="eyebrow">Warcraft Logs Analysis</p>
        <h1>WCL 战斗日志深度复盘</h1>
        <p className="hero-subtitle">请先通过公会验证，再开始分析。</p>
      </header>

      <form className="login-panel" onSubmit={handleLogin}>
        <h2>公会验证</h2>
        <input
          value={guildName}
          onChange={(event) => setGuildName(event.target.value)}
          placeholder="请输入公会名"
          aria-label="公会名"
          autoFocus
        />
        <button type="submit" disabled={loginLoading}>
          {loginLoading ? '验证中...' : '登录'}
        </button>
        {loginError && <p className="login-error">{loginError}</p>}
      </form>
    </div>
  )
}
