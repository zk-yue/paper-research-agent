import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import NewTask from './pages/NewTask'
import TaskDetail from './pages/TaskDetail'

function TaskDetailRoute() {
  const { id } = useParams()
  return <TaskDetail key={id} />
}

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewTask />} />
          <Route path="/task/:id" element={<TaskDetailRoute />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}

export default App
