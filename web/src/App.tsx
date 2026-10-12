import { Navigate, Route, Routes, BrowserRouter, useLocation } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './components/RequireAuth'
import { legacyRedirect } from './services/legacyRoutes'
import { AcceptInvite } from './pages/AcceptInvite'
import { GithubCallback } from './pages/GithubCallback'
import { Home } from './pages/Home'
import { HomeRedirect } from './pages/HomeRedirect'
import { Knowledge } from './pages/Knowledge'
import { Landing } from './pages/Landing'
import { Login } from './pages/Login'
import { Policies } from './pages/Policies'
import { KnowledgeEditor } from './pages/knowledge/KnowledgeEditor'
import { KnowledgeRevisions } from './pages/knowledge/KnowledgeRevisions'
import { Observability } from './pages/observability/Observability'
import { Project } from './pages/Project'
import { RunDetail } from './pages/RunDetail'
import { Runs } from './pages/Runs'
import { Settings } from './pages/Settings'

/** 旧いパス（services/legacyRoutes.ts の表）は新しい画面へ、それ以外はホームへ。 */
function Fallback() {
  const { pathname, search } = useLocation()
  return <Navigate to={legacyRedirect(pathname, search) ?? '/app'} replace />
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Landing />} />
          <Route element={<RequireAuth />}>
            <Route path="/app" element={<HomeRedirect />} />
            <Route path="/w/:slug" element={<Home />} />
            <Route path="/w/:slug/p/:owner/:repo/:tab?" element={<Project />} />
            <Route path="/w/:slug/knowledge" element={<Knowledge />} />
            <Route path="/w/:slug/knowledge/policies" element={<Policies />} />
            <Route path="/w/:slug/knowledge/:section/new" element={<KnowledgeEditor />} />
            <Route path="/w/:slug/knowledge/:section/:id" element={<KnowledgeEditor />} />
            <Route path="/w/:slug/knowledge/:section/:id/revisions" element={<KnowledgeRevisions />} />
            <Route path="/w/:slug/runs" element={<Runs />} />
            <Route path="/w/:slug/runs/:id" element={<RunDetail />} />
            <Route path="/w/:slug/observability" element={<Observability />} />
            <Route path="/w/:slug/settings/:tab?" element={<Settings />} />
            {/* 旧い検査の詳細: run のワークスペースが分かれば /w/:slug/runs/:id へ転送する（RunDetail） */}
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/github/callback" element={<GithubCallback />} />
            <Route path="/invite" element={<AcceptInvite />} />
          </Route>
          <Route path="*" element={<Fallback />} />
        </Routes>
        {/* スマホ幅は画面下のタブ（BottomNav）に重ねない */}
        <Toaster mobileOffset={{ bottom: 'calc(4.5rem + env(safe-area-inset-bottom))' }} />
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
