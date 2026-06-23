import { useState, useMemo, useEffect, useRef, Fragment } from 'react'
import { useMachines, useMachine, useTemplates, useReturnMachines } from '../hooks'
import { useToast } from '../components/Toast'
import { confirmShutdown } from '../api/aws'
import Badge from '../components/Badge'
import EmptyState from '../components/EmptyState'
import ErrorBanner from '../components/ErrorBanner'
import ConfirmModal from '../components/ConfirmModal'
import Topbar from '../components/Topbar'

// Robustly turn the API's launch_time into a JS Date.
// The backend returns:
//  - integer seconds since epoch (e.g. 1781102430)
//  - or an ISO string
//  - or sometimes integer milliseconds, depending on the timestamp_format
function parseLaunchTime(value) {
  if (value == null) return null
  if (typeof value === 'string') {
    const t = new Date(value)
    return isNaN(t.getTime()) ? null : t
  }
  if (typeof value === 'number' && isFinite(value)) {
    // Distinguish seconds vs milliseconds by magnitude. Anything below
    // 10^12 is treated as seconds (year 33658+ in ms territory otherwise).
    const ms = value < 1e12 ? value * 1000 : value
    const t = new Date(ms)
    return isNaN(t.getTime()) ? null : t
  }
  return null
}

function formatUptime(ms) {
  if (ms < 0) ms = 0
  const totalSec = Math.floor(ms / 1000)
  const days = Math.floor(totalSec / 86400)
  const hrs = Math.floor((totalSec % 86400) / 3600)
  const mins = Math.floor((totalSec % 3600) / 60)
  const secs = totalSec % 60

  if (days > 0) return `${days}d ${hrs}h`
  if (hrs > 0) return `${hrs}h ${mins}m`
  if (mins > 0) return `${mins}m ${secs}s`
  return `${secs}s`
}

function Uptime({ launchTime, status, terminationTime }) {
  const launch = useMemo(() => parseLaunchTime(launchTime), [launchTime])
  const term = useMemo(() => parseLaunchTime(terminationTime), [terminationTime])
  const lower = (status || '').toLowerCase()
  const frozen = ['terminated', 'stopped', 'shutting-down', 'stopping'].includes(lower)

  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (frozen || !launch) return
    const intervalSec = (Date.now() - launch.getTime()) > 3600_000 ? 60 : 1
    const i = setInterval(() => setNow(Date.now()), intervalSec * 1000)
    return () => clearInterval(i)
  }, [frozen, launch])

  if (!launch) return <span className="text-gray-400">—</span>

  // For terminal states, prefer terminationTime if available, otherwise stop counting.
  const endpoint = frozen ? (term ? term.getTime() : now) : now
  const ms = endpoint - launch.getTime()
  if (ms < 0) return <span className="text-gray-400">—</span>

  return (
    <span title={launch.toLocaleString()} className={frozen ? 'text-gray-400' : ''}>
      {formatUptime(ms)}
    </span>
  )
}

function CopyButton({ text }) {
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
      className="px-3 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
    >
      {copied ? '✓ Copied!' : 'Copy IDs'}
    </button>
  )
}

function MachineDetailRow({ id }) {
  const { data: detail, isLoading } = useMachine(id)
  if (isLoading) {
    return <div className="text-xs text-gray-400">Loading details…</div>
  }
  const m = detail?.machine || detail
  if (!m) {
    return <div className="text-xs text-gray-400">No details available.</div>
  }
  const fields = [
    ['Machine ID', m.machine_id],
    ['Instance Type', m.instance_type],
    ['Status', m.status],
    ['Provider API', m.provider_api],
    ['Provider Type', m.provider_type],
    ['Resource ID', m.resource_id],
    ['Request ID', m.request_id],
    ['Template ID', m.template_id],
    ['Image ID', m.image_id],
    ['Public IP', m.public_ip],
    ['Private IP', m.private_ip],
    ['Public DNS', m.public_dns_name],
    ['Private DNS', m.private_dns_name],
    ['Subnet ID', m.subnet_id],
    ['VPC ID', m.vpc_id],
    ['Price Type', m.price_type],
    ['Launch Time', m.launch_time ? new Date(typeof m.launch_time === 'number' ? m.launch_time * 1000 : m.launch_time).toLocaleString() : null],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '')
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-1.5">
      {fields.map(([label, value]) => (
        <div key={label} className="text-xs">
          <span className="text-gray-400 dark:text-gray-500 w-32 inline-block">{label}</span>
          <span className="mono text-gray-700 dark:text-gray-300 break-all">{String(value)}</span>
        </div>
      ))}
      {m.tags && Object.keys(m.tags).length > 0 && (
        <div className="col-span-full text-xs mt-2">
          <p className="text-gray-400 dark:text-gray-500 mb-1">Tags</p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(m.tags).map(([k, v]) => (
              <span
                key={k}
                className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded px-2 py-0.5 mono text-[11px] text-gray-600 dark:text-gray-300"
              >
                {k}={String(v)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Machines() {
  const { data, isLoading, error, refetch } = useMachines()
  const { data: templates } = useTemplates()
  const returnMachines = useReturnMachines()
  const { show } = useToast()

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [templateFilter, setTemplateFilter] = useState('all')
  const [selected, setSelected] = useState(new Set())
  const [confirmReturn, setConfirmReturn] = useState(null)
  const [expanded, setExpanded] = useState(null)

  // Map of machine_id -> shutdownAt ISO string when confirmed via /api/v1/aws/confirm-shutdown
  const [shutdownStatus, setShutdownStatus] = useState({})
  // Track which IDs are currently being verified so we don't double-call
  const inflightRef = useRef(new Set())

  const machineList = Array.isArray(data) ? data : data?.machines || []
  const templateList = Array.isArray(templates) ? templates : templates?.templates || []

  // Find any machine in a transitional or terminal state that isn't yet confirmed
  // and trigger confirm-shutdown. We re-run whenever the list changes.
  useEffect(() => {
    const candidates = machineList
      .filter((m) => {
        const id = m.machine_id
        if (!id || !id.startsWith('i-')) return false
        if (shutdownStatus[id]) return false // already confirmed
        if (inflightRef.current.has(id)) return false // request in flight
        const s = (m.status || '').toLowerCase()
        return s === 'shutting-down' || s === 'terminated' || s === 'stopped'
      })
      .map((m) => m.machine_id)

    if (candidates.length === 0) return

    candidates.forEach((id) => inflightRef.current.add(id))

    let cancelled = false
    confirmShutdown(candidates)
      .then((res) => {
        if (cancelled) return
        const updates = {}
        for (const r of res?.results || []) {
          if (r.tagged && r.shutdownAt) {
            updates[r.machineId] = r.shutdownAt
          }
        }
        if (Object.keys(updates).length) {
          setShutdownStatus((prev) => ({ ...prev, ...updates }))
        }
      })
      .catch(() => {
        // Silent: this is a best-effort background task. The toast on user
        // action is enough; we don't want to spam errors during polling.
      })
      .finally(() => {
        candidates.forEach((id) => inflightRef.current.delete(id))
      })

    return () => {
      cancelled = true
    }
  }, [machineList, shutdownStatus])

  const filtered = useMemo(() => {
    return machineList.filter((m) => {
      const id = m.machine_id || ''
      const instanceType = m.instance_type || ''
      if (search && !id.toLowerCase().includes(search.toLowerCase()) &&
          !instanceType.toLowerCase().includes(search.toLowerCase())) return false
      if (statusFilter !== 'all' && m.status !== statusFilter) return false
      if (templateFilter !== 'all' && m.template_id !== templateFilter) return false
      return true
    })
  }, [machineList, search, statusFilter, templateFilter])

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (selected.size === filtered.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(filtered.map((m) => m.machine_id).filter(Boolean)))
    }
  }

  const handleReturnSelected = async () => {
    const ids = Array.from(selected)
    try {
      await returnMachines.mutateAsync(ids)
      show({ type: 'success', message: `Returned ${ids.length} machine(s)` })
      setSelected(new Set())
      setConfirmReturn(null)
    } catch (err) {
      show({ type: 'error', message: err.message || 'Return failed' })
    }
  }

  const handleReturn = async (id) => {
    try {
      await returnMachines.mutateAsync([id])
      show({ type: 'success', message: `Machine returned` })
      setConfirmReturn(null)
    } catch (err) {
      show({ type: 'error', message: err.message || 'Return failed' })
    }
  }

  const selectedIds = Array.from(selected)

  return (
    <div className="flex flex-col h-full">
      <Topbar title="Machines" />

      <div className="flex-1 overflow-y-auto p-6">
        {/* Toolbar */}
        <div className="flex flex-wrap gap-3 mb-5 items-center">
          <input
            type="text"
            placeholder="Search by ID or instance type…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#185FA5] w-64"
          />

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]"
          >
            <option value="all">All Statuses</option>
            <option value="running">Running</option>
            <option value="stopped">Stopped</option>
            <option value="pending">Pending</option>
          </select>

          <select
            value={templateFilter}
            onChange={(e) => setTemplateFilter(e.target.value)}
            className="border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 text-sm bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-[#185FA5]"
          >
            <option value="all">All Templates</option>
            {templateList.map((t) => (
              <option key={t.template_id} value={t.template_id}>{t.template_id}</option>
            ))}
          </select>

          <button
            onClick={refetch}
            className="ml-auto px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            ↻ Refresh
          </button>
        </div>

        {/* Selection bar */}
        {selected.size > 0 && (
          <div className="flex items-center gap-4 mb-4 px-4 py-3 bg-[#185FA5] text-white rounded-lg text-sm">
            <span className="font-medium">{selected.size} machine{selected.size !== 1 ? 's' : ''} selected</span>
            <CopyButton text={selectedIds.join(', ')} />
            <button
              onClick={() => setConfirmReturn({ type: 'bulk', ids: selectedIds })}
              className="px-3 py-1.5 bg-white text-[#185FA5] rounded text-xs font-medium hover:bg-gray-100"
            >
              Return Selected
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="ml-auto opacity-70 hover:opacity-100"
            >
              ✕ Clear
            </button>
          </div>
        )}

        {error ? (
          <ErrorBanner error={error} onRetry={refetch} />
        ) : isLoading ? (
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-12 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon="⬡"
            title="No machines found"
            description={search || statusFilter !== 'all' ? 'Try adjusting your filters.' : 'No machines have been allocated yet.'}
          />
        ) : (
          <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
            <table className="w-full text-sm table-fixed">
              <thead>
                <tr className="border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-xs text-gray-500 dark:text-gray-400">
                  <th className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.size === filtered.length && filtered.length > 0}
                      onChange={toggleAll}
                      className="rounded"
                    />
                  </th>
                  <th className="text-left px-4 py-3 font-medium w-48">Machine ID</th>
                  <th className="text-left px-4 py-3 font-medium w-36 hidden md:table-cell">Instance Type</th>
                  <th className="text-left px-4 py-3 font-medium w-28">Status</th>
                  <th className="text-left px-4 py-3 font-medium w-24 hidden lg:table-cell">Uptime</th>
                  <th className="text-left px-4 py-3 font-medium hidden lg:table-cell">Template</th>
                  <th className="text-right px-4 py-3 font-medium w-40">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => {
                  const id = m.machine_id
                  const isSelected = selected.has(id)
                  const isExpanded = expanded === id
                  return (
                    <Fragment key={id}>
                    <tr
                      className={`border-b border-gray-50 dark:border-gray-800 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors
                        ${isSelected ? 'bg-blue-50 dark:bg-blue-950/50' : ''}`}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(id)}
                          className="rounded"
                        />
                      </td>
                      <td
                        className="px-4 py-3 cursor-pointer"
                        onClick={() => setExpanded(isExpanded ? null : id)}
                      >
                        <span className="mono text-xs text-gray-600 dark:text-gray-400 block truncate" title={id}>
                          {isExpanded ? '▾ ' : '▸ '}
                          {id}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-600 dark:text-gray-400 hidden md:table-cell">
                        {m.instance_type || '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2 flex-wrap">
                          {!shutdownStatus[id] && (
                            <Badge status={m.status || 'unknown'} />
                          )}
                          {shutdownStatus[id] && (
                            <span
                              className="text-[10px] uppercase tracking-wider font-semibold text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 px-1.5 py-0.5 rounded"
                              title={`Shutdown tag applied at ${shutdownStatus[id]}`}
                            >
                              ✓ shutdown
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400 hidden lg:table-cell text-xs">
                        <Uptime launchTime={m.launch_time} status={m.status} terminationTime={m.termination_time} />
                      </td>
                      <td className="px-4 py-3 text-gray-500 dark:text-gray-400 hidden lg:table-cell text-xs truncate">
                        {m.template_id || '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2 justify-end">
                          {(m.status || '').toLowerCase() === 'running' && !shutdownStatus[id] && (
                            <button
                              onClick={() => setConfirmReturn({ type: 'single', id })}
                              className="text-xs px-2.5 py-1 border border-red-200 dark:border-red-800 rounded text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30"
                            >
                              Return
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="bg-gray-50 dark:bg-gray-800">
                        <td colSpan={7} className="px-4 py-3">
                          <MachineDetailRow id={id} />
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {confirmReturn?.type === 'single' && (
        <ConfirmModal
          title="Return Machine"
          description="This will return the machine to the pool. This action cannot be undone."
          itemList={[confirmReturn.id]}
          confirmLabel="Return Machine"
          onConfirm={() => handleReturn(confirmReturn.id)}
          onCancel={() => setConfirmReturn(null)}
          loading={returnMachines.isPending}
        />
      )}
      {confirmReturn?.type === 'bulk' && (
        <ConfirmModal
          title={`Return ${confirmReturn.ids.length} Machines`}
          description="These machines will be returned to the pool. This action cannot be undone."
          itemList={confirmReturn.ids}
          confirmLabel={`Return ${confirmReturn.ids.length} Machines`}
          onConfirm={handleReturnSelected}
          onCancel={() => setConfirmReturn(null)}
          loading={returnMachines.isPending}
        />
      )}
    </div>
  )
}
