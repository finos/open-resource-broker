import client from './client'

export const getTemplates = async () => {
  const { data } = await client.get('/templates/')
  return data
}

export const getTemplate = async (id) => {
  const { data } = await client.get(`/templates/${id}/`)
  return data
}

// PUT a partial template update. Used by Config defaults flow and EditTemplateModal.
export const updateTemplate = async (id, body) => {
  const { data } = await client.put(`/templates/${id}`, body)
  return data
}

// Validate a template config without persisting (POST /templates/validate)
export const validateTemplate = async (body) => {
  const { data } = await client.post('/templates/validate', body)
  return data
}

// Refresh template cache from files (POST /templates/refresh)
export const refreshTemplates = async () => {
  const { data } = await client.post('/templates/refresh')
  return data
}
