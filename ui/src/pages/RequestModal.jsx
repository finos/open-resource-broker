import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTemplates, useCreateRequest } from '../hooks'
import { useToast } from '../components/Toast'

export default function RequestModal({ onClose, preselectedTemplate = null }) {
  const navigate = useNavigate()
  const { show } = useToast()
  const { data: templates, isLoading: templatesLoading } = useTemplates()
  const createRequest = useCreateRequest()

  const templateList = Array.isArray(templates) ? templates : templates?.templates || []

  const [selectedTemplate, setSelectedTemplate] = useState(preselectedTemplate)
  const [count, setCount] = useState(1)

  useEffect(() => {
    if (preselectedTemplate) setSelectedTemplate(preselectedTemplate)
  }, [preselectedTemplate])

  const maxAllowed = selectedTemplate?.max_instances ?? selectedTemplate?.max_capacity ?? 99

  const handleSubmit = async () => {
    if (!selectedTemplate) return
    try {
      const result = await createRequest.mutateAsync({
        templateId: selectedTemplate.template_id,
        count,
      })
      show({ type: 'success', message: `Request submitted successfully` })
      onClose()
      const newId = result?.request_id
      navigate(newId ? `/requests?expand=${newId}` : '/requests')
    } catch (err) {
      show({ type: 'error', message: err.message || 'Failed to create request' })
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 dark:bg-black/60">
      <div
        className="bg-white dark:bg-gray-900 rounded-lg shadow-xl w-full max-w-lg mx-4 p-6 border border-transparent dark:border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Request Machines</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none">×</button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Template</label>
            {templatesLoading ? (
              <div className="h-10 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
            ) : (
              <select
                value={selectedTemplate?.template_id || ''}
                onChange={(e) => {
                  const t = templateList.find((t) => t.template_id === e.target.value)
                  setSelectedTemplate(t || null)
                  setCount(1)
                }}
                className="w-full border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]"
              >
                <option value="">Select a template…</option>
                {templateList.map((t) => (
                  <option key={t.template_id} value={t.template_id}>
                    {t.name || t.template_id} — {t.provider_api || ''} · {t.instance_type || ''}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Number of machines {selectedTemplate ? `(max ${maxAllowed})` : ''}
            </label>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setCount((c) => Math.max(1, c - 1))}
                className="w-9 h-9 border border-gray-300 dark:border-gray-600 rounded-md text-lg text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                −
              </button>
              <input
                type="number"
                min={1}
                max={maxAllowed}
                value={count}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10)
                  if (!isNaN(v)) setCount(Math.min(maxAllowed, Math.max(1, v)))
                }}
                className="w-20 border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm text-center bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]"
              />
              <button
                onClick={() => setCount((c) => Math.min(maxAllowed, c + 1))}
                className="w-9 h-9 border border-gray-300 dark:border-gray-600 rounded-md text-lg text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                +
              </button>
            </div>
          </div>
        </div>

        <div className="flex gap-3 justify-end mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!selectedTemplate || createRequest.isPending}
            className="px-5 py-2 text-sm bg-[#185FA5] text-white rounded-md font-medium hover:bg-[#14508a] disabled:opacity-50"
          >
            {createRequest.isPending ? 'Requesting…' : 'Request Machines'}
          </button>
        </div>
      </div>
    </div>
  )
}
