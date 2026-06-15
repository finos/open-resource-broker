import axios from 'axios'

// Health and info live at root, not under /api/v1
const rootClient = axios.create({
  baseURL: (import.meta.env.VITE_ORB_API_BASE || 'http://localhost:8000/api/v1').replace('/api/v1', ''),
  timeout: 10000,
})

rootClient.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.detail || err.message || 'Unknown error'
    const error = new Error(msg)
    error.status = err.response?.status
    return Promise.reject(error)
  }
)

export const getHealth = async () => {
  const { data } = await rootClient.get('/health')
  return data
}

export const getInfo = async () => {
  const { data } = await rootClient.get('/info')
  return data
}

// Returns Prometheus exposition text. We parse it into a small key/value map.
export const getMetrics = async () => {
  const { data } = await rootClient.get('/metrics', {
    headers: { Accept: 'text/plain' },
    transformResponse: [(r) => r], // keep as plain string
  })
  return data
}
