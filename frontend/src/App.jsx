import { useEffect, useState } from 'react'

import { clearSession, fetchMe, getAuthToken, getGuildName } from './api'
import FightView from './components/FightView'
import HomeView from './components/HomeView'
import LoginPage from './components/LoginPage'
import PlayerReportView from './components/PlayerReportView'
import Sidebar from './components/Sidebar'
import { ROUTE_FIGHT, ROUTE_PLAYER, navigate, useHashRoute } from './router'

function App() {
  const route = useHashRoute()
  const [authToken, setAuthToken] = useState(() => getAuthToken())
  const [sessionChecked, setSessionChecked] = useState(() => !getAuthToken())
  const [verifiedGuild, setVerifiedGuild] = useState(() => getGuildName())
  const [loginNotice, setLoginNotice] = useState('')
  // 生成报告后靠它递增来让侧边栏重新拉取列表
  const [reportsVersion, setReportsVersion] = useState(0)

  const isReportRoute = route.name === ROUTE_FIGHT || route.name === ROUTE_PLAYER

  function handleUnauthorized(message) {
    clearSession()
    setAuthToken('')
    setVerifiedGuild('')
    setSessionChecked(true)
    setLoginNotice(message || '登录已失效，请重新输入公会名')
  }

  // 校验 localStorage 里恢复出来的 token，失效就直接落到登录页
  useEffect(() => {
    if (sessionChecked) return

    let cancelled = false
    fetchMe()
      .then((payload) => {
        if (cancelled) return
        setVerifiedGuild(payload.guild_name || '')
        setSessionChecked(true)
      })
      .catch((failed) => {
        if (cancelled) return
        if (failed.status === 401) {
          handleUnauthorized(failed.message)
          return
        }
        // 后端不可用等其它错误先放行，交给后续请求去报错
        setSessionChecked(true)
      })

    return () => {
      cancelled = true
    }
  }, [sessionChecked])

  function handleLoggedIn(result) {
    setAuthToken(result.token)
    setVerifiedGuild(result.guild_name || '')
    setLoginNotice('')
  }

  function handleLogout() {
    clearSession()
    setAuthToken('')
    setVerifiedGuild('')
    setLoginNotice('')
    navigate('#')
  }

  const bumpReports = () => setReportsVersion((version) => version + 1)

  // 分享链接：**未登录也必须能打开**，所以这条分支要排在登录判断前面。
  // 走这条路径时页面里不能有任何需要鉴权的请求 —— 报告读接口都是公开的。
  if (!authToken && isReportRoute) {
    return (
      <div className="page">
        <div className="share-bar">
          <span className="eyebrow">分享的只读报告</span>
          <button type="button" className="ghost-button" onClick={() => navigate('#')}>
            返回首页
          </button>
        </div>
        {route.name === ROUTE_FIGHT ? (
          <FightView reportCode={route.reportCode} fightId={route.fightId} readOnly />
        ) : (
          <PlayerReportView
            reportCode={route.reportCode}
            fightId={route.fightId}
            kind={route.kind}
            slug={route.slug}
          />
        )}
      </div>
    )
  }

  if (authToken && !sessionChecked) {
    return (
      <div className="page login-page">
        <p className="status-line">正在验证登录状态...</p>
      </div>
    )
  }

  if (!authToken) {
    return <LoginPage notice={loginNotice} onLoggedIn={handleLoggedIn} />
  }

  const activeKey = isReportRoute ? `${route.reportCode}__${route.fightId}` : ''

  return (
    <div className="page">
      <div className="layout">
        <Sidebar
          version={reportsVersion}
          activeKey={activeKey}
          isHome={route.name === 'home'}
          onUnauthorized={handleUnauthorized}
        />
        <main className="main-area">
          <div className="auth-bar">
            {verifiedGuild && <span>已验证公会：{verifiedGuild}</span>}
            {/* 窄屏下侧边栏会整个铺在主区上方、被滚出视野，所以这里再留一个回首页的入口 */}
            {route.name !== 'home' && (
              <button type="button" className="ghost-button" onClick={() => navigate('#')}>
                分析新战斗
              </button>
            )}
            <button type="button" className="ghost-button" onClick={handleLogout}>
              退出登录
            </button>
          </div>

          {route.name === 'home' && (
            <HomeView onUnauthorized={handleUnauthorized} onReportsChanged={bumpReports} />
          )}
          {route.name === ROUTE_FIGHT && (
            <FightView
              reportCode={route.reportCode}
              fightId={route.fightId}
              onUnauthorized={handleUnauthorized}
              onReportsChanged={bumpReports}
            />
          )}
          {route.name === ROUTE_PLAYER && (
            <PlayerReportView
              reportCode={route.reportCode}
              fightId={route.fightId}
              kind={route.kind}
              slug={route.slug}
              onUnauthorized={handleUnauthorized}
            />
          )}
        </main>
      </div>
    </div>
  )
}

export default App
