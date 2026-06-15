import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useRequests, useReturnRequests, useCancelRequest, useRequest } from '../hooks'
import { useToast } from '../components/Toast'
import { streamRequest } from '../api/requests'
import Badge from '../components/Badge'
import StatusDot from '../components/StatusDot'
import EmptyState from '../components/EmptyState'
import ErrorBanner from '../components/ErrorBanner'
import ConfirmModal from '../components/ConfirmModal'
import Topbar from '../components/Topbar'

const FILTERS = [
  { value: 'all',         label: 'All' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed',   label: 'Completed' },
  { value: 'failed',      label: 'Failed' },
  { value: 'cancelled',   label: 'Cancelled' },
  { value: 'timeout',     label: 'Timeout' },
  { value: 'returns',     label: 'Returns only' },
]

function StatusPill({ status }) {
  const s = (status || '').toLowerCase()
  if (s === 'completed' || s === 'complete' || s === 'partial') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-green-50 dark:bg-green-900/40 border border-green-200 dark:border-green-700 text-green-700 dark:text-green-300">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        Complete
      </span>
    )
  }
  if (s === 'failed' || s === 'timeout') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-red-50 dark:bg-red-900/40 border border-red-200 dark:border-red-700 text-red-700 dark:text-red-300">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
        {s === 'timeout' ? 'Timed out' : 'Failed'}
      </span>
    )
  }
  if (s === 'cancelled') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-500" />
        Cancelled
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-amber-50 dark:bg-amber-900/40 border border-amber-200 dark:border-amber-700 text-amber-700 dark:text-amber-300">
      <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
      In progress
    </span>
  )
}

function RelativeTime({ ts }) {
  if (!ts) return <span className="text-gray-400">—</span>
  const diff = Date.now() - new Date(ts).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return <span>just now</span>
  if (mins < 60) return <span>{mins}m ago</span>
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return <span>{hrs}h ago</span>
  return <span>{Math.floor(hrs / 24)}d ago</span>
}

function CopyButton({ text, label = 'Copy IDs' }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={handleCopy}
      className="text-xs text-[#185FA5] hover:underline flex items-center gap-1"
    >
      {copied ? '✓ Copied!' : label}
    </button>
  )
}

function RequestCard({ request, defaultExpanded }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [showCancel, setShowCancel] = useState(false)
  const cancelRequest = useCancelRequest()
  const { show } = useToast()

  const id = request.request_id
  // While expanded, get a fresh single-record view (uses GET /requests/{id}/status)
  const { data: liveDetail } = useRequest(expanded ? id : null)

  // SSE stream while expanded and the request is non-terminal
  const [streamed, setStreamed] = useState(null)
  const status = (liveDetail?.requests?.[0] || streamed || request)?.status || request.status
  const terminal = ['completed', 'complete', 'failed', 'cancelled', 'timeout', 'partial'].includes(
    (status || '').toLowerCase()
  )

  useEffect(() => {
    if (!expanded || terminal) return
    let es
    try {
      es = streamRequest(id, { interval: 3, timeout: 600 })
      es.onmessage = (e) => {
        if (!e.data || e.data.trim() === '{}') return
        try {
          const payload = JSON.parse(e.data)
          const first = payload?.requests?.[0]
          if (first) setStreamed(first)
        } catch {
          // ignore malformed frames
        }
      }
      es.onerror = () => {
        es.close()
      }
    } catch {
      // EventSource not supported / blocked — fall back to React Query polling already in place.
    }
    return () => {
      try { es?.close() } catch {}
    }
  }, [expanded, id, terminal])

  // Effective request: live SSE > query > prop. SSE frames sometimes omit the
  // machines list — fall back to whichever source has it.
  const effective = streamed || liveDetail?.requests?.[0] || request
  const machinesSource =
    (Array.isArray(effective.machines) && effective.machines.length > 0)
      ? effective
      : (Array.isArray(liveDetail?.requests?.[0]?.machines) && liveDetail.requests[0].machines.length > 0)
        ? liveDetail.requests[0]
        : request

  const templateId = effective.template_id
  const requestedCount = effective.requested_count ?? 0
  const machines = machinesSource.machines || []
  const ts = effective.created_at || request.created_at
  const progress = requestedCount > 0 ? Math.round((machines.length / requestedCount) * 100) : 0
  const canCancel = effective.status === 'in_progress' || effective.status === 'pending'

  // Derive per-machine status, taking the parent request status into account.
  // If the request is complete/launched but a machine row is missing a status,
  // it's misleading to show "pending" — the request couldn't have completed
  // otherwise. Same idea for failed parents.
  const deriveMachineStatus = (m) => {
    const explicit = m.status
    if (explicit) return explicit
    const result = (m.result || '').toLowerCase()
    if (result === 'succeed' || result === 'success') return 'running'
    if (result === 'fail' || result === 'failed' || result === 'error') return 'failed'
    const parent = (effective.status || '').toLowerCase()
    if (parent === 'complete' || parent === 'completed' || parent === 'launched') return 'running'
    if (parent === 'failed' || parent === 'timeout') return 'failed'
    if (parent === 'cancelled') return 'cancelled'
    return 'unknown'
  }

  const handleCancel = async () => {
    try {
      await cancelRequest.mutateAsync(id)
      show({ type: 'success', message: 'Request cancelled' })
      setShowCancel(false)
    } catch (err) {
      show({ type: 'error', message: err.message || 'Cancel failed' })
    }
  }

  const machineIds = machines.map((m) => m.machine_id).filter(Boolean)

  return (
    <>
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
        {/* Header row */}
        <div
          className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
          onClick={() => setExpanded((v) => !v)}
        >
          <Badge status={effective.status} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="mono text-xs text-gray-500 dark:text-gray-400 truncate">{id}</span>
              {templateId && (
                <span className="text-xs text-gray-500 dark:text-gray-400 hidden sm:inline">· {templateId}</span>
              )}
            </div>
            <div className="mt-1.5 flex items-center gap-4">
              <div className="flex-1 max-w-[200px]">
                <div className="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      effective.status === 'failed' ? 'bg-red-400' :
                      (effective.status === 'completed' || effective.status === 'complete') ? 'bg-green-500' : 'bg-amber-400'
                    }`}
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {machines.length} of {requestedCount}
              </span>
            </div>
          </div>
          <span className="text-xs text-gray-400 dark:text-gray-500 hidden md:block whitespace-nowrap">
            <RelativeTime ts={ts} />
          </span>
          <span className="text-gray-400 dark:text-gray-500 text-sm">{expanded ? '▲' : '▼'}</span>
        </div>

        {/* Expanded content */}
        {expanded && (
          <div className="border-t border-gray-100 dark:border-gray-700 px-5 py-4 space-y-4">
            <div className="flex items-center justify-between">
              <StatusPill status={effective.status} />
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {machines.length} of {requestedCount} machines
                {!terminal && expanded && (
                  <span className="ml-2 text-[10px] uppercase tracking-wider text-amber-600 dark:text-amber-400">
                    · live
                  </span>
                )}
              </span>
            </div>

            {effective.message && (effective.status === 'failed' || effective.status === 'timeout') && (
              <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded p-3 text-sm text-red-700 dark:text-red-300">
                <strong>Error:</strong> {effective.message}
              </div>
            )}

            {effective.message && (effective.status === 'completed' || effective.status === 'complete') && (
              <div className="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 rounded p-2">{effective.message}</div>
            )}

            {machines.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-medium text-gray-500 dark:text-gray-400">
                    Machines ({machines.length})
                  </p>
                  {machineIds.length > 0 && (
                    <CopyButton text={machineIds.join(', ')} />
                  )}
                </div>
                <div className="space-y-1">
                  {machines.map((m) => {
                    const machineStatus = deriveMachineStatus(m)
                    return (
                    <div key={m.machine_id || m.cloud_host_id} className="flex items-center gap-3 text-sm py-1.5 border-b border-gray-50 dark:border-gray-800 last:border-0">
                      <StatusDot status={machineStatus} size="sm" />
                      <span className="mono text-xs text-gray-600 dark:text-gray-400 flex-1 truncate">
                        {m.machine_id || m.cloud_host_id || '—'}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">{m.instance_type || '—'}</span>
                      {m.private_ip_address && (
                        <span className="mono text-xs text-gray-400 dark:text-gray-500">{m.private_ip_address}</span>
                      )}
                      <Badge status={machineStatus} />
                    </div>
                    )
                  })}
                </div>
              </div>
            )}

            {canCancel && (
              <div className="flex justify-end pt-1">
                <button
                  onClick={() => setShowCancel(true)}
                  className="text-xs text-red-600 dark:text-red-400 hover:underline border border-red-200 dark:border-red-800 px-3 py-1.5 rounded"
                >
                  Cancel Request
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {showCancel && (
        <ConfirmModal
          title="Cancel Request"
          description={`Cancel request ${id}? Any already-allocated machines will be returned.`}
          confirmLabel="Cancel Request"
          onConfirm={handleCancel}
          onCancel={() => setShowCancel(false)}
          loading={cancelRequest.isPending}
        />
      )}
    </>
  )
}

export default function Requests() {
  const [searchParams] = useSearchParams()
  const expandId = searchParams.get('expand')
  const [filter, setFilter] = useState('all')

  const allRequests = useRequests(filter === 'returns' ? 'all' : filter)
  const returnsOnly = useReturnRequests()

  const useReturns = filter === 'returns'
  const data = useReturns ? returnsOnly.data : allRequests.data
  const isLoading = useReturns ? returnsOnly.isLoading : allRequests.isLoading
  const error = useReturns ? returnsOnly.error : allRequests.error
  const refetch = useReturns ? returnsOnly.refetch : allRequests.refetch

  const requestList = Array.isArray(data) ? data : data?.requests || []

  const filtered = filter === 'all' || filter === 'returns'
    ? requestList
    : requestList.filter((r) => r.status === filter)

  const sorted = [...filtered].sort((a, b) => {
    const tA = a.created_at || a.createdAt || 0
    const tB = b.created_at || b.createdAt || 0
    return new Date(tB) - new Date(tA)
  })

  return (
    <div className="flex flex-col h-full">
      <Topbar title="Requests" />

      <div className="flex-1 overflow-y-auto p-6">
        {/* Filter bar */}
        <div className="flex gap-2 mb-5 flex-wrap">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors
                ${filter === f.value
                  ? 'bg-[#185FA5] text-white border-[#185FA5]'
                  : 'bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-[#185FA5] hover:text-[#185FA5]'
                }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {error ? (
          <ErrorBanner error={error} onRetry={refetch} />
        ) : isLoading ? (
          <div className="space-y-3">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-20 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse" />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <EmptyState
            icon="↻"
            title="No requests"
            description={filter !== 'all' ? `No ${filter} requests found.` : 'No machine requests have been submitted yet.'}
          />
        ) : (
          <div className="space-y-3">
            {sorted.map((req) => {
              const id = req.request_id || req.id
              return (
                <RequestCard
                  key={id}
                  request={req}
                  defaultExpanded={expandId === id}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
