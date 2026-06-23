import client from './client'

export const getRequests = async () => {
  const { data } = await client.get('/requests/')
  return data
}

// List only return requests (GET /requests/return)
export const getReturnRequests = async (limit = 100) => {
  const { data } = await client.get('/requests/return', { params: { limit } })
  return data
}

export const getRequest = async (id) => {
  const { data } = await client.get(`/requests/${id}/status`)
  return data
}

// Server-Sent Events stream for a request's status. Returns an EventSource.
export const streamRequest = (id, { interval = 2, timeout = 300 } = {}) => {
  const base =
    import.meta.env.VITE_ORB_API_BASE || 'http://localhost:8000/api/v1'
  const url = new URL(`${base}/requests/${id}/stream`)
  url.searchParams.set('interval', String(interval))
  url.searchParams.set('timeout', String(timeout))
  return new EventSource(url.toString())
}

// Create uses /machines/request with { templateId, count }
export const createRequest = async ({ templateId, count }) => {
  const { data } = await client.post('/machines/request', { templateId, count })
  return data
}

export const cancelRequest = async (id) => {
  const { data } = await client.delete(`/requests/${id}`)
  return data
}
