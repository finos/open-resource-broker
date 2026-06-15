import { useState } from 'react'
import { useTemplates, useTemplate, useCreateRequest, useRefreshTemplates } from '../hooks'
import { useNavigate } from 'react-router-dom'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { cleanupAwsTemplate } from '../api/aws'
import { useToast } from '../components/Toast'
import EmptyState from '../components/EmptyState'
import ErrorBanner from '../components/ErrorBanner'
import ConfirmModal from '../components/ConfirmModal'
import Topbar from '../components/Topbar'
import CreateTemplateModal from './CreateTemplateModal'
import EditTemplateModal from './EditTemplateModal'

const useDeleteTemplate = () => {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await client.delete(`/templates/${id}`)
      return data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['templates'] }),
  })
}

const TYPE_FILTERS = ['All', 'SpotFleet', 'EC2Fleet', 'RunInstances', 'ASG']

const TYPE_COLOR = {
  SpotFleet:    'bg-purple-100 text-purple-800 border-purple-200 dark:bg-purple-900/40 dark:text-purple-300 dark:border-purple-700',
  EC2Fleet:     'bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-700',
  RunInstances: 'bg-teal-100 text-teal-800 border-teal-200 dark:bg-teal-900/40 dark:text-teal-300 dark:border-teal-700',
  ASG:          'bg-orange-100 text-orange-800 border-orange-200 dark:bg-orange-900/40 dark:text-orange-300 dark:border-orange-700',
}

function TypeBadge({ type }) {
  const cls = TYPE_COLOR[type] || 'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {type}
    </span>
  )
}

function machineTypeSummary(machine_types) {
  if (!machine_types || typeof machine_types !== 'object') return '—'
  const types = Object.keys(machine_types)
  if (types.length === 0) return '—'
  if (types.length === 1) return types[0]
  return `${types[0]} +${types.length - 1}`
}

function TemplateCard({ template, selected, onClick }) {
  const id = template.template_id
  const type = template.provider_api
  const isUserCreated = template.tags?.CreatedBy === 'orb-ui'
  return (
    <div
      onClick={() => onClick(template)}
      className={`bg-white dark:bg-gray-900 border rounded-lg p-4 cursor-pointer hover:border-[#185FA5] transition-colors
        ${selected ? 'border-[#185FA5] ring-1 ring-[#185FA5]' : 'border-gray-200 dark:border-gray-700'}`}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          {type && <TypeBadge type={type} />}
          {isUserCreated && (
            <span className="text-[10px] uppercase tracking-wider font-semibold text-[#185FA5] bg-blue-50 dark:bg-blue-950 px-1.5 py-0.5 rounded">
              user
            </span>
          )}
        </div>
        {template.price_type && (
          <span className="text-xs text-gray-400 dark:text-gray-500 capitalize">{template.price_type}</span>
        )}
      </div>
      <p className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1 leading-snug">{template.name || id}</p>
      <p className="mono text-xs text-gray-400 dark:text-gray-500 mb-3 truncate">{id}</p>

      <div className="grid grid-cols-3 gap-2 text-center border-t border-gray-100 dark:border-gray-700 pt-3">
        <div>
          <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">{template.instance_type || '—'}</p>
          <p className="text-xs text-gray-400 dark:text-gray-500">Primary</p>
        </div>
        <div>
          <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">
            {machineTypeSummary(template.machine_types)}
          </p>
          <p className="text-xs text-gray-400 dark:text-gray-500">Types</p>
        </div>
        <div>
          <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">{template.max_instances ?? template.max_capacity ?? '—'}</p>
          <p className="text-xs text-gray-400 dark:text-gray-500">Max</p>
        </div>
      </div>

      <div className="mt-3 pt-2">
        <button
          onClick={(e) => { e.stopPropagation(); onClick(template) }}
          className="text-xs text-[#185FA5] hover:underline font-medium"
        >
          Request →
        </button>
      </div>
    </div>
  )
}

function DrawerDetail({ template, onClose, onEdit, onDelete }) {
  const id = template.template_id
  const { data: detail } = useTemplate(id)
  const createRequest = useCreateRequest()
  const navigate = useNavigate()
  const { show } = useToast()
  const t = detail || template
  const maxAllowed = t.max_instances ?? t.max_capacity ?? 1
  const [count, setCount] = useState(1)

  const isUserCreated = t.tags?.CreatedBy === 'orb-ui'

  const handleRequest = async () => {
    try {
      const result = await createRequest.mutateAsync({ templateId: id, count })
      show({ type: 'success', message: `Request submitted for ${count} machine(s)` })
      const newId = result?.request_id
      navigate(newId ? `/requests?expand=${newId}` : '/requests')
    } catch (err) {
      show({ type: 'error', message: err.message || 'Failed to create request' })
    }
  }

  const machineTypeEntries = Object.entries(t.machine_types || {})

  const fields = [
    ['Template ID', <span className="mono text-xs break-all">{id}</span>],
    ['Name', t.name || '—'],
    ['Provider API', t.provider_api || '—'],
    ['Price Type', t.price_type || '—'],
    ['Allocation Strategy', t.allocation_strategy || '—'],
    ['Primary Instance', t.instance_type || '—'],
    ['Max Instances', t.max_instances ?? t.max_capacity ?? '—'],
    ['Image ID', <span className="mono text-xs">{t.image_id || '—'}</span>],
  ]

  return (
    <div className="w-80 bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 flex flex-col h-full overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Template Details</h2>
        <div className="flex items-center gap-2">
          {isUserCreated && (
            <span className="text-[10px] uppercase tracking-wider font-semibold text-[#185FA5] bg-blue-50 dark:bg-blue-950 px-2 py-0.5 rounded">
              user
            </span>
          )}
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl leading-none">×</button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        <div className="space-y-3">
          {fields.map(([label, value]) => (
            <div key={label}>
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-0.5">{label}</p>
              <p className="text-sm text-gray-800 dark:text-gray-200">{value}</p>
            </div>
          ))}
        </div>

        {machineTypeEntries.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">Instance Types (weight)</p>
            <div className="space-y-1">
              {machineTypeEntries.map(([type, weight]) => (
                <div key={type} className="flex items-center justify-between text-xs bg-gray-50 dark:bg-gray-800 rounded px-2.5 py-1.5">
                  <span className="mono text-gray-700 dark:text-gray-300">{type}</span>
                  <span className="text-gray-400 dark:text-gray-500">weight {weight}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {t.tags && Object.keys(t.tags).length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">Tags</p>
            <div className="space-y-1">
              {Object.entries(t.tags).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-xs bg-gray-50 dark:bg-gray-800 rounded px-2.5 py-1.5">
                  <span className="mono text-gray-700 dark:text-gray-300">{k}</span>
                  <span className="text-gray-500 dark:text-gray-400">{v}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Number of machines <span className="text-gray-400 font-normal">(max {maxAllowed})</span>
          </p>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setCount((c) => Math.max(1, c - 1))}
              className="w-8 h-8 border border-gray-300 dark:border-gray-600 rounded text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
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
              className="w-16 border border-gray-300 dark:border-gray-600 rounded px-2 py-1.5 text-sm text-center bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]"
            />
            <button
              onClick={() => setCount((c) => Math.min(maxAllowed, c + 1))}
              className="w-8 h-8 border border-gray-300 dark:border-gray-600 rounded text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
            >
              +
            </button>
          </div>
        </div>
      </div>

      <div className="p-5 border-t border-gray-100 dark:border-gray-700 space-y-2">
        <button
          onClick={handleRequest}
          disabled={createRequest.isPending}
          className="w-full bg-[#185FA5] text-white py-2.5 rounded-md text-sm font-medium hover:bg-[#14508a] disabled:opacity-50"
        >
          {createRequest.isPending ? 'Requesting…' : 'Request Machines'}
        </button>
        {isUserCreated && (
          <>
            <button
              onClick={() => onEdit(t)}
              className="w-full border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 py-2 rounded-md text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-800"
            >
              Edit Template
            </button>
            <button
              onClick={() => onDelete(t)}
              className="w-full border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 py-2 rounded-md text-sm font-medium hover:bg-red-50 dark:hover:bg-red-900/30"
            >
              Delete Template
            </button>
          </>
        )}
      </div>
    </div>
  )
}

export default function Templates() {
  const { data, isLoading, error, refetch } = useTemplates()
  const [filter, setFilter] = useState('All')
  const [selected, setSelected] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const deleteTemplate = useDeleteTemplate()
  const refreshTemplates = useRefreshTemplates()
  const { show } = useToast()

  const handleRefresh = async () => {
    try {
      await refreshTemplates.mutateAsync()
      show({ type: 'success', message: 'Templates refreshed from disk' })
    } catch (err) {
      show({ type: 'error', message: err.message || 'Refresh failed' })
    }
  }

  const templateList = Array.isArray(data) ? data : data?.templates || []

  const filtered = filter === 'All'
    ? templateList
    : templateList.filter((t) => t.provider_api === filter)

  return (
    <div className="flex flex-col h-full">
      <Topbar
        title="Templates"
        actions={
          <div className="flex items-center gap-3">
            <span className="hidden lg:inline text-xs text-gray-400 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-3 py-1.5">
              CLI: <span className="mono">orb templates generate</span>
            </span>
            <button
              onClick={handleRefresh}
              disabled={refreshTemplates.isPending}
              className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              {refreshTemplates.isPending ? 'Refreshing…' : '↻ Refresh'}
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="px-4 py-2 text-sm bg-[#185FA5] text-white rounded-md hover:bg-[#14508a]"
            >
              + Add Template
            </button>
          </div>
        }
      />

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 overflow-y-auto p-6">
          <div className="flex gap-2 mb-5 flex-wrap">
            {TYPE_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors
                  ${filter === f
                    ? 'bg-[#185FA5] text-white border-[#185FA5]'
                    : 'bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-[#185FA5] hover:text-[#185FA5]'
                  }`}
              >
                {f}
                {f !== 'All' && (
                  <span className="ml-1.5 text-[10px] opacity-70">
                    {templateList.filter((t) => t.provider_api === f).length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {error ? (
            <ErrorBanner error={error} onRetry={refetch} />
          ) : isLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="h-44 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState
              icon="◧"
              title="No templates found"
              description={filter !== 'All' ? `No ${filter} templates.` : 'No templates have been generated yet.'}
              actionLabel={filter !== 'All' ? 'Show all' : undefined}
              onAction={filter !== 'All' ? () => setFilter('All') : undefined}
            />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {filtered.map((t) => (
                <TemplateCard
                  key={t.template_id}
                  template={t}
                  selected={selected?.template_id === t.template_id}
                  onClick={(tmpl) =>
                    setSelected((prev) => prev?.template_id === tmpl.template_id ? null : tmpl)
                  }
                />
              ))}
            </div>
          )}
        </div>

        {selected && (
          <DrawerDetail
            template={selected}
            onClose={() => setSelected(null)}
            onEdit={(tmpl) => {
              setEditing(tmpl)
              setSelected(null)
            }}
            onDelete={(tmpl) => setDeleting(tmpl)}
          />
        )}
      </div>

      {showCreate && <CreateTemplateModal onClose={() => setShowCreate(false)} />}
      {editing && (
        <EditTemplateModal template={editing} onClose={() => setEditing(null)} />
      )}
      {deleting && (
        <ConfirmModal
          title="Delete template?"
          description={`This permanently removes "${deleting.name || deleting.template_id}" from ORB and deletes any matching AWS EC2 Launch Templates. Existing machines created from it are not affected.`}
          itemList={[deleting.template_id]}
          confirmLabel="Delete Template"
          loading={deleteTemplate.isPending}
          onCancel={() => setDeleting(null)}
          onConfirm={async () => {
            try {
              const tplId = deleting.template_id
              await deleteTemplate.mutateAsync(tplId)
              // Best-effort: also wipe AWS Launch Templates tagged with this id.
              try {
                const cleanup = await cleanupAwsTemplate(tplId)
                const removed = cleanup?.deleted?.length || 0
                if (removed > 0) {
                  show({
                    type: 'success',
                    message: `Template "${tplId}" deleted (also removed ${removed} AWS launch template${removed === 1 ? '' : 's'})`,
                  })
                } else {
                  show({ type: 'success', message: `Template "${tplId}" deleted` })
                }
              } catch (cleanupErr) {
                show({
                  type: 'success',
                  message: `Template "${tplId}" deleted in ORB (AWS cleanup failed: ${cleanupErr.message || 'unknown error'})`,
                })
              }
              setDeleting(null)
              setSelected(null)
            } catch (err) {
              show({ type: 'error', message: err.message || 'Failed to delete template' })
            }
          }}
        />
      )}
    </div>
  )
}
