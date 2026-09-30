import { Navigate, Route, Routes, BrowserRouter, useLocation } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './components/RequireAuth'
import { legacyRedirect } from './services/legacyRoutes'
import { GithubCallback } from './pages/GithubCallback'
import { Home } from './pages/Home'
import { HomeRedirect } from './pages/HomeRedirect'
import { Login } from './pages/Login'
import { Project } from './pages/Project'
import { RunDetail } from './pages/RunDetail'
import { Settings } from './pages/Settings'

/** 知らないパス: 旧いパスなら新しい画面へ、それ以外はホームへ。 */
function Fallback() {
  const { pathname } = useLocation()
  return <Navigate to={legacyRedirect(pathname) ?? '/app'} replace />
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          {/* ランディングページは段 3。それまでは / をホームへ送る */}
          <Route path="/" element={<Navigate to="/app" replace />} />
          <Route element={<RequireAuth />}>
            <Route path="/app" element={<HomeRedirect />} />
            <Route path="/w/:slug" element={<Home />} />
            <Route path="/w/:slug/p/:owner/:repo" element={<Project />} />
            <Route path="/w/:slug/settings" element={<Settings />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/github/callback" element={<GithubCallback />} />
          </Route>
          <Route path="*" element={<Fallback />} />
        </Routes>
        <Toaster />
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
