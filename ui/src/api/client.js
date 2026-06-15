import axios from 'axios'

const client = axios.create({
  baseURL: import.meta.env.VITE_ORB_API_BASE || 'http://localhost:8000/api/v1',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg =
      err.response?.data?.detail ||
      err.response?.data?.message ||
      err.response?.data?.error ||
      err.message ||
      'Unknown error'
    const error = new Error(msg)
    error.status = err.response?.status
    error.data = err.response?.data
    return Promise.reject(error)
  }
)

export default client
