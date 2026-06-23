import { useNavigate } from 'react-router-dom'
import { useRequests, useTemplates, useMachines, useMetrics } from '../hooks'
import { useState } from 'react'
import Badge from '../components/Badge'
import ErrorBanner from '../components/ErrorBanner'
import Topbar from '../components/Topbar'
import RequestModal from './RequestModal'

function MetricCard({ label, value, loading }) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg p-5">
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</p>
      {loading ? (
        <div className="h-8 w-16 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
      ) : (
        <p className="text-3xl font-semibold text-gray-900 dark:text-gray-100">{value ?? '—'}</p>
      )}
    </div>
  )
}

// Parse the Prometheus exposition text into a flat { metric_name: number } map.
// Ignores HELP/TYPE comments and labelled samples (we just want top-level totals).
function parsePrometheus(text) {
  if (!text || typeof text !== 'string') return {}
  const out = {}
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    // Skip lines with labels, e.g. orb_request_total{status="ok"} 5
    if (line.includes('{')) continue
    const parts = line.split(/\s+/)
    if (parts.length < 2) continue
    const value = Number(parts[1])
    if (!Number.isNaN(value)) out[parts[0]] = value
  }
  return out
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [showRequestModal, setShowRequestModal] = useState(false)

  const { data: machines, isLoading: machinesLoading, error: machinesError, refetch: refetchMachines } = useMachines()
  const { data: requests, isLoading: requestsLoading } = useRequests()
  const { data: templates, isLoading: templatesLoading } = useTemplates()
  const { data: metricsText } = useMetrics()
  const metrics = parsePrometheus(metricsText)
  const metricEntries = Object.entries(metrics).slice(0, 6)

  const machineList = Array.isArray(machines) ? machines : machines?.machines || []
  const requestList = Array.isArray(requests) ? requests : requests?.requests || []
  const templateList = Array.isArray(templates) ? templates : templates?.templates || []

  const runningCount = machineList.filter((m) => m.status === 'running').length
  const pendingCount = requestList.filter((r) => r.status === 'in_progress' || r.status === 'pending').length

  const yesterday = Date.now() - 86400000
  const returnedToday = machineList.filter((m) => {
    const t = m.returned_at || m.returnedAt
    return t && new Date(t).getTime() > yesterday
  }).length

  const recentRequests = [...requestList]
    .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
    .slice(0, 10)

  if (machinesError && !machinesLoading) {
    return (
      <div className="p-6">
        <ErrorBanner error={machinesError} onRetry={refetchMachines} />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
    <Topbar
      title="Dashboard"
      actions={
        <button
          onClick={() => setShowRequestModal(true)}
          className="px-4 py-2 text-sm bg-[#185FA5] text-white rounded-md hover:bg-[#14508a]"
        >
          + Request Machines
        </button>
      }
    />
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Metric cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <MetricCard label="Machines Running" value={runningCount} loading={machinesLoading} />
        <MetricCard label="Pending Requests" value={pendingCount} loading={requestsLoading} />
        <MetricCard label="Returned Today" value={returnedToday} loading={machinesLoading} />
        <MetricCard label="Templates Available" value={templateList.length} loading={templatesLoading} />
      </div>

      {/* Server metrics (Prometheus) */}
      {metricEntries.length > 0 && (
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg">
          <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Server metrics</h2>
            <span className="text-[10px] uppercase tracking-wider text-gray-400 dark:text-gray-500">live</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 p-5">
            {metricEntries.map(([name, value]) => (
              <div key={name}>
                <p className="text-[10px] mono text-gray-400 dark:text-gray-500 truncate" title={name}>{name}</p>
                <p className="text-lg font-semibold text-gray-800 dark:text-gray-200">
                  {Number.isInteger(value) ? value : value.toFixed(2)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6">
        {/* Recent requests */}
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg">
          <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Recent Requests</h2>
            <button
              onClick={() => navigate('/requests')}
              className="text-xs text-[#185FA5] hover:underline"
            >
              View all →
            </button>
          </div>
          {recentRequests.length === 0 ? (
            <div className="py-12 text-center text-sm text-gray-400 dark:text-gray-500">No requests yet</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400">
                  <th className="text-left px-5 py-3 font-medium">Status</th>
                  <th className="text-left px-4 py-3 font-medium">Request ID</th>
                  <th className="text-left px-4 py-3 font-medium hidden md:table-cell">Template</th>
                  <th className="text-left px-4 py-3 font-medium hidden lg:table-cell">Count</th>
                  <th className="text-left px-4 py-3 font-medium hidden lg:table-cell">Time</th>
                </tr>
              </thead>
              <tbody>
                {recentRequests.map((req) => {
                  const id = req.request_id
                  return (
                    <tr
                      key={id}
                      onClick={() => navigate(`/requests?expand=${id}`)}
                      className="border-b border-gray-50 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                    >
                      <td className="px-5 py-3"><Badge status={req.status} /></td>
                      <td className="px-4 py-3 mono text-xs text-gray-600 dark:text-gray-400 truncate max-w-[140px]">{id}</td>
                      <td className="px-4 py-3 text-gray-700 dark:text-gray-300 hidden md:table-cell">
                        {req.template_id || '—'}
                      </td>
                      <td className="px-4 py-3 text-gray-700 dark:text-gray-300 hidden lg:table-cell">
                        {(req.machines?.length ?? 0)} / {req.requested_count ?? '?'}
                      </td>
                      <td className="px-4 py-3 text-gray-400 dark:text-gray-500 hidden lg:table-cell text-xs">
                        {req.created_at ? new Date(req.created_at).toLocaleString() : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {showRequestModal && (
        <RequestModal onClose={() => setShowRequestModal(false)} />
      )}
    </div>
    </div>
  )
}
