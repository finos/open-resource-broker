import client from './client'

export const getMachines = async () => {
  const { data } = await client.get('/machines/')
  return data
}

export const getMachine = async (id) => {
  const { data } = await client.get(`/machines/${id}`)
  return data
}

// Both single and bulk use the same endpoint; body is { machineIds: [...] }
// We always send force=true so a stale "pending return" record from a prior
// failure (e.g. expired AWS creds) doesn't block subsequent attempts.
export const returnMachines = async (ids) => {
  const { data } = await client.post('/machines/return', { machineIds: ids, force: true })
  return data
}
