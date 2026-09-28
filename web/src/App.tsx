import { Navigate, Route, Routes, BrowserRouter } from 'react-router-dom'
import './App.css'
import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './components/RequireAuth'
import { Login } from './pages/Login'
import { McpTokens } from './pages/McpTokens'
import { RunDetail } from './pages/RunDetail'
import { RunList } from './pages/RunList'

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<RunList />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/workspaces/:slug/mcp-tokens" element={<McpTokens />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
